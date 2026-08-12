# Stage 4.7B2 — Admin-only Live Soft Tombstone Activation

Status: accepted locally; pending GitHub review.

## Purpose

Stage 4.7B1 created reversible tombstone storage and `crm_active_entities`.
Stage 4.7B2 adds the guarded production activation path.

The activation command is intentionally admin-only and requires an explicit
`CONFIRM` argument.

## Activation sequence

`/bitrix_tombstone_activate CONFIRM`

runs under the existing Bitrix sync lock:

1. perform a fresh full unlimited read-only Bitrix24 sync;
2. require a `complete`, authoritative reconciliation audit from that exact run;
3. take that run's absence candidates;
4. reverify every candidate immediately before activation:
   - deal / lead / contact / company: direct read-only `*.get`;
   - activity: exact-ID `crm.activity.list` after a positive existing-activity
     control check, because this on-premise Bitrix installation returns a blank
     HTTP 400 body for missing `crm.activity.get`;
5. if any result is EXISTS, ACCESS_DENIED, ERROR or anomalous, abort the whole
   activation before any tombstone is written;
6. if every candidate is independently confirmed missing, write all soft
   tombstones in one SQLite transaction;
7. verify raw CRM counts did not change and active counts decreased only by
   newly applied tombstones.

## Safety properties

- no Bitrix24 write methods are added;
- raw payload rows are never physically deleted;
- stage history is not tombstone-enabled;
- activation is all-or-nothing for the candidate batch;
- existing tombstones are idempotent;
- reappearing entities are revived automatically by normal sync UPSERT;
- status can be inspected with `/bitrix_tombstone_status`;
- a command without literal `CONFIRM` does not activate anything.

## Live activation

The Stage 4.7B2 development/validation runner does **not** execute the live
activation command and requires live tombstone counts to remain unchanged.

Live activation is performed only after this stage is reviewed and merged.

## Local validation evidence

Validated before publication:

- reconciliation DRY RUN formatter contract: PASS;
- Python compile: PASS;
- Ruff: PASS;
- targeted Stage 4.7B2 tests: PASS;
- full pytest: PASS;
- `git diff --check`: PASS;
- strict Git scope: PASS;
- admin-only activation: PASS;
- literal `CONFIRM` gate: PASS;
- fresh full unlimited sync before activation: REQUIRED;
- per-candidate live read-only verification: PASS;
- fail-closed whole-batch behavior: PASS;
- activity positive-control verification: PASS;
- atomic SQLite soft-tombstone application: PASS;
- physical delete from `crm_raw_entities`: NONE;
- live tombstones applied during development: ZERO;
- Bitrix24 CRM write: NONE.

Publication version: Agency Stack `0.4.25`.

Live activation remains intentionally **not executed** by this stage's
development/publish workflow. It is a separate post-merge production action.
