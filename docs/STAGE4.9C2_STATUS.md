# Stage 4.9C2 — Conversation Read Model

## Goal

Materialize a deterministic human-conversation layer above raw Bitrix24 Open Lines messages.

## Ordering

Human messages are ordered by the original numeric Bitrix `message_id`.

## Human scope

Only `client` and `manager`. System and bot messages remain in the raw Open Lines layer and are
excluded from human turns.

## Turn boundary

A new turn starts when either the role changes or the concrete sender changes. A manager handoff
is therefore preserved even if two manager messages are consecutive.

## Materialized objects

- `conversation_threads`
- `conversation_turns`
- `conversation_turn_messages`
- `conversation_thread_crm_links` view

No primary CRM entity is invented; every existing CRM link remains available.

## Non-goals

- No SLA thresholds.
- No response-quality scoring.
- No AI analysis.
- No business-rule assumptions.
- No Bitrix writes.

## Next

Stage 4.9D will derive factual communication metrics from this read model.
