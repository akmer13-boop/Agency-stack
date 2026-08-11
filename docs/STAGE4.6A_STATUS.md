# Stage 4.6A — ROP Scheduler Executor

Status: accepted locally, pending GitHub review.

## Goal

Close the missing scheduler foundation without changing business metrics or
inventing report times.

The existing deterministic `/rop_daily` report remains the daily report source.
The existing deterministic weekly analytics remains the weekly report source.

## Runtime configuration

All scheduling is opt-in and fail-closed:

- `ROP_SCHEDULER_ENABLED`
- `ROP_SCHEDULER_DAILY_ENABLED`
- `ROP_SCHEDULER_DAILY_TIME`
- `ROP_SCHEDULER_WEEKLY_ENABLED`
- `ROP_SCHEDULER_WEEKLY_DAY`
- `ROP_SCHEDULER_WEEKLY_TIME`
- `ROP_SCHEDULER_RECIPIENT_IDS`
- `ROP_SCHEDULER_POLL_SECONDS`
- `ROP_SCHEDULER_STATE_PATH`

No time, weekday or recipient is supplied as a business default.

## Safety states

- `DISABLED` — scheduler is globally off.
- `BLOCKED` — enabled but missing/invalid configuration or recipient is not an
  ADMIN/MANAGER/OBSERVER.
- `READY` — at least one job is fully configured and every recipient has a
  ROP-capable Telegram role.

## Delivery

The Telegram bot starts the scheduler alongside polling.

Daily report:
- due after the configured local `HH:MM` on the current date.

Weekly report:
- due after the configured weekday + `HH:MM`;
- if the process starts later in the same ISO week, the weekly report catches up.

## Durable duplicate protection

Each successful delivery is persisted in a local JSON ledger:

`data/rop_scheduler_state.json`

Key scope: `job + period + recipient`.

The `data/` directory is excluded from Git. No CRM or analytics tables are
modified.

## Local validation

Validated against the synchronized Agency Stack runtime:

- Ruff: PASS;
- targeted scheduler tests: PASS;
- full pytest: PASS;
- current runtime scheduler state: `DISABLED`;
- configured recipients: 0;
- configured jobs: none;
- blocker: `scheduler_disabled`;
- `git diff --check`: PASS;
- DB migrations: NONE;
- CRM write: NONE.

## Explicit limitations

- one active Telegram scheduler process is assumed;
- no distributed lock for multi-instance HA yet;
- failed recipient delivery is retried on a later tick;
- partial Telegram chunk delivery may repeat for that recipient on retry;
- schedule configuration changes require process restart;
- report content remains the existing deterministic read-only analytics.

## Safety

- default scheduler state: DISABLED;
- no CRM write;
- no DB migration;
- no customer SLA introduced;
- no OpenAI/LLM required for scheduled report generation;
- recipients must already have a ROP Telegram role.

## Next

Stage 4.6B can add scheduler observability / last-run health and, if needed,
a distributed execution lock for multi-instance deployment.
