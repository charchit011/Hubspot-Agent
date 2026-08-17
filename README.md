# HubSpot → Salesforce Migration Agent

Reads a HubSpot portal's schema, produces a reviewable Excel workbook of
everything to be created in Salesforce, waits for per-row `Y / N / HOLD`
approval, then deploys the approved metadata and writes Setup links back.

Not an autonomous agent. Every org write passes a human gate enforced by a
hook, not by a prompt. Full design rationale: [docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md).
Project rules the agent must follow: [CLAUDE.md](CLAUDE.md).

## Current phase

Prototype for a real engagement. **Schema-only** (scripts 01–06).

- No HubSpot token yet → `01` runs in `--fixture` mode against synthetic schema
- No Salesforce org yet → `04 --check` substitutes for `deploy validate`
- Person Accounts assumed **off**; verify before Phase 3
- Built config-driven for reuse across clients

## Quick start

```bash
source .venv/bin/activate

python scripts/make_fixtures.py                        # synthetic HubSpot schema
python scripts/01_fetch_hubspot.py --fixture           # → raw/
python scripts/02_build_workbook.py                    # → mapping/workbook.xlsx
# ... review the workbook: set every Deploy cell to Y or N ...
python scripts/03_validate_sheet.py                    # must PASS
python scripts/03_validate_sheet.py --approve --by "Your Name"
python scripts/04_sheet_to_metadata.py --check         # → force-app/
python scripts/08_status.py                            # where am I?
```

`scripts/dev_simulate_review.py` fills in Y/N decisions for testing, so the
pipeline runs end to end without a human reviewer.

## Pipeline

| Script | Does | Checkpoint |
|---|---|---|
| `01_fetch_hubspot.py` | HubSpot schema → `raw/` | Every client-named custom object appears |
| `02_build_workbook.py` | `raw/` + `config/` → workbook | Sections collapse, dropdowns work, all `HOLD` |
| `03_validate_sheet.py` | Catch contradictions | A deliberate contradiction exits non-zero |
| `04_sheet_to_metadata.py` | Workbook → XML | `--check` passes; with an org, `deploy validate` |
| `05_deploy.py` | Validate, then separately deploy | Validation reports and **stops** |
| `06_writeback.py` | Results + Setup links → workbook | Three links land on the right Setup pages |
| `07_load_data.py` | Bulk API 2.0 upsert | Second run is a clean no-op |
| `08_status.py` | State + next three actions | — |

`guard_deploy.py` runs as a `PreToolUse` hook and blocks: unapproved deploys,
deploys whose approval hash has gone stale, outstanding `HOLD` rows, production
without `MIGRATION_ALLOW_PROD=1`, and any destructive command.

## Skills

`/fetch-hubspot` · `/build-workbook` · `/review-check` · `/deploy-metadata` ·
`/load-data` · `/migration-status`

Subagents: `schema-analyst` (keeps large schema JSON out of context),
`deploy-error-analyst` (groups deploy failures by root cause).

## Config

Three files drive everything. When a client review returns a wrong mapping, fix
the **rule** here and regenerate — never hand-patch workbook cells.

- `config/object_map.yml` — HubSpot object → SF object; `defaults` catches
  anything undiscovered
- `config/type_rules.yml` — type mapping, sizing, review triggers
- `config/naming.yml` — API naming, limits, reserved words, standard field map

## What still needs a human

1. **HubSpot Private App token** with all `crm.schemas.*.read`,
   `crm.objects.*.read`, `crm.objects.owners.read` scopes. A missing scope
   produces a silently incomplete schema, not an error.
2. **A Salesforce org** aliased `client-sbx`. A free Developer Edition restores
   the real `deploy validate` checkpoint and changes no code.
3. **`.env`** — copy from `.env.example` and fill in.
4. **The review itself.** Nothing deploys while any row reads `HOLD`.
