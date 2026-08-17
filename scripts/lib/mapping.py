"""Apply config rules to HubSpot properties, producing Salesforce proposals.

Everything here is driven by config/*.yml. When a client review says a mapping
is wrong, the fix belongs in the YAML — not in a workbook cell and not in this
file. That is what makes the second client a third of the effort.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config"


def load_config() -> dict:
    return {
        "objects": yaml.safe_load((CONFIG / "object_map.yml").read_text(encoding="utf-8")),
        "types": yaml.safe_load((CONFIG / "type_rules.yml").read_text(encoding="utf-8")),
        "naming": yaml.safe_load((CONFIG / "naming.yml").read_text(encoding="utf-8")),
    }


@dataclass
class Proposal:
    """One proposed Salesforce field, with the reasons it needs human eyes."""
    hs_name: str
    hs_label: str
    hs_type: str
    hs_field_type: str
    api_name: str = ""
    sf_type: str = "Text"
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    external_id: bool = False
    unique: bool = False
    reference_to: str | None = None
    picklist_values: list[dict] = field(default_factory=list)
    visible_lines: int | None = None
    default_value: object = None
    maps_to_standard: str | None = None
    deploy_default: str = "HOLD"
    flags: list[str] = field(default_factory=list)
    fill_percent: float | None = None
    sample: str = ""

    @property
    def is_standard(self) -> bool:
        return self.maps_to_standard is not None


# ---------------------------------------------------------------------------
# API naming
# ---------------------------------------------------------------------------

def to_api_name(hs_name: str, naming: dict, suffix: bool = True) -> tuple[str, list[str]]:
    """HubSpot property name → Salesforce API name. Returns (name, flags)."""
    flags: list[str] = []
    transform = naming["transform"]

    # Split into words, then rejoin Title_Case_With_Underscores.
    # Underscore is a word BOUNDARY here, not a character to keep: HubSpot's
    # service_orders must become Service_Orders, not Service_orders. The
    # configured strip_pattern preserves underscores because it also governs
    # which characters survive; word splitting is a separate concern.
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", hs_name) if p]
    if not parts:
        return "X__c" if suffix else "X", ["Empty name after transformation."]

    name = "_".join(p[0].upper() + p[1:] if p else p for p in parts)

    if transform.get("collapse_double_underscore"):
        name = re.sub(r"_{2,}", "_", name)
    if transform.get("trim_leading_underscore"):
        name = name.lstrip("_")
    if transform.get("trim_trailing_underscore"):
        name = name.rstrip("_")
    if name and not name[0].isalpha():
        name = transform.get("leading_digit_prefix", "X") + name

    prefix_cfg = naming.get("prefix", {})
    if prefix_cfg.get("enabled"):
        name = prefix_cfg.get("value", "") + name

    suffix_str = transform.get("custom_suffix", "__c") if suffix else ""
    max_len = naming["limits"]["field_api_name_max"]

    if len(name) + len(suffix_str) > max_len:
        keep = max_len - len(suffix_str)
        name = name[:keep].rstrip("_")
        # Truncation is never silent: two long names frequently collapse to one.
        flags.append(
            f"API name truncated to fit {max_len} chars — set Final_API to a "
            "deliberate short name, and check it does not collide."
        )

    full = name + suffix_str

    reserved = {w.lower() for w in naming.get("reserved_words", [])}
    if name.lower() in reserved:
        flags.append(f"{name!r} is a reserved word — Final_API must differ.")

    return full, flags


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

def _match_name_override(hs_name: str, overrides: list[dict]) -> dict | None:
    """Exact matches win over glob patterns, so `email` beats `*_email`."""
    lowered = hs_name.lower()
    for rule in overrides:
        if str(rule.get("match", "")).lower() == lowered:
            return rule
    for rule in overrides:
        pattern = str(rule.get("match", "")).lower()
        if "*" in pattern and fnmatch.fnmatch(lowered, pattern):
            return rule
    return None


def _match_pair(hs_type: str, hs_field_type: str, pair_rules: list[dict]) -> dict | None:
    for rule in pair_rules:
        m = rule.get("match", {})
        if m.get("type") == hs_type and m.get("fieldType") == hs_field_type:
            return rule
    return None


def size_text(observed_max: int, types_cfg: dict) -> tuple[str, int]:
    """Choose a Text length from observed data instead of defaulting to 255."""
    sizing = types_cfg.get("length_sizing", {})
    if not sizing.get("enabled"):
        return "Text", 255

    target = int(observed_max * sizing.get("headroom_multiplier", 1.5)) or 1
    for bucket in sizing.get("buckets", [255]):
        if target <= bucket:
            return "Text", bucket
    return sizing.get("promote_above_255_to", "LongTextArea"), 32768


def map_property(prop: dict, hs_object: str, config: dict,
                 fill_percent: float | None = None,
                 observed_max_len: int = 0,
                 sample: str = "") -> Proposal:
    """One HubSpot property → one Proposal. The core of the whole tool."""
    types_cfg = config["types"]
    naming = config["naming"]

    hs_name = prop.get("name", "")
    hs_type = prop.get("type", "string")
    hs_field_type = prop.get("fieldType", "text")

    p = Proposal(
        hs_name=hs_name,
        hs_label=prop.get("label", hs_name),
        hs_type=hs_type,
        hs_field_type=hs_field_type,
        fill_percent=fill_percent,
        sample=sample,
    )

    # 1. Standard field mapping wins outright — it prevents FirstName__c
    #    being created alongside the real FirstName.
    standard_map = (naming.get("standard_field_map", {}) or {}).get(hs_object, {})
    if hs_name in standard_map:
        p.maps_to_standard = standard_map[hs_name]
        p.api_name = standard_map[hs_name]
        p.sf_type = "Standard"
        p.deploy_default = "HOLD"
        p.flags.append(f"Maps to standard field {p.api_name} — no custom field created.")
        return p

    # 2. Pair rule on (type, fieldType).
    rule = _match_pair(hs_type, hs_field_type, types_cfg.get("pair_rules", []))
    if rule:
        _apply_rule(p, rule)
    else:
        fallback = (types_cfg.get("type_rules", {}) or {}).get(hs_type)
        if fallback:
            _apply_rule(p, fallback)
        else:
            p.flags.append(
                f"No rule for type={hs_type!r} fieldType={hs_field_type!r}. "
                "Add a rule to config/type_rules.yml rather than fixing this cell."
            )
            p.deploy_default = "HOLD"

    # 3. Name overrides win over type rules.
    override = _match_name_override(hs_name, types_cfg.get("name_overrides", []))
    if override:
        _apply_rule(p, override)

    # 4. API name.
    if not p.api_name:
        p.api_name, name_flags = to_api_name(hs_name, naming)
        p.flags.extend(name_flags)

    # 5. Picklist values from the HubSpot options array.
    if p.sf_type in ("Picklist", "MultiselectPicklist"):
        p.picklist_values = _picklist_values(prop, naming, p)

    # 6. Data-driven sizing.
    if p.sf_type == "Text" and observed_max_len:
        sf_type, length = size_text(observed_max_len, types_cfg)
        p.sf_type, p.length = sf_type, length
        if sf_type == "LongTextArea":
            p.flags.append(
                f"Observed values up to {observed_max_len} chars — promoted to LongTextArea."
            )

    # 7. Review triggers.
    _apply_triggers(p, prop, types_cfg, naming)
    return p


def _apply_rule(p: Proposal, rule: dict):
    if "sf_type" in rule and rule["sf_type"] is not None:
        p.sf_type = rule["sf_type"]
    # An explicit api_name overrides the derived one, so a rule can pin a field
    # the rest of the pipeline depends on by name.
    if rule.get("api_name"):
        p.api_name = rule["api_name"]
    for key, attr in (("length", "length"), ("precision", "precision"),
                      ("scale", "scale"), ("visible_lines", "visible_lines"),
                      ("references", "reference_to"), ("default_value", "default_value")):
        if key in rule:
            setattr(p, attr, rule[key])
    if rule.get("external_id"):
        p.external_id = True
    if rule.get("unique"):
        p.unique = True
    if rule.get("map_to_standard"):
        p.maps_to_standard = rule["map_to_standard"]
        p.api_name = rule["map_to_standard"].split(".")[-1]
        # The standard field already exists in the org with its own type, so we
        # create nothing. Marking it Standard keeps this row exempt from the
        # custom-field rules in 03 (the __c suffix, the 40-char limit) — which
        # would otherwise reject a perfectly correct mapping.
        p.sf_type = "Standard"
    if rule.get("deploy_default"):
        p.deploy_default = rule["deploy_default"]
    if rule.get("flag"):
        p.flags.append(rule["flag"])


def _picklist_values(prop: dict, naming: dict, p: Proposal) -> list[dict]:
    """HubSpot's internal value and its label differ. We use the internal value
    as the Salesforce API name and the label as the display label, consistently
    — mixing the two across fields is a classic source of load failures."""
    limit = naming["limits"]["picklist_value_max"]
    values, seen = [], set()

    for option in prop.get("options", []) or []:
        raw = str(option.get("value", ""))
        label = str(option.get("label", raw))
        if len(raw) > limit:
            p.flags.append(f"Picklist value {raw[:30]!r}… exceeds {limit} chars — deploy will fail.")
            raw = raw[:limit]
        if raw in seen:
            p.flags.append(f"Duplicate picklist value {raw!r} — removed.")
            continue
        seen.add(raw)
        values.append({"fullName": raw, "label": label,
                       "sort": option.get("displayOrder", 0)})

    return values


def _apply_triggers(p: Proposal, prop: dict, types_cfg: dict, naming: dict):
    for trigger in types_cfg.get("review_triggers", []):
        condition = trigger.get("condition")
        threshold = trigger.get("threshold")
        reason = trigger.get("reason", condition)

        if condition == "calculated_property" and prop.get("calculated"):
            p.flags.append(f"CALCULATED: {reason}")
            if prop.get("calculationFormula"):
                p.flags.append(f"HubSpot formula: {prop['calculationFormula']}")
            p.deploy_default = "HOLD"

        elif condition == "picklist_options_over" and len(prop.get("options") or []) > threshold:
            p.flags.append(f"{len(prop['options'])} picklist options: {reason}")

        elif condition == "fill_percent_under" and p.fill_percent is not None:
            if p.fill_percent < threshold:
                p.flags.append(f"Only {p.fill_percent:.1f}% filled: {reason}")

        elif condition == "name_over_40_chars" and len(p.api_name) > naming["limits"]["field_api_name_max"]:
            p.flags.append(f"API name is {len(p.api_name)} chars: {reason}")

        elif condition == "unknown_hubspot_type" and p.sf_type is None:
            p.flags.append(reason)


def detect_collisions(proposals: list[Proposal]) -> dict[str, list[str]]:
    """Two HubSpot properties collapsing to one SF API name. Flags both sides —
    naming only one of them leaves the reviewer guessing."""
    by_name: dict[str, list[str]] = {}
    for p in proposals:
        if p.is_standard:
            continue
        by_name.setdefault(p.api_name.lower(), []).append(p.hs_name)

    collisions = {name: sources for name, sources in by_name.items() if len(sources) > 1}
    for p in proposals:
        sources = collisions.get(p.api_name.lower())
        if sources:
            others = [s for s in sources if s != p.hs_name]
            p.flags.append(
                f"API NAME COLLISION with {', '.join(others)} — both become "
                f"{p.api_name}. Give each a distinct Final_API."
            )
    return collisions


def compute_fill_rates(records: list[dict], property_names: list[str]) -> dict[str, float]:
    if not records:
        return {}
    total = len(records)
    counts = dict.fromkeys(property_names, 0)
    for record in records:
        for name, value in (record.get("properties") or {}).items():
            if name in counts and value not in (None, ""):
                counts[name] += 1
    return {name: (count / total) * 100 for name, count in counts.items()}


def observed_max_lengths(records: list[dict]) -> dict[str, int]:
    lengths: dict[str, int] = {}
    for record in records:
        for name, value in (record.get("properties") or {}).items():
            if value:
                lengths[name] = max(lengths.get(name, 0), len(str(value)))
    return lengths


def first_sample(records: list[dict], property_name: str) -> str:
    for record in records:
        value = (record.get("properties") or {}).get(property_name)
        if value:
            text = str(value)
            return text[:47] + "…" if len(text) > 50 else text
    return ""
