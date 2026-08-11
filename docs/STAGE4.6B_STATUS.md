# Stage 4.6B — Scheduler Observability / Last-Run Health

Status: accepted locally, pending GitHub review.

## Goal

Make the Stage 4.6A scheduler operationally observable without changing report
business logic or enabling automatic delivery.

## Runtime health

A local fail-safe health state is stored in:

`data/rop_scheduler_health.json`

The state contains only operational metadata:

- process start time;
- last tick start / completion;
- last tick status;
- due / delivered / failed counters;
- last successful delivery time;
- last technical error time / code;
- consecutive failure count.

No CRM payload, message text, employee PII or Telegram message contents are
stored in this health state.

## Health statuses

- `DISABLED` — scheduler is disabled by configuration;
- `BLOCKED` — scheduler configuration is incomplete/invalid;
- `NOT_STARTED` — READY config exists but runtime state does not;
- `STARTING` — process started but first tick has not completed;
- `HEALTHY` — last tick is fresh and successful;
- `DEGRADED` — last tick failed or had partial delivery failures;
- `STALE` — last completed tick is older than the technical freshness window;
- `UNAVAILABLE` — health state is unreadable/invalid.

The freshness window is technical and derived from scheduler polling:

`max(3 × poll interval, 60 seconds)`

It is not a business SLA.

## Interfaces

- Telegram: `/rop_scheduler_health`
- API `/health` exposes scheduler state, health, last tick timestamp and
  consecutive failures.

## Structured logging

Scheduler-specific fields are now preserved by the JSON logger, including:

- state;
- job / period;
- recipient id;
- due / delivered / failed;
- startup job/recipient counts and blockers;
- health write operation / health status.

## Safety

- scheduler remains `DISABLED` by default;
- no report schedule is invented;
- no recipients are invented;
- CRM write: NONE;
- DB migrations: NONE;
- customer SLA: NONE;
- manager performance interpretation: NONE;
- distributed execution lock: NOT IMPLEMENTED.

## Next

After validation and merge, the scheduler gap can remain PARTIAL until real
runtime activation is approved. Multi-instance distributed locking should only
be added if deployment topology actually requires more than one active scheduler.

## Local validation

Validated on Mac against the synchronized Agency Stack runtime:

- Ruff: PASS;
- targeted Stage 4.6B tests: PASS;
- full pytest: PASS;
- current runtime scheduler health: `DISABLED`;
- `git diff --check`: PASS;
- DB migrations: NONE;
- CRM write: NONE.

The scheduler remains fail-closed and no schedule, recipient or business SLA
is introduced by this stage.
