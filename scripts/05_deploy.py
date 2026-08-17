#!/usr/bin/env python3
"""05 — Validate, then (separately) quick-deploy.

    python scripts/05_deploy.py --org client-sbx                  # validate, then STOP
    python scripts/05_deploy.py --org client-sbx --quick-deploy <id>

Two invocations by design. Validation reports and exits; it never rolls into a
deploy. CLAUDE.md rule 5 — the human gate lives between these two calls, and
guard_deploy.py enforces it independently.

CHECKPOINT: validation reports component counts and a deploy id, and the script
exits without deploying anything.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib import hashing, workbook as wbmod

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "mapping" / "workbook.xlsx"
LOGS = ROOT / "logs"


def log(msg=""):
    print(msg, flush=True)


def run_sf(args: list[str]) -> tuple[int, dict]:
    log(f"  $ {' '.join(args)}")
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
    return result.returncode, payload


def save_log(payload: dict, kind: str) -> Path:
    LOGS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOGS / f"{kind}_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def preflight(org: str) -> bool:
    """The same checks the hook makes, reported clearly instead of as a block."""
    if not WB.exists():
        log("ERROR: no workbook. Run 02 first.")
        return False

    tabs = wbmod.read_workbook(WB)
    approval = wbmod.read_approval(WB)

    remaining = wbmod.holds(tabs)
    if remaining:
        log(f"ERROR: {len(remaining)} row(s) still at HOLD. Run 03 for the list.")
        return False

    if approval["Status"] != "APPROVED":
        log(f"ERROR: _Approval!Status is {approval['Status']!r}, not APPROVED.")
        log("  python scripts/03_validate_sheet.py --approve --by \"Your Name\"")
        return False

    current = hashing.content_hash(list(wbmod.all_rows(tabs)))
    if not hashing.hashes_match(approval["Content_Hash"], current):
        log("ERROR: the workbook changed after approval.")
        log(f"  approved: {hashing.short_hash(approval['Content_Hash'])}…")
        log(f"  current:  {hashing.short_hash(current)}…")
        log("  Re-validate and re-approve before deploying.")
        return False

    if "prod" in org.lower():
        log(f"\n{'!' * 70}")
        log(f"TARGET ORG {org!r} LOOKS LIKE PRODUCTION.")
        log("Confirm the rollback manifest exists BEFORE proceeding.")
        log(f"{'!' * 70}\n")

    return True


def summarise(result: dict):
    counts = {
        "componentsTotal": result.get("numberComponentsTotal", 0),
        "componentErrors": result.get("numberComponentErrors", 0),
        "componentsDeployed": result.get("numberComponentsDeployed", 0),
    }
    log(f"\n  components: {counts['componentsTotal']} total, "
        f"{counts['componentsDeployed']} processed, {counts['componentErrors']} errors")

    details = result.get("details", {}) or {}
    failures = details.get("componentFailures", []) or []
    if isinstance(failures, dict):
        failures = [failures]

    if failures:
        log(f"\n  {len(failures)} FAILURE(S):")
        grouped: dict[str, list[str]] = {}
        for failure in failures:
            problem = failure.get("problem", "unknown")
            grouped.setdefault(problem[:90], []).append(failure.get("fullName", "?"))
        for problem, names in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            log(f"    [{len(names)}] {problem}")
            for name in names[:5]:
                log(f"        - {name}")
            if len(names) > 5:
                log(f"        … +{len(names) - 5} more")
        log("\n  For a grouped root-cause analysis, use the deploy-error-analyst subagent.")

    warnings = details.get("componentSuccesses", []) or []
    if isinstance(warnings, dict):
        warnings = [warnings]
    changed = [w for w in warnings if w.get("changed")]
    if changed:
        log(f"\n  {len(changed)} component(s) would change.")


def validate(org: str) -> int:
    log("=" * 70)
    log(f"VALIDATE against {org}")
    log("=" * 70)

    code, payload = run_sf(["sf", "project", "deploy", "validate",
                            "--target-org", org, "--json", "--wait", "30"])
    path = save_log(payload, "validate")
    result = payload.get("result", payload)
    summarise(result)
    log(f"\n  full result: {path.relative_to(ROOT)}")

    job_id = result.get("id") or result.get("deployId")
    if code != 0 or result.get("status") not in ("Succeeded", "SucceededPartial"):
        log(f"\nRESULT: VALIDATION FAILED (status {result.get('status')!r}).")
        log("Fix the workbook, re-run 04, then validate again.")
        log("Never hand-patch XML — it is overwritten on the next run.")
        return 1

    log("\n" + "=" * 70)
    log("VALIDATION PASSED — NOTHING HAS BEEN DEPLOYED")
    log("=" * 70)
    log(f"  validated deploy id: {job_id}")
    log("\nSTOPPING HERE, by design. Review the counts above.")
    log("To deploy, on a separate, deliberate invocation:")
    log(f"  python scripts/05_deploy.py --org {org} --quick-deploy {job_id}")
    return 0


def quick_deploy(org: str, job_id: str) -> int:
    log("=" * 70)
    log(f"QUICK DEPLOY {job_id} → {org}")
    log("=" * 70)

    code, payload = run_sf(["sf", "project", "deploy", "quick",
                            "--job-id", job_id, "--target-org", org,
                            "--json", "--wait", "60"])
    path = save_log(payload, "deploy")
    result = payload.get("result", payload)
    summarise(result)

    log(f"\n  full result: {path.relative_to(ROOT)}")
    if code != 0:
        log("\nRESULT: DEPLOY FAILED.")
        log(f"Write the failures back so they are visible in the workbook:")
        log(f"  python scripts/06_writeback.py --deploy-json {path.relative_to(ROOT)}")
        return 1

    log("\nRESULT: DEPLOYED.")
    log("NEXT — write results and Setup links back into the workbook:")
    log(f"  python scripts/06_writeback.py --deploy-json {path.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate, then separately deploy")
    parser.add_argument("--org", default="client-sbx", help="target org alias")
    parser.add_argument("--quick-deploy", metavar="JOB_ID",
                        help="deploy a previously validated job id")
    args = parser.parse_args()

    if not preflight(args.org):
        return 1

    if args.quick_deploy:
        return quick_deploy(args.org, args.quick_deploy)
    return validate(args.org)


if __name__ == "__main__":
    sys.exit(main())
