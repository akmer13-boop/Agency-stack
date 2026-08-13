# Stage 4.8A2 — Management Fact Quality & Coverage

Status: locally validated; pending GitHub review.

## Goal

Measure whether the synchronized active CRM contains the source fields and evidence needed by future management metrics, without inventing any business acceptance thresholds.

## Coverage measured

- deal responsible, creation/update timestamps, stage identifiers/semantics, currency;
- lead responsible, creation/update timestamps, status identifiers/semantics, source;
- current deal/lead IDs with at least one stored stage-history event;
- sales activity owner ID, responsible ID and conservative observed timestamp;
- observed manager IDs that resolve to the local Bitrix user directory;
- conservative activity evidence-class distribution.

## Explicit guardrails

- descriptive counts and percentages only;
- no `good/bad`, `reliable/unreliable` or minimum acceptable percentage;
- business-policy readiness remains separate from data coverage;
- source is `crm_active_entities` through the semantic layer;
- no CRM write;
- no SQLite schema change;
- no automatic repair/backfill;
- tombstoned entities stay excluded automatically through the active view.

## AI tool

`get_rop_fact_quality()` returns the objective coverage report for questions about data completeness, missing evidence, or whether the source data exists for a future metric.

Development version remains `0.4.26`. Publish will bump only after validation.

## Local validation evidence

Validated before publication on the live local CRM snapshot:

- Python compile: PASS;
- Ruff: PASS;
- targeted Stage 4.8A2 tests: PASS;
- full pytest: 184 passed;
- live read-only coverage smoke: PASS;
- active CRM source: YES;
- coverage thresholds: NONE;
- business quality verdict: NONE;
- automatic data repair: NONE;
- Bitrix24 CRM write: NONE;
- SQLite schema migration: NONE;
- `git diff --check`: PASS;
- strict Git scope: PASS.

Observed live coverage evidence:

- deals: 8241;
- leads: 18714;
- sales activities linked to lead/deal: 116321;
- deal assigned user coverage: 8241/8241;
- deal created/updated/stage/semantic/currency coverage: 8241/8241;
- deal stage-history owner match: 8241/8241;
- lead assigned/created/updated/status/semantic coverage: 18714/18714;
- lead source coverage: 18710/18714, missing 4;
- lead stage-history owner match: 18714/18714;
- sales activity owner/responsible/timestamp coverage: 116321/116321;
- observed manager ID directory mapping: 86/90, missing 4.

These percentages are descriptive evidence only. Stage 4.8A2 does not
define an acceptable threshold or automatically repair missing data.

Publication version: Agency Stack `0.4.27`.
