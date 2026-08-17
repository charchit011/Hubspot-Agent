#!/usr/bin/env python3
"""08 — Report migration state across schema and data.

    python scripts/08_status.py

Reads the workbook and logs/. Reports where things stand, then names the next
three actions. Backs the /migration-status skill.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib import hashing, workbook as wbmod

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "mapping" / "workbook.xlsx"
RAW = ROOT / "raw"
LOGS = ROOT / "logs"
FORCE_APP = ROOT / "force-app" / "main" / "default"


def log(msg=""):
    print(msg, flush=True)


def main() -> int:
    log("=" * 74)
    log("MIGRATION STATUS")
    log("=" * 74)

    if not RAW.exists() or not (RAW / "schemas.json").exists():
        log("\nNothing fetched yet.")
        log("\nNEXT THREE ACTIONS:")
        log("  1. python scripts/make_fixtures.py")
        log("  2. python scripts/01_fetch_hubspot.py --fixture")
        log("  3. python scripts/02_build_workbook.py")
        return 0

    age = (datetime.now().timestamp() - (RAW / "schemas.json").stat().st_mtime) / 86400
    stale = " STALE — re-run 01" if age > 7 else ""
    log(f"\n  raw/ schema age: {age:.1f} days{stale}")

    caps = RAW / "portal_capabilities.json"
    if caps.exists():
        data = json.loads(caps.read_text(encoding="utf-8"))
        log(f"  sensitive data:  {data.get('sensitive_data_enabled')}")
        if data.get("scope_problems"):
            log(f"  SCOPE PROBLEMS:  {len(data['scope_problems'])} — schema is incomplete")

    if not WB.exists():
        log("\n  No workbook yet.")
        log("\nNEXT THREE ACTIONS:")
        log("  1. python scripts/02_build_workbook.py")
        log("  2. review the workbook, set every Deploy to Y or N")
        log("  3. python scripts/03_validate_sheet.py")
        return 0

    tabs = wbmod.read_workbook(WB)
    approval = wbmod.read_approval(WB)

    log(f"\n  {'object':<22} {'Y':>4} {'N':>4} {'HOLD':>5}  {'deployed':>9}  "
        f"{'failed':>6}")
    log("  " + "-" * 66)

    totals = {"Y": 0, "N": 0, "HOLD": 0, "deployed": 0, "failed": 0}

    for tab_name, tab in sorted(tabs.items()):
        counts = {"Y": 0, "N": 0, "HOLD": 0, "deployed": 0, "failed": 0}
        for section in tab.sections.values():
            for row in section.rows:
                decision = str(row.get("Deploy", "")).strip().upper()
                if decision in counts:
                    counts[decision] += 1
                result = str(row.get("Result", "")).strip()
                if result == "Success":
                    counts["deployed"] += 1
                elif result == "Failed":
                    counts["failed"] += 1
        for key in totals:
            totals[key] += counts[key]

        flag = "  ←" if counts["HOLD"] or counts["failed"] else ""
        log(f"  {tab_name:<22} {counts['Y']:>4} {counts['N']:>4} {counts['HOLD']:>5}  "
            f"{counts['deployed']:>9}  {counts['failed']:>6}{flag}")

    log("  " + "-" * 66)
    log(f"  {'TOTAL':<22} {totals['Y']:>4} {totals['N']:>4} {totals['HOLD']:>5}  "
        f"{totals['deployed']:>9}  {totals['failed']:>6}")

    # -- gates -------------------------------------------------------------
    log("\n  GATES")
    log(f"    approval:  {approval['Status']}"
        + (f" by {approval['Approved_By']} at {approval['Approved_At']}"
           if approval['Approved_By'] else ""))

    current = hashing.content_hash(list(wbmod.all_rows(tabs)))
    if approval["Content_Hash"]:
        match = hashing.hashes_match(approval["Content_Hash"], current)
        log(f"    hash:      {'matches' if match else 'STALE — the sheet changed after approval'}")
    else:
        log("    hash:      not recorded")

    generated = list(FORCE_APP.rglob("*-meta.xml")) if FORCE_APP.exists() else []
    log(f"    metadata:  {len(generated)} file(s) generated")

    deploys = sorted(LOGS.glob("deploy_*.json")) if LOGS.exists() else []
    log(f"    deploys:   {len(deploys)} run(s)"
        + (f", last {deploys[-1].name}" if deploys else ""))

    # -- next actions ------------------------------------------------------
    log("\n  NEXT THREE ACTIONS")
    actions = []

    if totals["HOLD"]:
        held = wbmod.holds(tabs)
        by_tab: dict[str, int] = {}
        for row in held:
            by_tab[row["_tab"]] = by_tab.get(row["_tab"], 0) + 1
        worst = sorted(by_tab.items(), key=lambda kv: -kv[1])[:2]
        detail = ", ".join(f"{n} on {t}" for t, n in worst)
        actions.append(f"Resolve {totals['HOLD']} HOLD rows ({detail}). "
                       "Set each Deploy to Y or N.")
        actions.append("python scripts/03_validate_sheet.py  → expect PASSED")
        actions.append('python scripts/03_validate_sheet.py --approve --by "Your Name"')
    elif approval["Status"] != "APPROVED":
        actions.append("python scripts/03_validate_sheet.py  → expect PASSED")
        actions.append('python scripts/03_validate_sheet.py --approve --by "Your Name"')
        actions.append("python scripts/04_sheet_to_metadata.py --check")
    elif approval["Content_Hash"] and not hashing.hashes_match(
            approval["Content_Hash"], current):
        actions.append("The sheet changed after approval — review the diff in intent.")
        actions.append("python scripts/03_validate_sheet.py")
        actions.append('python scripts/03_validate_sheet.py --approve --by "Your Name"')
    elif totals["failed"]:
        actions.append(f"Fix {totals['failed']} failed row(s) — see the Error column.")
        actions.append("python scripts/04_sheet_to_metadata.py --check")
        actions.append("python scripts/05_deploy.py --org client-sbx  → re-validate")
    elif not generated:
        actions.append("python scripts/04_sheet_to_metadata.py --check")
        actions.append("sf org login web --alias client-sbx  (restores the real checkpoint)")
        actions.append("python scripts/05_deploy.py --org client-sbx")
    elif not totals["deployed"]:
        actions.append("sf org login web --alias client-sbx  (no org connected yet)")
        actions.append("python scripts/05_deploy.py --org client-sbx  → validate, then STOP")
        actions.append("python scripts/05_deploy.py --org client-sbx --quick-deploy <id>")
    else:
        actions.append("Click three Setup links in the workbook to verify they resolve.")
        actions.append("Decide whether the data load (07) is in scope this phase.")
        actions.append("Re-run 01 against the real portal when the token arrives.")

    for i, action in enumerate(actions[:3], start=1):
        log(f"    {i}. {action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
