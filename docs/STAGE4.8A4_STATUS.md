# Stage 4.8A4 — Data Gap Diagnostics

Status: locally validated; pending GitHub review.

## Purpose

Turn descriptive coverage gaps into exact, actionable diagnostics without
guessing business importance and without changing CRM data.

## Scope

The stage identifies exact active local CRM IDs behind missing evidence for:

- deal assignment, timestamps, stage, stage semantic and currency;
- deal stage-history coverage;
- lead assignment, timestamps, status, status semantic and source;
- lead stage-history coverage;
- sales activity owner, responsible user and observed timestamp;
- manager IDs that are referenced by sales data but are absent from the
  current local Bitrix user directory.

For unmapped manager IDs, the report also counts how many current deals,
leads and sales activities reference each ID.

## Safety contract

- current `crm_active_entities` only;
- tombstoned entities stay excluded;
- no client names, contacts or free-text communication content in output;
- missing manager directory mapping is not treated as proof of deletion;
- no invented replacement values;
- no automatic data repair;
- no Bitrix24 write;
- no SQLite schema migration;
- no business quality threshold.

## Deliverables

- `rop_data_gap_diagnostics.py`;
- AI tool `get_rop_data_gap_diagnostics`;
- agent routing/guardrails;
- tests for exact IDs, tombstone exclusion and manager impact;
- live read-only smoke against current CRM.

## Next

Use the diagnostics to clean small source/reference gaps, then re-run
Fact Quality & Coverage. Business KPI binding remains blocked until the
relevant business policies are formally approved.

## Local validation evidence

Validated on Windows before publication:

- Python compile: PASS;
- Ruff: PASS;
- targeted tests: PASS;
- full pytest: PASS;
- live read-only diagnostics smoke: PASS;
- exact gap IDs: REPORTED;
- manager reference impact: REPORTED;
- client text / contacts: EXCLUDED;
- automatic repair: NONE;
- business verdict: NONE;
- Bitrix24 CRM write: NONE;
- SQLite schema migration: NONE;
- `git diff --check`: PASS;
- strict Git scope: PASS.

Observed Windows local snapshot during validation:

- deal stage-history gaps: IDs `10328`, `10350`, `10361`;
- lead source gaps: IDs `10988`, `11024`, `11043`, `11066`;
- unmapped responsible IDs: `102`, `125`, `484`, `7912`;
- responsible ID `7912` had 3298 current references in that local snapshot.

These are descriptive local-snapshot diagnostics only. They are not a
business verdict and may change after the next CRM synchronization.

Publication version: Agency Stack `0.4.29`.
