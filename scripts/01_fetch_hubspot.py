#!/usr/bin/env python3
"""01 — Fetch the complete HubSpot schema to raw/.

Read-only. Touches no Salesforce org, writes nothing outside raw/.

    python scripts/01_fetch_hubspot.py
    python scripts/01_fetch_hubspot.py --fixture      # no token needed
    python scripts/01_fetch_hubspot.py --no-samples   # skip fill-rate sampling

--fixture reads canned JSON from fixtures/ instead of calling the API, so
scripts 02-06 are buildable and testable before portal access exists. Nothing
downstream can tell the difference.

CHECKPOINT: open raw/schemas.json and confirm every custom object the client
mentioned is present. A missing object is almost always a Private App SCOPE
problem, not a code problem — check scopes before debugging code.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
FIXTURES = ROOT / "fixtures"
CONFIG = ROOT / "config"

# Object types worth probing for pipelines even if the schema does not say so.
PIPELINE_CANDIDATES = ("deals", "tickets")

# Association pairs we always ask about. Custom objects are added dynamically.
CORE_OBJECTS = ("contacts", "companies", "deals", "tickets")


def log(msg: str):
    print(msg, flush=True)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"  wrote {path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Fixture mode
# ---------------------------------------------------------------------------

def run_fixtures() -> int:
    if not FIXTURES.exists() or not any(FIXTURES.glob("*.json")):
        log(f"ERROR: no fixtures found in {FIXTURES.relative_to(ROOT)}/")
        log("Generate them with: python scripts/make_fixtures.py")
        return 1

    RAW.mkdir(exist_ok=True)
    count = 0
    for src in sorted(FIXTURES.glob("*.json")):
        shutil.copy2(src, RAW / src.name)
        log(f"  {src.name}")
        count += 1

    log(f"\nFIXTURE MODE — copied {count} files to raw/.")
    log("This is synthetic schema. Real portal data will differ in shape and volume.")
    summarise()
    return 0


# ---------------------------------------------------------------------------
# Live fetch
# ---------------------------------------------------------------------------

def run_live(fetch_samples: bool) -> int:
    from dotenv import load_dotenv
    from lib.hubspot_client import HubSpotClient, HubSpotError, ScopeError

    load_dotenv(ROOT / ".env")
    RAW.mkdir(exist_ok=True)

    try:
        client = HubSpotClient()
    except HubSpotError as exc:
        log(f"ERROR: {exc}")
        return 1

    scope_problems: list[str] = []

    # -- schemas ------------------------------------------------------------
    log("Fetching object schemas…")
    try:
        schemas = client.schemas()
    except ScopeError as exc:
        log(f"FATAL: {exc}")
        log("Cannot continue without the schemas scope.")
        return 1
    write_json(RAW / "schemas.json", schemas)

    custom = [s for s in schemas if s.get("objectTypeId", "").startswith("2-")]
    standard = [s for s in schemas if not s.get("objectTypeId", "").startswith("2-")]
    log(f"  {len(schemas)} object types — {len(standard)} standard, {len(custom)} custom")

    object_names = [s.get("name") for s in schemas if s.get("name")]
    for name in CORE_OBJECTS:
        if name not in object_names:
            object_names.append(name)

    # -- properties ---------------------------------------------------------
    log("\nFetching properties…")
    property_index = {}
    for name in object_names:
        try:
            props = client.properties(name)
        except ScopeError as exc:
            scope_problems.append(f"{name}: {exc}")
            log(f"  {name}: SCOPE DENIED — schema will be incomplete")
            continue
        except HubSpotError as exc:
            log(f"  {name}: skipped ({exc})")
            continue
        write_json(RAW / f"properties_{name}.json", props)
        property_index[name] = props

        calculated = sum(1 for p in props if p.get("calculated"))
        note = f", {calculated} calculated" if calculated else ""
        log(f"  {name}: {len(props)} properties{note}")

    # -- owners -------------------------------------------------------------
    log("\nFetching owners…")
    try:
        owners = client.owners()
        write_json(RAW / "owners.json", owners)
        inactive = sum(1 for o in owners if o.get("archived"))
        log(f"  {len(owners)} owners ({inactive} archived)")
    except HubSpotError as exc:
        scope_problems.append(f"owners: {exc}")
        log(f"  owners: FAILED — {exc}")

    # -- pipelines ----------------------------------------------------------
    log("\nFetching pipelines…")
    pipelines = {}
    candidates = set(PIPELINE_CANDIDATES) | {s.get("name") for s in custom if s.get("name")}
    for name in sorted(c for c in candidates if c):
        found = client.pipelines(name)
        if found:
            pipelines[name] = found
            stages = sum(len(p.get("stages", [])) for p in found)
            log(f"  {name}: {len(found)} pipelines, {stages} stages")
    write_json(RAW / "pipelines.json", pipelines)

    # -- associations -------------------------------------------------------
    log("\nFetching association labels…")
    associations = {}
    assoc_objects = list(CORE_OBJECTS) + [s.get("name") for s in custom if s.get("name")]
    for a in assoc_objects:
        for b in assoc_objects:
            if a == b:
                continue
            labels = client.association_labels(a, b)
            if labels:
                associations[f"{a}__to__{b}"] = labels
    write_json(RAW / "associations.json", associations)
    log(f"  {len(associations)} association pairs with labels")

    # -- sensitive data probe ----------------------------------------------
    log("\nProbing for Sensitive Data…")
    capabilities = client.probe_sensitive_data()
    capabilities["probed_at"] = datetime.now(timezone.utc).isoformat()
    capabilities["scope_problems"] = scope_problems
    write_json(RAW / "portal_capabilities.json", capabilities)
    log(f"  sensitive_data_enabled: {capabilities['sensitive_data_enabled']}")
    if capabilities.get("warning"):
        log(f"  WARNING: {capabilities['warning']}")

    # -- samples for fill rate / length sizing ------------------------------
    if fetch_samples:
        log("\nSampling records for fill rates…")
        samples = {}
        for name, props in property_index.items():
            names = [p["name"] for p in props][:100]
            if not names:
                continue
            records = client.sample_records(name, names)
            if records:
                samples[name] = records
                log(f"  {name}: {len(records)} sample records")
        write_json(RAW / "samples.json", samples)

    update_discovered(schemas, custom, pipelines, capabilities)

    if scope_problems:
        log("\n" + "=" * 70)
        log(f"{len(scope_problems)} SCOPE PROBLEM(S) — the schema is INCOMPLETE:")
        for problem in scope_problems:
            log(f"  - {problem}")
        log("Fix the Private App scopes and re-run. Do NOT build a workbook from this.")
        log("=" * 70)
        return 2

    summarise()
    return 0


def update_discovered(schemas, custom, pipelines, capabilities):
    """Rewrite the `discovered` block in object_map.yml. Human-owned keys are
    left untouched — only `discovered` is machine-managed."""
    import yaml

    path = CONFIG / "object_map.yml"
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    mapped = set(data.get("objects", {}) or {})
    excluded = {e.get("object") for e in (data.get("excluded") or [])}
    all_names = {s.get("name") for s in schemas if s.get("name")}

    data["discovered"] = {
        "last_fetch": datetime.now(timezone.utc).isoformat(),
        "portal_id": None,
        "sensitive_data_enabled": capabilities.get("sensitive_data_enabled"),
        "standard_objects": sorted(n for n in all_names
                                   if n not in {s.get("name") for s in custom}),
        "custom_objects": sorted(s.get("name") for s in custom if s.get("name")),
        "objects_with_pipelines": sorted(pipelines),
        "unmapped": sorted(all_names - mapped - excluded),
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    log(f"\n  updated config/object_map.yml discovered block")

    if data["discovered"]["unmapped"]:
        log(f"  {len(data['discovered']['unmapped'])} object(s) need a mapping decision:")
        for name in data["discovered"]["unmapped"]:
            log(f"    - {name}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarise():
    schemas_path = RAW / "schemas.json"
    if not schemas_path.exists():
        return
    schemas = json.loads(schemas_path.read_text(encoding="utf-8"))

    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)

    custom = [s for s in schemas if str(s.get("objectTypeId", "")).startswith("2-")]
    log(f"Objects: {len(schemas)} total, {len(custom)} custom")
    for s in custom:
        log(f"  custom: {s.get('name')} ({s.get('labels', {}).get('singular', '?')})")

    total_props = 0
    flagged = {"calculated": [], "big_picklist": [], "long_name": []}

    for path in sorted(RAW.glob("properties_*.json")):
        obj = path.stem.replace("properties_", "")
        props = json.loads(path.read_text(encoding="utf-8"))
        total_props += len(props)
        log(f"  {obj}: {len(props)} properties")

        for p in props:
            name = p.get("name", "")
            if p.get("calculated"):
                flagged["calculated"].append(f"{obj}.{name}")
            options = p.get("options") or []
            if len(options) > 100:
                flagged["big_picklist"].append(f"{obj}.{name} ({len(options)})")
            if len(name) + 3 > 40:
                flagged["long_name"].append(f"{obj}.{name} ({len(name) + 3})")

    log(f"\nTotal properties: {total_props}")

    log("\nJUDGEMENT CALLS NEEDED:")
    if flagged["calculated"]:
        log(f"  {len(flagged['calculated'])} calculated properties "
            "(do not translate to SF formulas):")
        for item in flagged["calculated"][:10]:
            log(f"    - {item}")
        if len(flagged["calculated"]) > 10:
            log(f"    … +{len(flagged['calculated']) - 10} more")
    if flagged["big_picklist"]:
        log(f"  {len(flagged['big_picklist'])} picklists over 100 options "
            "(consider a lookup instead):")
        for item in flagged["big_picklist"][:5]:
            log(f"    - {item}")
    if flagged["long_name"]:
        log(f"  {len(flagged['long_name'])} names exceed 40 chars once suffixed "
            "(need manual short names):")
        for item in flagged["long_name"][:5]:
            log(f"    - {item}")
    if not any(flagged.values()):
        log("  none detected")

    caps_path = RAW / "portal_capabilities.json"
    if caps_path.exists():
        caps = json.loads(caps_path.read_text(encoding="utf-8"))
        log(f"\nSensitive Data: {caps.get('sensitive_data_enabled')}")

    log("\nNEXT: verify every client-named custom object appears above,")
    log("then run: python scripts/02_build_workbook.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch HubSpot schema to raw/")
    parser.add_argument("--fixture", action="store_true",
                        help="use fixtures/ instead of the live API (no token needed)")
    parser.add_argument("--no-samples", action="store_true",
                        help="skip record sampling (faster, but no fill rates)")
    args = parser.parse_args()

    if args.fixture:
        return run_fixtures()
    return run_live(fetch_samples=not args.no_samples)


if __name__ == "__main__":
    sys.exit(main())
