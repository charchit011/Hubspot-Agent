#!/usr/bin/env python3
"""06 — Write deploy results and Setup links back into the workbook.

    python scripts/06_writeback.py --deploy-json logs/deploy_20260817T…json
    python scripts/06_writeback.py --deploy-json … --org client-sbx

Parses componentSuccesses[] / componentFailures[], matches on fullName back to
workbook rows, and writes Component_Id, Setup_Link, Deployed_At, Result, Error.

Failed rows KEEP their Y (CLAUDE.md rule 10) so the next run retries only the
failures.

Setup URL shapes are resolved objects-first: field and rule URLs need the
parent object's 01I… id, so objects are mapped before anything else.

CHECKPOINT: click three links in the workbook — one object, one field, one
validation rule. Each must land on the right Setup page.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib import workbook as wbmod

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "mapping" / "workbook.xlsx"

# Verify these against your own org before trusting them at scale. Salesforce
# has changed Setup URL shapes before, and a stale template produces a workbook
# full of dead links that all look correct.
URL = {
    "object":     "{i}/lightning/setup/ObjectManager/{oid}/Details/view",
    "field":      "{i}/lightning/setup/ObjectManager/{oid}/FieldsAndRelationships/{fid}/view",
    "recordtype": "{i}/lightning/setup/ObjectManager/{oid}/RecordTypes/{rid}/view",
    "rule":       "{i}/lightning/setup/ObjectManager/{oid}/ValidationRules/{rid}/view",
    "permset":    "{i}/lightning/setup/PermSets/page?address=%2F{pid}",
    "deploy":     "{i}/lightning/setup/DeployStatus/page?address=%2Fchangemgmt"
                  "%2FmonitorDeploymentsDetails.apexp%3FasyncId%3D{did}",
}


def log(msg=""):
    print(msg, flush=True)


def instance_url(org: str) -> str:
    try:
        result = subprocess.run(
            ["sf", "org", "display", "--target-org", org, "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        payload = json.loads(result.stdout)
        url = (payload.get("result") or {}).get("instanceUrl", "")
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    log(f"  WARNING: could not resolve the instance URL for {org!r}. "
        "Links will be relative and will not resolve.")
    return ""


def load_components(path: Path) -> tuple[list[dict], list[dict], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result", payload)
    details = result.get("details", {}) or {}

    def as_list(value):
        if isinstance(value, dict):
            return [value]
        return value or []

    return (as_list(details.get("componentSuccesses")),
            as_list(details.get("componentFailures")),
            result.get("id") or result.get("deployId") or "")


def build_object_ids(successes: list[dict]) -> dict[str, str]:
    """apiName → 01I… . Field and rule URLs cannot be built without this, so
    objects are always resolved first."""
    ids = {}
    for entry in successes:
        if entry.get("componentType") == "CustomObject":
            name = entry.get("fullName", "")
            if name and entry.get("id"):
                ids[name] = entry["id"]
    return ids


def setup_link(entry: dict, object_ids: dict, instance: str) -> str:
    """Excel HYPERLINK() for one deployed component."""
    ctype = entry.get("componentType", "")
    full = entry.get("fullName", "")
    cid = entry.get("id", "")
    if not instance:
        return ""

    if ctype == "CustomObject":
        oid = object_ids.get(full, full)  # standard objects use the API name
        url = URL["object"].format(i=instance, oid=oid)
        label = f"Setup: {full}"

    elif ctype == "CustomField" and "." in full:
        obj, field = full.split(".", 1)
        oid = object_ids.get(obj, obj)
        if not cid:
            return ""
        url = URL["field"].format(i=instance, oid=oid, fid=cid)
        label = f"Setup: {field}"

    elif ctype == "RecordType" and "." in full:
        obj, rt = full.split(".", 1)
        oid = object_ids.get(obj, obj)
        url = URL["recordtype"].format(i=instance, oid=oid, rid=cid)
        label = f"Setup: {rt}"

    elif ctype == "ValidationRule" and "." in full:
        obj, rule = full.split(".", 1)
        oid = object_ids.get(obj, obj)
        url = URL["rule"].format(i=instance, oid=oid, rid=cid)
        label = f"Setup: {rule}"

    elif ctype == "PermissionSet":
        url = URL["permset"].format(i=instance, pid=cid)
        label = f"Setup: {full}"

    else:
        return ""

    return f'=HYPERLINK("{url}","{label}")'


def match_rows(tabs) -> dict[str, tuple[str, int]]:
    """fullName → (tab, row). Built from what the workbook says WILL deploy, so
    it mirrors 04's naming exactly."""
    index = {}

    for tab_name, tab in tabs.items():
        sf_object = tab_name if tab_name.endswith("__c") or tab_name in (
            "Account", "Contact", "Opportunity", "Case", "Lead",
            "Product2", "OpportunityLineItem") else f"{tab_name}__c"

        fields = tab.sections.get("FIELDS")
        if fields:
            for row in fields.rows:
                api = str(row.get("Final_API") or row.get("Proposed_API") or "").strip()
                if api:
                    index[f"{sf_object}.{api}"] = (tab_name, row["_row"])
                    # The object row itself: the first field row stands in for it.
                    index.setdefault(sf_object, (tab_name, row["_row"]))

        rels = tab.sections.get("RELATIONSHIPS")
        if rels:
            for row in rels.rows:
                api = str(row.get("Final_API") or row.get("Proposed_API") or "").strip()
                if api and not api.endswith("__c"):
                    api += "__c"
                if api:
                    index[f"{sf_object}.{api}"] = (tab_name, row["_row"])

        rts = tab.sections.get("RECORD_TYPES")
        if rts:
            for row in rts.rows:
                rt = str(row.get("Final_RT_API") or row.get("Proposed_RT_API") or "").strip()
                if rt:
                    index.setdefault(f"{sf_object}.{rt}", (tab_name, row["_row"]))

        rules = tab.sections.get("VALIDATION_RULES")
        if rules:
            for row in rules.rows:
                name = str(row.get("Rule_Name") or "").strip()
                if name:
                    index[f"{sf_object}.{name}"] = (tab_name, row["_row"])

    return index


def main() -> int:
    parser = argparse.ArgumentParser(description="Write deploy results into the workbook")
    parser.add_argument("--deploy-json", required=True, help="path to the deploy result JSON")
    parser.add_argument("--org", default="client-sbx", help="org alias, for the instance URL")
    args = parser.parse_args()

    deploy_path = Path(args.deploy_json)
    if not deploy_path.is_absolute():
        deploy_path = ROOT / deploy_path
    if not deploy_path.exists():
        log(f"ERROR: {deploy_path} not found.")
        return 1
    if not WB.exists():
        log("ERROR: no workbook.")
        return 1

    successes, failures, deploy_id = load_components(deploy_path)
    log("=" * 70)
    log("WRITEBACK")
    log("=" * 70)
    log(f"  {len(successes)} success(es), {len(failures)} failure(s)")

    instance = instance_url(args.org)
    object_ids = build_object_ids(successes)
    log(f"  resolved {len(object_ids)} object id(s) for URL building")

    tabs = wbmod.read_workbook(WB)
    index = match_rows(tabs)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    updates = {}
    matched = unmatched = 0

    for entry in successes:
        full = entry.get("fullName", "")
        location = index.get(full)
        if not location:
            unmatched += 1
            continue
        tab, row = location
        matched += 1
        updates[(tab, row, "Component_Id")] = entry.get("id", "")
        updates[(tab, row, "Deployed_At")] = now
        updates[(tab, row, "Result")] = "Success"
        updates[(tab, row, "Error")] = ""
        link = setup_link(entry, object_ids, instance)
        if link:
            updates[(tab, row, "Setup_Link")] = link

    for entry in failures:
        full = entry.get("fullName", "")
        location = index.get(full)
        if not location:
            unmatched += 1
            continue
        tab, row = location
        matched += 1
        # Rule 10: the row keeps its Y. Only Result and Error change, so the
        # next run retries exactly the failures and nothing else.
        updates[(tab, row, "Deployed_At")] = now
        updates[(tab, row, "Result")] = "Failed"
        updates[(tab, row, "Error")] = str(entry.get("problem", ""))[:500]

    wbmod.write_cells(WB, updates)

    log(f"  wrote {len(updates)} cell(s) across {matched} row(s)")
    if unmatched:
        log(f"  {unmatched} component(s) had no matching workbook row "
            "(generated components such as the external ID field and the "
            "permission set are expected here)")

    if failures:
        log(f"\n{len(failures)} FAILURE(S) written back — each row keeps its Y "
            "and will retry next run:")
        grouped: dict[str, list[str]] = {}
        for entry in failures:
            grouped.setdefault(str(entry.get("problem", "?"))[:80], []).append(
                entry.get("fullName", "?"))
        for problem, names in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            log(f"  [{len(names)}] {problem}")
            for name in names[:5]:
                location = index.get(name)
                where = f"{location[0]}!row {location[1]}" if location else "unmatched"
                log(f"      - {name}  ({where})")

    if deploy_id and instance:
        log(f"\n  deploy status: {URL['deploy'].format(i=instance, did=deploy_id)}")

    log("\nCHECKPOINT: open the workbook and click three Setup links —")
    log("one object, one field, one validation rule. Each must land on the")
    log("right Setup page. Verify once by hand before trusting them at scale.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
