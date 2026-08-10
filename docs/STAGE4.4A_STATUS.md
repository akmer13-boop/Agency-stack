# Stage 4.4A — First Response Policy Contract

Status: accepted locally, pending GitHub review.

## Goal

Prepare a configurable and fail-closed business-rule layer for First Response
without inventing customer policy.

Stage 4.3 remains the observed evidence layer. Stage 4.4A adds only policy
readiness and validation.

## Configuration

The runtime receives these optional settings:

- `ROP_FIRST_RESPONSE_POLICY_ENABLED`
- `ROP_FIRST_RESPONSE_TIMER_START`
- `ROP_FIRST_RESPONSE_EVENT`
- `ROP_FIRST_RESPONSE_CLOCK`
- `ROP_FIRST_RESPONSE_REASSIGNMENT_MODE`
- `ROP_FIRST_RESPONSE_THRESHOLD_SECONDS`

All policy fields are empty/zero by default and the policy is disabled.

## States

- `DISABLED` — policy is intentionally off.
- `BLOCKED` — enabled, but required values are missing or unsupported.
- `READY` — every configured value is supported by the current code.

`READY` does not mean SLA compliance is being calculated.

## Currently supported technical choices

- timer start: `lead_created`
- response event: `manager_evidence` or `confirmed_communication`
- clock: `calendar_elapsed`
- reassignment mode: `not_attributed`
- threshold: positive integer seconds

These are capabilities of the code, not customer-approved defaults.

## Explicitly unsupported

- business-hours calendar;
- holidays/weekends exclusion;
- historical reassignment attribution;
- automatic threshold selection from Stage 4.3 baselines;
- SLA compliance / pass / fail / overdue calculation.

## Tool

`get_rop_first_response_policy()` returns policy readiness and blockers.

## Safety

- fail closed;
- no CRM writes;
- no DB migration;
- no default SLA threshold;
- no LLM calculation;
- no inferred customer policy.

## Next

Stage 4.4B starts only after the customer approves the actual policy values or
after a required unsupported capability is implemented.

## Local runtime validation

Validated on the current local runtime after implementation:

- policy contract: available;
- default runtime state: `DISABLED`;
- default blocker: `policy_disabled`;
- no customer threshold inferred;
- no First Response compliance calculation enabled;
- Ruff: PASS;
- full pytest: PASS;
- DB migrations: NONE;
- CRM write: NONE.

The production/customer policy remains intentionally unconfigured until the
business decisions are formally approved.
