# HubSpot → Salesforce Migration Agent

Reads a client's HubSpot schema, produces a reviewable Excel workbook of
everything to be created in Salesforce, waits for per-row `Y / N / HOLD`
approval, then deploys approved metadata and writes Setup links back.

**This is not an autonomous agent.** Every org write passes a human gate,
enforced by `scripts/guard_deploy.py` — a hook, not a prompt.

## Non-negotiable rules

1. **The workbook is the single source of truth.** `mapping/workbook.xlsx`
   decides what gets deployed. Never deploy from conversation, memory, or a
   config file.

2. **Never hand-edit anything under `force-app/`.** It is generated output.
   To change metadata, change the workbook and re-run `04_sheet_to_metadata.py`.

3. **Never generate metadata XML in conversation.** XML comes only from
   `scripts/lib/sf_metadata.py`. If the emitter is wrong, fix the emitter.

4. **Always validate before deploying.** `sf project deploy validate` must pass,
   and I must confirm, before `sf project deploy start` is ever run.

5. **Never run `03` → deploy in one step.** Stop and show me the validation
   summary. Wait for an explicit go-ahead.

6. **Default target org is `client-sbx`.** Deploying to any org whose alias
   contains `prod` requires me to type the alias in full and confirm twice,
   and requires `MIGRATION_ALLOW_PROD=1` in the environment.

7. **Records are not metadata.** Records load via Bulk API (`07_load_data.py`),
   never through a metadata deploy.

8. **Validation rules deploy after the data load**, or are deployed with
   `active=false`. Rules written for future data entry will reject legitimate
   historical records.

9. **Deploy order is fixed:** objects → fields → picklists → record types →
   permission set (FLS) → relationships → validate → deploy → data load →
   validation rules.

10. **Every failed row keeps its `Y`.** Failures are written back into the
    workbook with the error text. The next run retries only failures.

## Definitions

- `Y` = deploy this run. `N` = correct as designed, skip this run. `HOLD` = undecided.
- The agent refuses to run while any `HOLD` remains anywhere in the workbook.
- `N` rows must remain re-runnable: flipping to `Y` later and re-running must work.

## Project state — prototype phase

This project is currently a **prototype for a real engagement**. What that
changes, and what it does not:

- **No HubSpot portal access yet.** `HUBSPOT_TOKEN` in `.env` is a placeholder.
  `01_fetch_hubspot.py` therefore supports `--fixture`, which reads canned JSON
  from `fixtures/` instead of calling the API. Scripts 02–06 are fully testable
  today via that path. When the real token arrives, drop `--fixture` — nothing
  downstream changes.

- **Portal contents unknown.** Custom objects, pipelines, and Sensitive Data
  status are all undiscovered. Nothing client-specific is hardcoded anywhere.
  Any object not named in `config/object_map.yml` falls through to `defaults`
  and becomes a generated SF custom object. `01` probes for Sensitive Data by
  attempting an engagements read and catching the 403; the result lands in
  `raw/portal_capabilities.json`. **Never assume it is off.**

- **No Salesforce org connected.** This is the significant gap. The guide's
  checkpoint for script 04 is `sf project deploy validate` passing against a
  real org, which proves the XML is *deployable* rather than merely well-formed
  — Salesforce rejects plenty of XML that parses fine. Until an org exists,
  `04` self-checks offline via `--check`: XML well-formedness, an
  `sf project convert source` structural pass, and assertions over
  field-type/attribute combinations Salesforce is known to reject. **Treat that
  as ~70% confidence, not a green light.** A free Developer Edition org
  aliased `client-sbx` restores the real checkpoint and changes no other code.

- **Scope is schema-only** (scripts 01–06). `07_load_data.py` exists and is
  complete, but is out of scope for this phase and must not be run without a
  separate explicit decision.

- **Built for reuse across clients.** No client specifics in code — everything
  lives in `config/*.yml`. When a client review returns a wrong mapping, fix the
  **rule** in `config/type_rules.yml` and regenerate. Do not hand-patch workbook
  cells: the rules never improve otherwise, and every future client costs the
  same as the first.

## Target org assumptions

Recorded from the build interview. Each is an assumption until verified against
a real org — verify before Phase 3, not after.

- **Person Accounts: OFF.** Contact → `Contact`, Company → `Account`.
  Verify with `sf data query -q "SELECT IsPersonAccount FROM Account LIMIT 1"`.
  If that query *succeeds*, Person Accounts are enabled, `config/object_map.yml`
  is wrong, and the Contact model must be reworked before anything else.
- **Record Types: in scope for everything with a pipeline.** Deals → Opportunity
  record types + Sales Processes; Tickets → Case record types + Support
  Processes; custom objects get record types where HubSpot reports pipelines.
- **Multi-currency: unknown.** If enabled, every Currency field needs
  `CurrencyIsoCode` handling. Check before proposing Currency fields.

## Environment

- macOS (arm64). Python venv at `.venv` — always activate before running scripts.
- Python 3.14. If an `openpyxl`/`lxml` wheel fails to build, rebuild the venv on
  3.12 rather than fighting it; nothing in this project needs 3.13+.
- Salesforce CLI 2.123.1 is installed. Node 25.
- Secrets in `.env`. **Never print token values to the console or logs.**
  `scripts/lib/hubspot_client.py` redacts them; keep it that way.
- Org aliases: `client-sbx` (default, not yet created), `client-prod` (guarded).

## Working agreement

- **Build one script per session**, in the guide's order. After each, state the
  exact checkpoint command and what output proves it works. Do not start the
  next script until the checkpoint is confirmed passing.
- **Never write to a Salesforce org during the build phase. Validate only.**
  `sf project deploy validate` is permitted; `deploy start` and `deploy quick`
  are not, and the hook blocks them regardless.
- When a script fails, fix the script — not the generated output, not the
  workbook cell, not the XML.
- Prefer failing loudly over proceeding on a guess. A hard error naming the tab
  and row is worth more than a partial deploy.

## Reference

Full build guide, including endpoint tables, Setup URL patterns, the gotchas
checklist, and the six-phase client delivery sequence: `docs/BUILD_GUIDE.md`.
