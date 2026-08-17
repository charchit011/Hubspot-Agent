---
name: review-check
description: Validate the reviewed workbook for contradictions before any deploy. Use after the client returns their reviewed workbook, and before /deploy-metadata. Runs in seconds and prevents partial deploys.
allowed-tools: Bash, Read
---

# Validate the reviewed workbook

Catches contradictions before anything touches an org. Cheap to run, and it
saves the expensive failure mode: a half-applied deploy.

## Steps

1. Run `python scripts/03_validate_sheet.py`
2. **If it fails**, report each error as four things:
   `tab | row | what is wrong | what to change it to`
   Group by tab. Never dump a raw stack trace at the reviewer.
3. **If it passes**, summarise exactly what would deploy:
   - objects: how many `Y`, how many `N`
   - fields: `Y` / `N` per object
   - picklists, record types, relationships, validation rules
   - the permission set, and how many fields it grants FLS on
4. **Stop here.** Do not proceed to deployment under any circumstance.

## The checks, and why each exists

| Check | Failure it prevents |
|---|---|
| Any `HOLD` remaining | Deploying something nobody decided on |
| Field `Y` under object `N` | Orphaned field, deploy error |
| Lookup `Y` to an object neither deploying nor in the org | Broken reference |
| Validation rule `Y` referencing a field `N` | Rule references a missing field |
| Master-detail `Y` on an object with records | Impossible in Salesforce, full stop |
| API name >40 chars, missing `__c`, reserved, duplicate | Rejected at deploy |
| Picklist value >255 chars, or duplicated | Rejected at deploy |
| Text with length >255 | Warn — should be LongTextArea |
| Required field with Fill % < 100 | **Rejects historical records on load** |
| Owner field mapped, `_Users` has unmapped owners | Ownership silently defaults to the running user |

A warning does not block. A failure exits non-zero and nothing downstream runs.

## Do not

- Suggest bypassing a failure. Every one of these represents a real deploy
  failure or a silent data problem — the check is the cheap version.
- Edit the workbook to make a check pass without saying so explicitly.
