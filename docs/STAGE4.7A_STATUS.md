# Stage 4.7A — Deleted Entity Reconciliation Evidence

Status: accepted locally, pending GitHub review.

## Goal

Detect local CRM rows that were not observed in a completed full Bitrix24 read
without deleting, hiding or tombstoning them yet.

The problem addressed by this stage:

- normal sync uses UPSERT;
- an entity deleted from Bitrix24 may therefore remain in local SQLite;
- analytics could keep treating that stale local row as present.

## Fail-closed contract

Reconciliation is authoritative only when:

1. sync mode is `full`;
2. `BITRIX24_SYNC_MAX_ITEMS_PER_ENTITY=0` (unlimited);
3. all reconcilable entity types were traversed successfully;
4. IDs observed from Bitrix were actually persisted locally;
5. the full sync itself completed without Bitrix pagination/request errors.

Reconcilable entity types:

- deal;
- lead;
- contact;
- company;
- activity.

Stage history is intentionally excluded because it is historical evidence, not
current-entity presence. User/department directory is also excluded because
visibility/deactivation semantics differ from CRM entity deletion.

## Dry-run evidence

After a successful full sync, the system compares:

`local entity IDs - observed full-sync IDs`

The result is stored locally as:

`bitrix_reconciliation_audit.json`

By default the audit file is placed next to `DATABASE_PATH`. Runtime can
override it with `BITRIX24_RECONCILIATION_AUDIT_PATH`.

These IDs are called **absence candidates**, not deleted entities.

The audit stores only technical entity type + CRM IDs and counts. It does not
store additional CRM payload or message content.

## Important safety rule

Stage 4.7A NEVER:

- deletes a row from `crm_raw_entities`;
- marks a row deleted;
- hides a row from analytics;
- writes to Bitrix24;
- treats one missing observation as proof of deletion.

Incremental sync does not update the authoritative absence audit.

## Interfaces

- full `/bitrix_sync` automatically generates a dry-run audit;
- `/bitrix_reconciliation_status` shows the latest audit;
- full sync output includes reconciliation status and absence-candidate counts.

## Next

Stage 4.7B should only activate tombstones after real CRM validation. A safe
policy should require stronger evidence than one absence observation, for
example repeated authoritative full-sync absence and/or read-only direct entity
verification before changing analytics visibility.

## Local validation

Validated on Mac against the synchronized Agency Stack runtime:

- Python compile: PASS;
- Ruff: PASS;
- targeted Stage 4.7A tests: 20 passed;
- full pytest: 164 passed;
- `git diff --check`: PASS;
- no delete/tombstone behavior;
- analytics row hiding: NONE;
- DB migrations: NONE;
- CRM write: NONE.

Stage 4.7A remains DRY RUN only. Absence candidates are evidence, not proof
of deletion.
