---
name: deploy-metadata
description: Validate and deploy approved workbook rows to a Salesforce org. Use only after /review-check passes clean and the _Approval tab reads APPROVED. Stops for explicit human go-ahead between validation and deploy.
argument-hint: "[org-alias]"
allowed-tools: Bash, Read
---

# Deploy approved metadata

Target org: `$ARGUMENTS` — defaults to `client-sbx` when not given.

**During the build phase this skill runs to step 4 and stops.** Validation only.
No org writes. `scripts/guard_deploy.py` enforces this independently of anything
written here — it is a hook, and it fires whether or not the model cooperates.

## Steps

1. `python scripts/03_validate_sheet.py` — **abort entirely on any failure**
2. `python scripts/04_sheet_to_metadata.py`
3. `sf project deploy validate --target-org <org> --json`
   - No org connected yet? Use `python scripts/04_sheet_to_metadata.py --check`
     for the offline substitute: XML well-formedness, an
     `sf project convert source` structural pass, and assertions over field
     type/attribute combinations Salesforce rejects. Report it as **partial
     confidence** — it does not prove deployability.
4. Report component counts, every warning, and the validated deploy ID.
   **STOP. Wait for an explicit go-ahead.** Do not interpret enthusiasm,
   silence, or "looks good" as authorisation to deploy.
5. On explicit go-ahead only:
   `sf project deploy quick --job-id <id> --target-org <org> --json`
6. `python scripts/06_writeback.py --deploy-json <path>`
7. Report: deployed count, failed count, and for each failure its workbook tab,
   row, and error text. Failed rows keep their `Y` and retry next run.

## Production

An alias containing `prod` requires all of:
- `MIGRATION_ALLOW_PROD=1` set in the environment, deliberately, for that session
- the operator typing the full alias
- confirming twice
- a `destructiveChanges.xml` rollback manifest generated **beforehand**

The guard hook blocks the deploy if the first is missing. The rest is on the
operator, and asking for them is part of this skill's job.

## Do not

- Run `deploy start` when a validated deploy id exists — use `deploy quick`.
- Continue past step 4 without an explicit, unambiguous go-ahead.
- Hand-patch XML to make a deploy pass. Fix the workbook, re-run `04`.
- Deploy when `_Approval!Content_Hash` no longer matches the workbook. That
  mismatch means the sheet changed after approval — the single most valuable
  check in the project.
