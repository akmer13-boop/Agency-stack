# Stage 4.11A6B - Deadline / Recheck Sweep

Status: LOCAL / NOT COMMITTED

Purpose:
re-evaluate time-based Stage SLA transitions even when no new
Bitrix event arrives.

Why:
an event-driven system can evaluate a deal as OPEN before its
deadline, but the deadline can later pass without any new CRM
event.

A6B closes that gap.

Current sweep scope:
- policy profile: tourism_b2c
- entity: deal
- rule: stale_deal
- latest deterministic evaluation only
- current verdict must be OPEN
- deadline must be due

The sweep intentionally does not process unresolved leads.

First Response SLA remains scope-blocked until the project has
a verified lead -> business-profile mapping that distinguishes
Tourism from Concierge and other business lines.

Supersession:
if a newer CRM event produces a newer SLA evaluation, the old
deadline candidate is ignored.

This prevents an old stage deadline from firing after a
qualifying activity has reset the timer.

Coverage:
the normal realtime Stage SLA evaluator remains the source of
truth.

Therefore a source-coverage gap at the deadline produces
BLOCKED rather than a false ATTENTION result.

Persistence:
- rop_sla_deadline_sweep

Idempotency:
a completed source evaluation is swept at most once.

Retries:
technical failed sweep attempts can retry up to a bounded max.

No trigger callback payload is copied into sweep evaluation
JSON.

Safety:
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production callback activation
- no Concierge SLA assumptions
- no automatic lead scoring
- no commit/push

Next:
Stage 4.11A6C - operational coverage watermark / safe sweep
runner contract, then pre-commit audit of A6.
