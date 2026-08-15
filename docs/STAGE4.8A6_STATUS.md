# Stage 4.8A6 — Human Attribution Guard

Status: in progress.

Base: Agency Stack 0.4.30.

## Goal

Prevent technical or unresolved CRM actors from being presented as human managers.

## A6.1 — Management Facts

Current step implements the guard in deterministic Management Facts:

- team-level deal, lead and activity totals remain unchanged;
- only `directory_user` actors are emitted in `HUMAN MANAGER FACTS`;
- `special_actor_candidate` and `unresolved_actor` remain visible in a separate
  `NON-HUMAN / UNRESOLVED ATTRIBUTION — NOT MANAGERS` section;
- no actor is deleted from source facts;
- no manager rating, SLA verdict or performance conclusion is calculated.

## Safety

- read-only local analytics;
- no Bitrix24 write;
- no new Bitrix24 scope;
- no schema migration;
- no hardcoded actor ID classification;
- Actor Resolution remains the source of identity-type evidence.

## Planned next checks

A6.2 will apply the same human-attribution rule to Lead Intelligence and
Weekend Leads only after A6.1 passes local validation.

## A6.2 — Lead Intelligence + Weekend Leads

- Lead Intelligence personal manager rows: DIRECTORY_USER only.
- Weekend Leads personal manager rows: DIRECTORY_USER only.
- Non-directory attribution is preserved in team/cohort totals and shown separately.
- Special/unresolved/non-directory actors are not presented as human managers.
- Heavy `ORDER BY CAST(entity_id AS INTEGER)` temp sorts were removed only from
  Lead Intelligence and Weekend Leads loaders.
- Response Evidence, Response Trend, Daily and Deep Analytics are intentionally
  unchanged in A6.2.
- CRM write: none.
- New Bitrix scope: none.
- Schema migration: none.
- Commit/push: not performed by validation script.

## A6.3 — Response Evidence + Trend + Deep Analytics + Daily

- Response Evidence manager-side evidence is restricted to DIRECTORY_USER RESPONSIBLE_ID.
- Confirmed communication and lead-cohort totals remain cohort facts and are preserved.
- Response Trend uses the same human manager evidence scope; excluded non-directory
  earliest evidence is surfaced per cohort.
- Deep Analytics manager/responsible breakdowns are DIRECTORY_USER only; excluded
  attribution is shown separately and aggregate loss/stage totals are preserved.
- Daily Brief human manager prioritization is DIRECTORY_USER only; special/unresolved
  actors remain in deal/team facts but are excluded from the human-manager intervention list.
- Heavy `ORDER BY CAST(entity_id AS INTEGER)` was removed from Deep Analytics loader.
- CRM write: none.
- New Bitrix scope: none.
- Schema migration: none.
- Commit/push: not performed by validation script.

## Publish readiness — 0.4.31

- A6.1 Management Facts human-attribution guard: PASS.
- A6.2 Lead Intelligence + Weekend Leads human-attribution guard: PASS.
- A6.3 Response Evidence/Trend + Deep Analytics + Daily guard: PASS.
- A6.4 final cross-report attribution audit: PASS.
- Human manager scope: DIRECTORY_USER only.
- Special/unresolved actor leakage into human manager rows: none.
- Team/cohort/deal totals: preserved.
- Confirmed communication totals: preserved.
- Excluded attribution: explicit.
- Heavy CRM temp sorts removed in A6-touched reports.
- CRM write: none.
- New Bitrix scope: none.
- Schema migration: none.

