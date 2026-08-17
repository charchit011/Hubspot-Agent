---
name: migration-status
description: Report the current state of the migration across schema and data. Use when picking up work after a break, before a client check-in, or when unsure what the next action is.
allowed-tools: Bash, Read
---

# Migration status

Read `mapping/workbook.xlsx` and `logs/`. Report, then recommend.

## Steps

1. `python scripts/08_status.py` (falls back to reading the workbook directly
   if the script is not present yet).
2. Report this table:

   | Object | Fields Y / N / HOLD | Schema deployed | Records loaded | Open failures |
   |---|---|---|---|---|

3. Then report the gates:
   - `_Approval` status: `DRAFT` or `APPROVED`, by whom, when
   - whether the content hash still matches the workbook
     (**a mismatch means the sheet changed after approval — say so loudly**)
   - age of `raw/` — stale beyond 7 days is worth flagging
   - which delivery phase the project is in, per `docs/BUILD_GUIDE.md` Part 8

4. **List the next three actions, in order.** Be specific: name the command and
   what its passing output looks like. "Review the workbook" is not an action;
   "resolve the 14 HOLD rows on the Deal tab, all calculated properties" is.

## Do not

- Guess at state not evidenced in the workbook or logs. Say "unknown" and name
  the command that would establish it.
- Recommend a deploy as a next action unless `/review-check` passes clean and
  `_Approval` reads `APPROVED` with a matching hash.
