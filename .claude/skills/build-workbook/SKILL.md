---
name: build-workbook
description: Generate the reviewable migration workbook from cached HubSpot schema. Use after /fetch-hubspot, or to regenerate the workbook after changing config/type_rules.yml or config/naming.yml.
allowed-tools: Bash, Read
---

# Build the migration workbook

Turns `raw/` + `config/` into `mapping/workbook.xlsx` — the source of truth for
everything that follows.

## Steps

1. Confirm `raw/` is populated. **Warn if any file is older than 7 days** —
   a stale schema produces a workbook that reviews cleanly and deploys wrong.
2. If `mapping/workbook.xlsx` already exists, warn before overwriting: it may
   contain human review decisions. Offer `--preserve-decisions`, which carries
   existing `Final_*`, `Deploy`, and `Notes` values forward by row identity.
3. Run `python scripts/02_build_workbook.py`
4. Report per object:
   - fields proposed as new custom fields
   - fields mapped to existing standard fields (no custom field created)
   - fields flagged for review, **with the reason for each**
   - count still at `HOLD` (which on a fresh build is all of them)
5. List every flagged item with its reason, ordered by how much thought it
   needs, so the reviewer knows what to look at first.

## What the reviewer needs told

- Edit only the `Final_*`, `Deploy`, and `Notes` columns. Everything else is
  either generated or protected.
- Set every `Deploy` cell to `Y` or `N`. **Leave nothing at `HOLD`** — `03`
  hard-fails while any remain, by design.
- `N` means "correct as designed, skip this run", not "wrong". It stays
  re-runnable: flipping to `Y` later and re-running must work.

## Do not

- Fix a bad mapping by editing a workbook cell. Fix the rule in
  `config/type_rules.yml` and regenerate. Otherwise the rules never improve and
  every future client costs the same effort as the first.
- Set any `Deploy` cell to `Y` on the client's behalf. Approval is theirs.
- Proceed to any deploy step. This skill stops at the workbook.
