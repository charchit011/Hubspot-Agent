# HubSpot → Salesforce Migration Agent
## Build & Delivery Guide

**What this builds:** a Claude Code project that reads a client's HubSpot schema, produces a reviewable Excel workbook of everything to be created in Salesforce, waits for your approval on a per-row `Y / N / HOLD` basis, then deploys the approved metadata and writes Setup links back into the workbook.

**What this is not:** an autonomous agent. Every org write passes a human gate, enforced by a hook, not by a prompt.

---

# Part 0 — Prerequisites

Collect these before writing any code. Missing access is the most common reason day one becomes day three.

| Item | How to get it | Blocker if missing |
|---|---|---|
| HubSpot Private App token | Client portal → Settings → Integrations → Private Apps | Cannot fetch schema |
| HubSpot scopes | `crm.schemas.*.read`, `crm.objects.*.read`, `crm.objects.owners.read` | Partial schema, silent gaps |
| Salesforce sandbox | Full or Partial copy sandbox, refreshed | Cannot rehearse realistically |
| SF user with Modify All Data + Customize Application | Client admin grants | Deploys fail mid-run |
| Node.js 18+ | nodejs.org | Claude Code CLI won't install |
| Python 3.10+ | python.org | Scripts won't run |
| Salesforce CLI | `npm install -g @salesforce/cli` | No deploy path |
| VS Code 1.98+ | code.visualstudio.com | Extension won't install |

**Ask the client these five questions before starting.** Each one changes the design:

1. How many HubSpot custom objects, and what are they?
2. Are HubSpot Pipelines being used, and do they need to become Sales Processes / Record Types?
3. Is this schema-only, or schema + historical records?
4. Is there an existing Salesforce org with metadata already in it, or is this greenfield?
5. Is HubSpot "Sensitive Data" enabled? (Blocks activity objects through some access paths.)

---

# Part 1 — Environment setup

## 1.1 Install the toolchain

```bash
# Claude Code CLI (needed for the integrated terminal, in addition to the extension)
npm install -g @anthropic-ai/claude-code

# Salesforce CLI
npm install -g @salesforce/cli

# Verify
claude --version
sf --version
python3 --version
```

Then in VS Code: `Ctrl+Shift+X` → search "Claude Code" → Install. Sign in with your Claude subscription; no API key needed.

## 1.2 Create the project

```bash
mkdir hubspot-sf-migration && cd hubspot-sf-migration
git init
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install requests openpyxl pyyaml python-dotenv
pip freeze > requirements.txt
code .
```

## 1.3 Authenticate both ends

```bash
# Salesforce — sandbox first, always
sf org login web --alias client-sbx --instance-url https://test.salesforce.com

# Production — add this later, deliberately, not on day one
# sf org login web --alias client-prod
```

HubSpot: paste the Private App token into `.env`.

**`.env`**
```
HUBSPOT_TOKEN=pat-na1-xxxxxxxx
HUBSPOT_PORTAL_ID=12345678
SF_ORG_ALIAS=client-sbx
```

**`.gitignore`** — write this before your first commit, not after:
```
.env
.venv/
raw/
logs/
mapping/*.xlsx
!mapping/workbook.template.xlsx
.sf/
.sfdx/
```

The workbook is gitignored because it contains client data. If you want version history of the *decisions*, have `05_writeback.py` also emit a `mapping/decisions.json` that is safe to commit.

## 1.4 Salesforce project scaffold

```bash
sf project generate --name . --manifest
```

This gives you `sfdx-project.json` and `force-app/main/default/`. Your generator writes into that tree; you never hand-edit it.

---

# Part 2 — Repo structure

```
hubspot-sf-migration/
├── CLAUDE.md                       # project rules — write this first
├── .env                            # gitignored
├── .gitignore
├── requirements.txt
├── sfdx-project.json
│
├── .claude/
│   ├── settings.json               # permissions + hooks
│   ├── skills/
│   │   ├── fetch-hubspot/SKILL.md
│   │   ├── build-workbook/SKILL.md
│   │   ├── review-check/SKILL.md
│   │   ├── deploy-metadata/SKILL.md
│   │   ├── load-data/SKILL.md
│   │   └── migration-status/SKILL.md
│   └── agents/
│       ├── schema-analyst.md
│       └── deploy-error-analyst.md
│
├── config/
│   ├── object_map.yml              # HubSpot object → SF object
│   ├── type_rules.yml              # HubSpot type → SF type defaults
│   └── naming.yml                  # prefixes, suffixes, reserved words
│
├── scripts/
│   ├── 01_fetch_hubspot.py
│   ├── 02_build_workbook.py
│   ├── 03_validate_sheet.py
│   ├── 04_sheet_to_metadata.py
│   ├── 05_deploy.py
│   ├── 06_writeback.py
│   ├── 07_load_data.py
│   ├── guard_deploy.py             # hook — blocks unsafe deploys
│   └── lib/
│       ├── hubspot_client.py
│       ├── workbook.py             # section-marker read/write
│       ├── sf_metadata.py          # XML emitters
│       └── hashing.py              # approval hash
│
├── raw/                            # HubSpot API dumps (gitignored)
├── mapping/
│   └── workbook.xlsx               # the source of truth
├── force-app/main/default/         # generated — never hand-edit
└── logs/
```

---

# Part 3 — CLAUDE.md

Write this by hand before generating anything. It is the constitution of the project.

```markdown
# HubSpot → Salesforce Migration Agent

## Non-negotiable rules

1. **The workbook is the single source of truth.** `mapping/workbook.xlsx` decides
   what gets deployed. Never deploy from conversation, memory, or a config file.

2. **Never hand-edit anything under `force-app/`.** It is generated output.
   To change metadata, change the workbook and re-run `04_sheet_to_metadata.py`.

3. **Never generate metadata XML in conversation.** XML comes only from
   `scripts/lib/sf_metadata.py`. If the emitter is wrong, fix the emitter.

4. **Always validate before deploying.** `sf project deploy validate` must pass,
   and I must confirm, before `sf project deploy start` is ever run.

5. **Never run `03` → deploy in one step.** Stop and show me the validation
   summary. Wait for an explicit go-ahead.

6. **Default target org is `client-sbx`.** Deploying to any org whose alias
   contains `prod` requires me to type the alias in full and confirm twice.

7. **Records are not metadata.** Records load via Bulk API (`07_load_data.py`),
   never through a metadata deploy.

8. **Validation rules deploy after the data load**, or are deployed with
   `active=false`. Rules written for future data entry will reject legitimate
   historical records.

9. **Deploy order is fixed:** objects → fields → picklists → permission set (FLS)
   → relationships → validate → deploy → data load → validation rules.

10. **Every failed row keeps its `Y`.** Failures are written back into the
    workbook with the error text. The next run retries only failures.

## Definitions
- `Y` = deploy this run. `N` = correct as designed, skip this run. `HOLD` = undecided.
- The agent refuses to run while any `HOLD` remains anywhere in the workbook.
- `N` rows must remain re-runnable: flipping to `Y` later and re-running must work.

## Environment
- Python venv at `.venv`. Always activate before running scripts.
- Secrets in `.env`. Never print token values to the console or logs.
- Org aliases: `client-sbx` (default), `client-prod` (guarded).
```

---

# Part 4 — Build the scripts

Build in order. Each has a checkpoint that must pass before you move on. Do not write all seven then test — you will not be able to tell which layer is wrong.

## Script 01 — Fetch HubSpot schema

**Purpose:** dump the complete HubSpot schema to JSON. Read-only, no Salesforce involved.

**Endpoints:**
| Data | Endpoint |
|---|---|
| All object types incl. custom | `GET /crm/v3/schemas` |
| Properties per object | `GET /crm/v3/properties/{objectType}` |
| Owners | `GET /crm/v3/owners` |
| Pipelines & stages | `GET /crm/v3/pipelines/{objectType}` |
| Association labels | `GET /crm/v4/associations/{fromType}/{toType}/labels` |

**Outputs:** `raw/schemas.json`, `raw/properties_{object}.json`, `raw/owners.json`, `raw/pipelines.json`, `raw/associations.json`

**Prompt to give Claude Code:**
> Write `scripts/01_fetch_hubspot.py`. It reads `HUBSPOT_TOKEN` from `.env`, calls the CRM v3 schemas endpoint to list all object types including custom ones, then fetches properties, owners, pipelines, and v4 association labels for each. Handle pagination via the `paging.next.after` cursor. Handle 429 with exponential backoff. Write each response to `raw/` as pretty-printed JSON. Never log the token.

**Checkpoint:** open `raw/schemas.json` and confirm every custom object the client mentioned is present. If one is missing, it is almost always a scope problem on the Private App, not a code problem.

## Script 02 — Build the workbook

**Purpose:** turn raw JSON + config into the reviewable Excel workbook.

**Structure** (one tab per object, sections stacked inside, column A hidden):

```
Tab: Contact
  A: marker      B..N: data
  #SECTION:FIELDS
  #HEADER   HS Property | HS Type | Fill % | Sample | Proposed_API | Proposed_Type |
            Proposed_Len | Final_API | Final_Type | Final_Len | Deploy | Notes |
            Deployed_At | Component_Id | Setup_Link | Result | Error
  #ROW      firstname | string | 98% | ... | FirstName | Text | 40 | ... | Y |
  #END
  #SECTION:PICKLISTS
  #SECTION:RELATIONSHIPS
  #SECTION:VALIDATION_RULES
  #SECTION:RECORD_TYPES
```

Plus control tabs: `_Index`, `_Users`, `_Approval`, `_RunLog`.

**`_Approval` tab** holds exactly four cells: `Status` (`DRAFT` / `APPROVED`), `Approved_By`, `Approved_At`, `Content_Hash`. The hash is what the guard checks.

**Prompt:**
> Write `scripts/02_build_workbook.py` and `scripts/lib/workbook.py`. Read `raw/` and `config/`, emit `mapping/workbook.xlsx` with openpyxl. One tab per object. Use hidden column A for `#SECTION:` / `#HEADER` / `#ROW` / `#END` markers so the file can be parsed back regardless of row positions. Group each section's rows with `row_dimensions.group()` so sections collapse. Add data validation dropdowns on Final_Type (valid SF field types) and Deploy (`Y`/`N`/`HOLD`). Protect the Proposed_* columns; leave Final_*, Deploy, and Notes unlocked. Default every Deploy cell to `HOLD`. Build an `_Index` tab with HYPERLINK() to each object tab plus counts. Colour object tabs red/amber/green by review state.

**Checkpoint:** open the xlsx. Sections collapse. Dropdowns work. You cannot type into a Proposed_* cell. Every Deploy cell says `HOLD`.

## Script 03 — Validate the sheet

**Purpose:** catch contradictions before touching the org. Runs in seconds; saves partial deploys.

**Checks:**
- Any `HOLD` remaining anywhere → hard fail
- Field marked `Y` under an object marked `N` → fail
- Lookup marked `Y` whose target object is neither being deployed nor already in the org → fail
- Validation rule marked `Y` referencing a field marked `N` → fail
- Master-detail marked `Y` on an object that already has records → fail
- Field API name: >40 chars, missing `__c`, reserved word, duplicate on same object → fail
- Picklist value >255 chars, or duplicate values → fail
- `Final_Type` = Text with length >255 → warn, suggest LongTextArea
- Field marked required with Fill % < 100 → warn loudly
- Owner field mapped but `_Users` tab has unmapped HubSpot owners → fail

**Checkpoint:** deliberately set a field to `Y` under an object set to `N`. Confirm the script exits non-zero with a clear message naming the tab and row.

## Script 04 — Sheet → metadata XML

**Purpose:** pure function. Same workbook in, same XML out, every time.

Emits into `force-app/main/default/`:
- `objects/{Object}/{Object}.object-meta.xml`
- `objects/{Object}/fields/{Field}__c.field-meta.xml`
- `objects/{Object}/validationRules/{Rule}.validationRule-meta.xml`
- `objects/{Object}/recordTypes/{RT}.recordType-meta.xml`
- `permissionsets/Migration_Access.permissionset-meta.xml`

**The permission set is not optional.** Fields deployed without FLS are invisible to everyone but System Administrator, and the client will report the migration as broken. Generate it automatically from every field marked `Y`.

**Checkpoint:** `sf project deploy validate --target-org client-sbx` passes with zero components deployed.

## Script 05 + 06 — Deploy and write back

`05_deploy.py`:
1. `sf project deploy validate --json` → capture `result.id`
2. Print component counts and any warnings, then **exit** — do not continue automatically
3. On a separate invocation with `--quick-deploy <id>`, run the quick deploy

`06_writeback.py` parses `result.details.componentSuccesses[]` — each entry carries `componentType`, `fullName`, and `id` (the metadata record ID: `01I…` for custom objects, `00N…` for custom fields). Match on `fullName` back to workbook rows and write:

| Column | Value |
|---|---|
| `Component_Id` | the `id` from the deploy result |
| `Setup_Link` | `=HYPERLINK("...")` built per component type |
| `Deployed_At` | timestamp |
| `Result` | Success / Failed |
| `Error_Message` | from `componentFailures[]` |

**Setup URL patterns** — resolve objects first, keep an `apiName → 01I…` map, then build field URLs from it:

```
Custom object   {instance}/lightning/setup/ObjectManager/{01I…}/Details/view
Standard object {instance}/lightning/setup/ObjectManager/Account/Details/view
Custom field    {instance}/lightning/setup/ObjectManager/{objId}/FieldsAndRelationships/{00N…}/view
Validation rule {instance}/lightning/setup/ObjectManager/{objId}/ValidationRules/{ruleId}/view
Permission set  {instance}/lightning/setup/PermSets/page?address=%2F{0PS…}
Deploy job      {instance}/lightning/setup/DeployStatus/page?address=%2Fchangemgmt%2FmonitorDeploymentsDetails.apexp%3FasyncId%3D{deployId}
```

Verify these once by clicking into your own org and comparing before you hardcode them. Salesforce has changed Setup URL shapes before, and a stale template produces a workbook full of dead links that all look correct.

**Checkpoint:** click three links in the workbook — one object, one field, one validation rule. Each lands on the right Setup page.

## Script 07 — Data load

Separate stage, separate approval. Key mechanics:

- Put a `HubSpot_Record_Id__c` **External ID, unique** field on every migrated object. This is what makes the whole load idempotent and re-runnable.
- Two passes: **records first**, then **relationships** wired up by external ID. Never try to do both in one pass.
- Use Bulk API 2.0 upsert on the external ID, so re-running never duplicates.
- Load parents before children (Account before Contact before Opportunity).
- Owner assignment comes from the `_Users` tab mapping, applied during transform, not after.

---

# Part 5 — Skills to create

Six skills. Each is `.claude/skills/<name>/SKILL.md`.

## `/fetch-hubspot`
```markdown
---
description: Fetch the full HubSpot schema and cache it to raw/
allowed-tools: Bash, Read
---
1. Activate .venv
2. Run `python scripts/01_fetch_hubspot.py`
3. Report: object count, custom object names, total property count per object
4. Flag anything unusual: calculated properties, properties with >100 picklist
   options, properties with names that collide once suffixed with __c
```

## `/build-workbook`
```markdown
---
description: Generate the reviewable migration workbook from cached HubSpot schema
allowed-tools: Bash, Read
---
1. Confirm raw/ is populated and not stale (warn if older than 7 days)
2. Run `python scripts/02_build_workbook.py`
3. Report per object: fields proposed for creation, fields mapped to standard,
   fields flagged for review, and the count still at HOLD
4. List every flagged item with the reason, so I know what to look at first
```

## `/review-check`
```markdown
---
description: Validate the reviewed workbook for contradictions before any deploy
allowed-tools: Bash, Read
---
1. Run `python scripts/03_validate_sheet.py`
2. If it fails, report each error as: tab, row, what's wrong, what to change
3. If it passes, summarise what will deploy: object counts, field counts,
   validation rules, and which are Y vs N
4. Do NOT proceed to deployment. Stop here.
```

## `/deploy-metadata`
```markdown
---
description: Validate and deploy approved workbook rows to a Salesforce org
argument-hint: [org-alias]
allowed-tools: Bash, Read
---
Target org: $ARGUMENTS (default client-sbx if not given)

1. Run `python scripts/03_validate_sheet.py` — abort on any failure
2. Run `python scripts/04_sheet_to_metadata.py`
3. Run `sf project deploy validate --target-org <org> --json`
4. Report component counts, warnings, and the validated deploy ID.
   STOP. Wait for my explicit go-ahead.
5. On go-ahead: `sf project deploy quick --job-id <id> --target-org <org> --json`
6. Run `python scripts/06_writeback.py` with the deploy JSON
7. Report: deployed count, failed count, and each failure's row and error
```

## `/load-data`
```markdown
---
description: Load HubSpot records into Salesforce via Bulk API, in dependency order
argument-hint: [org-alias] [object|all]
allowed-tools: Bash, Read
---
1. Confirm the schema deploy for the target objects has Result=Success in the workbook
2. Confirm HubSpot_Record_Id__c exists and is a unique External ID on each object
3. Extract → transform → upsert, parents before children
4. Pass 1: records. Pass 2: relationships by external ID.
5. Report per object: attempted, succeeded, failed, with a sample of each error type
6. Remind me that validation rules are still pending and should be activated now
```

## `/migration-status`
```markdown
---
description: Report current state of the migration across schema and data
allowed-tools: Bash, Read
---
Read mapping/workbook.xlsx and logs/. Report a table:
object | fields Y/N/HOLD | schema deployed | records loaded | open failures
Then list the next three actions I should take, in order.
```

---

# Part 6 — Hooks and guardrails

`CLAUDE.md` is advisory — the model follows it because it's asked to. Hooks are mandatory — they fire before the tool call and can block it. Your approval gate belongs here.

**`.claude/settings.json`**
```json
{
  "permissions": {
    "allow": [
      "Bash(python scripts/*)",
      "Bash(sf project deploy validate:*)",
      "Bash(sf org display:*)",
      "Bash(sf data query:*)"
    ],
    "deny": [
      "Bash(sf project delete:*)",
      "Bash(sf data delete:*)"
    ]
  },
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "python scripts/guard_deploy.py" }]
    }]
  }
}
```

**`scripts/guard_deploy.py`** reads the pending command from stdin and exits non-zero — blocking it — if any of these hold:

1. Command contains `deploy start` or `deploy quick`, **and** `_Approval!Status` ≠ `APPROVED`
2. Command contains `deploy start` or `deploy quick`, **and** the workbook's current content hash ≠ `_Approval!Content_Hash` → *the sheet changed after you approved it*
3. Any `Deploy` cell anywhere still reads `HOLD`
4. Target org alias contains `prod` and `MIGRATION_ALLOW_PROD=1` is not set in the environment
5. Command contains `sf data delete` or `--purge-on-delete`

Check 2 is the one that earns its keep. Long review cycles mean "I approved this" and "this is what deploys" drift apart, and nobody notices until after.

---

# Part 7 — Subagents

Two, both in `.claude/agents/`. Their job is keeping large blobs out of your main context.

**`schema-analyst.md`** — a 300-property HubSpot portal produces JSON that will bury your working context. This subagent reads `raw/`, analyses type-mapping edge cases, and returns a short list of judgement calls needed.

**`deploy-error-analyst.md`** — a failed deploy returns a large `componentFailures[]` array. This subagent parses it and returns grouped output: "6 failures — 4 are picklist value length on Deal, 2 are API name collisions on Contact — here are the workbook rows to fix."

---

# Part 8 — End-to-end delivery

This is the client-facing sequence. Roughly three to four weeks for a mid-size portal.

## Phase 0 — Discovery (2–3 days)
Run `/fetch-hubspot` against the client portal. Do not build anything yet. Produce a one-page findings note: object inventory, property counts, custom object list, and the three or four things that will need real decisions (calculated properties, pipelines, many-to-many associations, owner mapping).

**Gate:** client confirms the object inventory is complete. Missing objects discovered later invalidate the workbook.

## Phase 1 — First workbook (2–3 days)
Run `/build-workbook`. Review it yourself first, before the client sees it. Fix the type-mapping rules in `config/type_rules.yml` and regenerate, rather than fixing individual cells — otherwise your rules never improve and every future client costs the same effort.

**Gate:** you can defend every proposed mapping.

## Phase 2 — Client review (3–5 days, expect two rounds)
Send the workbook. Give them explicit instructions: edit only the `Final_*`, `Deploy`, and `Notes` columns; set `Deploy` to `Y`/`N`; leave nothing at `HOLD`.

Round two is normal. Budget for it.

**Gate:** `/review-check` passes clean, `_Approval` set to `APPROVED`, hash recorded.

## Phase 3 — Sandbox deploy (1 day)
`/deploy-metadata client-sbx`. Fix failures by editing the workbook and re-running — never by hand-patching XML. Two or three iterations is normal on the first client.

**Gate:** all `Y` rows show Result=Success, and you have clicked a sample of the Setup links.

## Phase 4 — Data load rehearsal (2–4 days)
`/load-data client-sbx all`. Then activate validation rules and confirm they don't reject the loaded data. This is where the "rules after data" ordering proves itself — if you got it wrong, you'll see it here rather than in production.

Run the load **twice**. The second run must be a clean no-op. If it creates duplicates, your external ID setup is wrong and production would have been a disaster.

**Gate:** record counts reconcile against HubSpot, spot-checked relationships are correct, second run is idempotent.

## Phase 5 — UAT (3–5 days)
Client tests in the sandbox. Changes come back as workbook edits, which means re-running Phase 3 and 4. This is why the pipeline is re-runnable — UAT feedback is cheap instead of catastrophic.

**Gate:** client sign-off in writing, referencing the workbook version and hash.

## Phase 6 — Production cutover
1. Freeze HubSpot writes (client communication, not a technical step — plan it a week ahead)
2. `sf org login web --alias client-prod`
3. Set `MIGRATION_ALLOW_PROD=1` deliberately, for this session only
4. `/deploy-metadata client-prod` — validate, review, confirm, deploy
5. `/load-data client-prod all`
6. Activate validation rules
7. Assign the generated permission set to users
8. Reconcile counts, hand over the workbook as the migration record

**Rollback:** metadata deploys are not transactional across a whole run. Your rollback is a `destructiveChanges.xml` generated from the same workbook (every `Y` row that succeeded), plus the fact that no existing data was modified — you only added. Generate the rollback manifest *before* the production deploy, not after you need it.

---

# Part 9 — Gotchas checklist

Print this. Every one of these has cost someone a day.

- [ ] Field API names: max 40 chars, `__c` suffix, no leading/trailing underscore, no double underscore
- [ ] Custom object names: max 40 chars, must have a Name field defined
- [ ] Picklist values: HubSpot's internal value ≠ its label — decide which becomes the SF API name and be consistent
- [ ] Fields deployed without FLS are invisible — the permission set is mandatory
- [ ] Master-detail cannot be added to an object that already has records
- [ ] Required fields will reject historical records with gaps — check Fill % before marking required
- [ ] Validation rules deploy last or inactive
- [ ] `hubspot_owner_id` needs a user mapping table; without it ownership silently defaults to the running user
- [ ] HubSpot calculated properties rarely map cleanly to SF formulas — flag every one for human review
- [ ] Roll-up summaries need master-detail, so they can't be created on lookup relationships
- [ ] External ID fields must be marked unique or upsert won't work
- [ ] Person Accounts, if enabled in the target org, change the Contact model entirely — check early
- [ ] HubSpot API rate limits: back off on 429, don't hammer during the fetch
- [ ] Bulk API 2.0 has daily record limits — check the org's allocation before a large load

---

# Part 10 — Build order summary

| Day | Work | Done when |
|---|---|---|
| 1 | Toolchain, auth, repo, `CLAUDE.md` | `sf org display -o client-sbx` works |
| 2 | Script 01 + `/fetch-hubspot` | `raw/` has every client object |
| 3–4 | Script 02 + `/build-workbook` | Workbook opens, sections collapse, dropdowns work |
| 5 | Script 03 + `/review-check` | Deliberate contradiction is caught |
| 6–7 | Script 04 | `deploy validate` passes, zero deployed |
| 8 | `guard_deploy.py` + hooks | Deploy blocked with a stale hash |
| 9 | Scripts 05/06 + `/deploy-metadata` | Setup links in the workbook resolve |
| 10–12 | Script 07 + `/load-data` | Second run is a clean no-op |
| 13 | Subagents, `/migration-status` | Failed deploy returns a short summary |

Two weeks of build, then Phase 0 with the client. Second client is roughly a third of the effort, which is the point of building it this way rather than doing it by hand.