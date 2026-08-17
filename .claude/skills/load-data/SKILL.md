---
name: load-data
description: Load HubSpot records into Salesforce via Bulk API 2.0, in dependency order. Use only after the schema deploy shows Result=Success for the target objects. Out of scope during the schema-only prototype phase.
argument-hint: "[org-alias] [object|all]"
allowed-tools: Bash, Read
---

# Load records

**Out of scope for the current phase.** The project is schema-only (scripts
01–06). Running this needs a separate, explicit decision — say so and stop if
invoked without one.

Target org: first argument, default `client-sbx`. Objects: second argument,
default `all`.

## Preconditions — verify all four, refuse if any fails

1. The schema deploy for these objects shows `Result=Success` in the workbook.
2. `HubSpot_Record_Id__c` exists on each object **and is External ID + unique**.
   Without unique, Bulk upsert cannot match and you get duplicates.
3. Every HubSpot owner in `_Users` has a Salesforce user mapped. Unmapped
   owners silently default ownership to the running user.
4. Validation rules are inactive or not yet deployed. Rules written for future
   data entry reject legitimate historical records.

## Steps

1. `python scripts/07_load_data.py --org <org> --objects <object|all>`
2. Extract → transform → upsert, **parents before children**
   (Account → Contact → Opportunity → OpportunityLineItem).
3. **Two passes, never combined:**
   - Pass 1: records, with lookup fields left null
   - Pass 2: relationships wired by external ID
4. Report per object: attempted, succeeded, failed, plus a sample of each
   distinct error type — not a dump of every failed row.
5. Remind the operator that validation rules are still pending and should be
   activated now, then verified against the loaded data.

## The idempotency test

Run the load **twice**. The second run must be a clean no-op.

If it creates duplicates, the external ID setup is wrong and a production run
would have been a disaster. This is the single most important check in the data
phase — do not skip it because the first run looked fine.

## Do not

- Load records through a metadata deploy. Records are not metadata.
- Combine the two passes to save time.
- Activate validation rules before the load completes and reconciles.
