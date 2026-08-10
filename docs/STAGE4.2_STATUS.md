# Stage 4.2 — Deterministic CRM Activity Classification

Status: accepted locally on real CRM, pending GitHub review.

## Goal

Stop treating every CRM activity as equivalent evidence of manager work.

The classifier is deterministic and conservative. The LLM does not decide the
activity class.

## Evidence classes

- `confirmed_communication`
- `human_action`
- `system_activity`
- `unknown`

## Rules

`confirmed_communication` is a completed standard meeting/call/e-mail.

`human_action` is a completed Bitrix User Action (`TYPE_ID=6`) unless stronger
system evidence applies.

`system_activity` is a completed non-communication activity with a positive
`AUTOCOMPLETE_RULE`.

Everything without enough evidence stays `unknown`.

## Manager-side evidence

This is a derived flag rather than a fifth class. It is true for:

- completed User Action;
- completed outgoing call/e-mail (`DIRECTION=2`) without autocomplete evidence.

Incoming call/e-mail remains communication evidence but is not automatically
credited as manager-side action. Meetings remain confirmed communication but are
not credited to a manager until the business rule is approved. Missing direction
is not guessed.

## Integration

The evidence split is added to:

- weekend lead processing;
- rolling Lead Intelligence activity statistics.

The existing confirmed-communication count is parity-tested against the legacy
rule.

## Safety

- no CRM write;
- no DB migration;
- no new DB tables;
- no LLM calculation;
- ambiguous activity stays `unknown`.

## Next

Stage 4.3 — review the real CRM classification inventory and prepare a
deterministic first-response evidence contract without declaring an SLA until the
customer approves the SLA definition.

## Real CRM validation

Validated on local synchronized Bitrix24 SQLite:

- lead activities: 65,400;
- mapping errors: 0;
- confirmed communication: 20,070;
- human action: 20,421;
- system activity: 24,270;
- unknown: 639;
- manager-side evidence events: 30,544;
- legacy confirmed communications: 20,070;
- new confirmed communications: 20,070;
- confirmed-communication parity: PASS.

Most important observed pattern:

- 24,270 completed `TYPE_ID=6` activities had `AUTOCOMPLETE_RULE=1`
  and are therefore separated as system activity instead of being credited as
  manager-side work.

The classification changes interpretation of CRM evidence without changing the
legacy confirmed-communication count.
