#!/usr/bin/env python3
"""PreToolUse hook — blocks unsafe Salesforce deploys.

CLAUDE.md is advisory: the model follows it because it is asked to. This is
mandatory. It fires before the Bash tool call and exits non-zero to block it,
whether or not the model cooperates.

Reads the pending tool call as JSON on stdin. Blocks when any of these hold:

  1. `deploy start` / `deploy quick`, and _Approval!Status is not APPROVED
  2. `deploy start` / `deploy quick`, and the workbook's current content hash
     differs from _Approval!Content_Hash  ← the sheet changed after approval
  3. Any Deploy cell anywhere still reads HOLD
  4. The target org alias contains "prod" and MIGRATION_ALLOW_PROD is not 1
  5. The command is `sf data delete` or carries --purge-on-delete

Check 2 is the one that earns its keep. Long review cycles mean "I approved
this" and "this is what deploys" drift apart, and nobody notices until after.

Exit 0 = allow. Exit 2 = block, with the reason on stderr.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "mapping" / "workbook.xlsx"

DEPLOY_PATTERN = re.compile(r"\bsf\b.*\bproject\b.*\bdeploy\b.*\b(start|quick)\b")
DESTRUCTIVE_PATTERN = re.compile(
    r"\bsf\b.*\bdata\b.*\bdelete\b|--purge-on-delete|\bsf\b.*\borg\b.*\bdelete\b")
ORG_PATTERN = re.compile(r"(?:--target-org|-o)[= ]+(\S+)")


def block(reason: str, detail: str = ""):
    print(f"\nBLOCKED by guard_deploy.py\n\n  {reason}", file=sys.stderr)
    if detail:
        print(f"\n{detail}", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(2)


def allow():
    sys.exit(0)


def read_command() -> str:
    """Claude Code sends the pending tool call as JSON on stdin."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return ""
    if not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # tolerate a bare command string
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    return tool_input.get("command", "") or ""


def main() -> int:
    command = read_command()
    if not command:
        allow()

    # -- 5. destructive commands, always ----------------------------------
    if DESTRUCTIVE_PATTERN.search(command):
        block(
            "Destructive command refused.",
            "  This migration only ADDS metadata and upserts records. Nothing in\n"
            "  the pipeline needs a delete. If you genuinely need one, run it\n"
            "  yourself outside Claude Code, deliberately.",
        )

    is_deploy = bool(DEPLOY_PATTERN.search(command))

    # -- 4. production interlock ------------------------------------------
    match = ORG_PATTERN.search(command)
    alias = match.group(1) if match else os.environ.get("SF_ORG_ALIAS", "")
    if "prod" in alias.lower():
        if os.environ.get("MIGRATION_ALLOW_PROD") != "1":
            block(
                f"Target org {alias!r} looks like production.",
                "  MIGRATION_ALLOW_PROD is not set to 1.\n\n"
                "  To proceed, deliberately, for this session only:\n"
                "    export MIGRATION_ALLOW_PROD=1\n\n"
                "  Before you do: generate the destructiveChanges.xml rollback\n"
                "  manifest FIRST. Generating it after you need it is too late.",
            )

    if not is_deploy:
        allow()

    # -- deploy-only checks ------------------------------------------------
    if not WB.exists():
        block("No workbook found.",
              f"  {WB.relative_to(ROOT)} does not exist. The workbook is the only\n"
              "  authority on what may deploy — nothing deploys without it.")

    try:
        from lib import hashing, workbook as wbmod
    except ImportError as exc:
        block("Cannot import the workbook library.",
              f"  {exc}\n  Is the venv active? The hook cannot verify approval, so it blocks.")

    try:
        tabs = wbmod.read_workbook(WB)
        approval = wbmod.read_approval(WB)
    except Exception as exc:
        block("Cannot read the workbook.",
              f"  {exc}\n  The hook cannot verify approval, so it blocks by default.")

    # -- 3. outstanding HOLDs ---------------------------------------------
    remaining = wbmod.holds(tabs)
    if remaining:
        sample = "\n".join(
            f"    {r['_tab']}!row {r['_row']}: "
            f"{r.get('HS Property') or r.get('HS Association') or r.get('HS Stage') or '?'}"
            for r in remaining[:8])
        more = f"\n    … +{len(remaining) - 8} more" if len(remaining) > 8 else ""
        block(f"{len(remaining)} row(s) still at HOLD.",
              f"  Nothing deploys while anyone is undecided:\n\n{sample}{more}\n\n"
              "  Set every Deploy cell to Y or N, then re-run 03.")

    # -- 1. approval status ------------------------------------------------
    if approval["Status"] != "APPROVED":
        block(f"_Approval!Status is {approval['Status']!r}, not APPROVED.",
              "  Approve with:\n"
              "    python scripts/03_validate_sheet.py --approve --by \"Your Name\"")

    # -- 2. hash drift — the check that earns its keep ---------------------
    current = hashing.content_hash(list(wbmod.all_rows(tabs)))
    if not hashing.hashes_match(approval["Content_Hash"], current):
        block(
            "The workbook changed after it was approved.",
            f"  approved hash: {hashing.short_hash(approval['Content_Hash'])}…\n"
            f"  current hash:  {hashing.short_hash(current)}…\n\n"
            f"  Approved by {approval['Approved_By'] or '?'} "
            f"at {approval['Approved_At'] or '?'}.\n\n"
            "  Someone edited Final_*, Deploy or Notes since then. What would\n"
            "  deploy is no longer what was signed off. Re-validate and\n"
            "  re-approve:\n"
            "    python scripts/03_validate_sheet.py --approve --by \"Your Name\"",
        )

    allow()


if __name__ == "__main__":
    main()
