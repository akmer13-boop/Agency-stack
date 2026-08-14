# Stage 4.8A5 — Actor Resolution Layer

Status: locally validated; pending GitHub review.

## Purpose

Stop treating every Bitrix24 `ASSIGNED_BY_ID` / `RESPONSIBLE_ID` as a confirmed
human manager.

Stage 4.8A5 introduces a deterministic, read-only identity-type layer for
actor IDs observed in current sales CRM facts.

## Actor kinds

- `directory_user` — the ID exists in the synchronized Bitrix24 user directory.
  This proves directory presence only; it does **not** by itself prove a sales
  manager role.
- `special_actor_candidate` — the ID is absent from the user directory but has
  a conservative high-confidence Open Lines technical signature in current
  local CRM evidence.
- `unresolved_actor` — the identity type cannot be established from current
  evidence.

## Technical signals

Signals are descriptive evidence, not identity verdicts:

- `openlines_related`
- `openlines_self_authored`
- `open_channel_lead_creator`
- `telephony_related`
- `crm_todo_related`

Only strong Open Lines evidence may promote an absent ID to
`special_actor_candidate`. Telephony or CRM TODO evidence alone remains
`unresolved_actor`.

## Changes

- new deterministic `rop_actor_resolution` service;
- new AI tool `get_rop_actor_resolution`;
- fact-quality coverage uses `observed_actor_id.resolution`;
- exact data-gap diagnostics report unresolved actors, not "missing managers";
- business-policy data dependencies use actor resolution rather than raw
  directory mapping;
- management facts expose actor kind and technical signals;
- agent guardrails prohibit treating technical/unresolved actors as confirmed
  human managers.

## Safety

- no hardcoded actor IDs;
- no `im` scope required;
- no new Bitrix24 methods;
- no CRM write;
- no SQLite schema migration;
- no automatic user repair;
- no client text, contacts or message contents in actor tool output;
- `special_actor_candidate` is **not** a confirmed bot/system account;
- `unresolved_actor` is **not** proof of deleted/inactive/fired user.

## Expected current-data interpretation

The implementation is generic. On the current local dataset it is expected to
recognize the previously observed Open Lines signature behind ID `7912` without
hardcoding that ID. Other non-directory IDs remain unresolved unless the same
generic evidence rules are satisfied.

## Local validation evidence

Validated on Windows before publication:

- Python compile: PASS;
- Ruff: PASS;
- targeted tests: PASS;
- full pytest: 194 PASS;
- live actor-resolution smoke: PASS;
- active local CRM source: YES;
- observed actor IDs: 91;
- directory users: 87;
- special actor candidates: 1;
- unresolved actors: 3;
- actor identity-type resolution coverage: 88/91;
- no hardcoded actor IDs;
- new Bitrix24 scope: NONE;
- Bitrix24 CRM write: NONE;
- SQLite schema migration: NONE;
- `git diff --check`: PASS;
- strict Git scope: PASS.

Observed local snapshot during validation:

- ID `7912`: `special_actor_candidate`; 3314 current references;
  signals `openlines_related`, `openlines_self_authored`,
  `open_channel_lead_creator`, `crm_todo_related`;
- ID `102`: `unresolved_actor`; `crm_todo_related`;
- ID `125`: `unresolved_actor`; no technical signal established;
- ID `484`: `unresolved_actor`; `telephony_related`.

These classifications are deterministic descriptions of the current local CRM
evidence. `special_actor_candidate` is not a confirmed bot/system identity,
and `unresolved_actor` is not proof of deletion, inactivity or dismissal.

Publication version: Agency Stack `0.4.30`.

## Large local CRM safety

Actor raw-payload scans do not sort by entity ID because actor aggregation does not depend on row order. Rows are consumed from SQLite as a stream, avoiding the large temporary sort file that can be created by `ORDER BY CAST(entity_id AS INTEGER)` on large activity datasets.
