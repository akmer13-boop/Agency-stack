# Stage 4.7B1 — Reversible Soft Tombstone Foundation

Status: accepted locally; pending GitHub review.

## Why this stage exists

Stage 4.7A found 50 local CRM entities that were absent from an authoritative
full Bitrix24 sync. Independent read-only verification then confirmed all 50:

- 27 deals: direct `crm.deal.get` returned Not found;
- 7 contacts: direct `crm.contact.get` returned Not found;
- 16 activities: exact-ID `crm.activity.list` returned an empty list after a
  positive control check on an existing activity;
- unresolved verification results: 0.

Stage 4.7B is intentionally split. B1 builds the reversible storage/read model.
It does **not** activate the 50 tombstones.

## Storage model

Raw CRM payload remains unchanged in `crm_raw_entities`.

A new additive table stores soft tombstones:

`crm_entity_tombstones`

Each row records:

- entity type;
- entity ID;
- tombstoned timestamp;
- source authoritative audit run ID;
- evidence kind;
- evidence verification timestamp.

Only current-entity types are tombstone-enabled:

- deal;
- lead;
- contact;
- company;
- activity.

Stage-history and directory rows are never tombstoned by this mechanism.

## Active view

A new view:

`crm_active_entities`

contains all raw CRM rows except rows with a matching soft tombstone.

Current-state semantic/ROP analytics read the active view instead of the raw
table. Raw data remains available for audit, reconciliation and historical
forensics.

## Automatic revival

If a tombstoned entity later appears again in a Bitrix sync, normal UPSERT:

1. refreshes the raw payload;
2. removes the matching tombstone in the same local transaction;
3. makes the entity visible in `crm_active_entities` again.

This keeps tombstones reversible without manual cleanup.

## Safety

Stage 4.7B1:

- physically deletes NO CRM raw payload;
- applies NO live tombstones;
- performs NO Bitrix24 write;
- changes NO business SLA;
- keeps stage history intact;
- uses an additive SQLite schema migration only.

## Next

Stage 4.7B2 will implement admin-only live activation. It must revalidate the
latest authoritative absence candidates immediately before applying tombstones
and abort the entire activation if any candidate exists, is inaccessible or
cannot be verified.

## Local validation evidence

Validated on the Stage 4.7B1 branch before publication:

- Python compile: PASS;
- Ruff: PASS;
- targeted tombstone/reconciliation/sync/semantic tests: PASS;
- full pytest: 171 passed;
- `git diff --check`: PASS;
- strict Git scope: PASS;
- additive tombstone schema: PASS;
- active CRM view coverage: PASS;
- automatic revival: PASS;
- physical delete from `crm_raw_entities`: NONE;
- live tombstones applied: ZERO;
- Bitrix24 CRM write: NONE.

Publication version: Agency Stack `0.4.24`.
