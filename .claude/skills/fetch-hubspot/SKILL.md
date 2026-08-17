---
name: fetch-hubspot
description: Fetch the full HubSpot schema and cache it to raw/. Use when starting Phase 0 discovery, when the client's portal contents are unknown, or when raw/ is stale and the workbook needs regenerating from fresh schema.
allowed-tools: Bash, Read
---

# Fetch HubSpot schema

Read-only. Touches no Salesforce org.

## Steps

1. Activate the venv: `source .venv/bin/activate`
2. Run `python scripts/01_fetch_hubspot.py`
   - No portal token yet? Use `python scripts/01_fetch_hubspot.py --fixture`
     to read canned JSON from `fixtures/` instead of calling the API.
3. Report:
   - Total object count, split standard vs custom
   - Every custom object by name and label
   - Property count per object
   - Pipeline count per object, and stage counts
   - Owner count, and how many are inactive
   - Sensitive Data status from `raw/portal_capabilities.json`

## Flag these explicitly — they are the judgement calls

- **Calculated properties.** They do not translate to SF formulas. List every one.
- **Picklists with >100 options.** Probably should be a lookup to a custom object.
- **Name collisions**: two HubSpot properties that collapse to the same SF API
  name once transformed and suffixed with `__c`.
- **Names >40 chars** once suffixed — they need a manual short name.
- **Properties with fill rate under 5%** — likely not worth migrating.
- **Objects present in HubSpot but absent from `config/object_map.yml`**
  (the `discovered.unmapped` list). Each needs a mapping decision.
- **Sensitive Data enabled** — activity objects may be silently truncated.

## Do not

- Print or log the token value.
- Modify `config/object_map.yml` by hand. `01` rewrites the `discovered` block;
  everything else is human-owned.
- Proceed to `/build-workbook` if any custom object the client mentioned is
  missing from `raw/schemas.json`. That is almost always a **Private App scope
  problem**, not a code problem — check scopes before debugging code.
