# Stage 4.9D2 — Factual Communication Metrics

## Goal

Materialize deterministic communication facts above `conversation_threads` and
`conversation_turns`.

## Response interval

A response interval is stored only when adjacent human turns change role:

- client → manager
- manager → client

`wait_seconds` is measured from the end of the previous human turn to the start of the next
human turn.

## First manager response

The first manager response is the first manager turn that occurs after the first client turn in
the chat. Its wait is measured from the end of that first client turn to the start of the manager
turn.

No SLA or business threshold is applied.

## Materialized tables

### `conversation_response_intervals`

Exact factual transition rows:

- source/target turn;
- source/target role and actor;
- manager user ID;
- source/target message IDs;
- timestamps;
- waiting seconds;
- first-manager-response flag.

### `conversation_thread_metrics`

One factual row per conversation thread:

- conversation duration;
- first client turn;
- first manager response turn/user;
- first-response seconds;
- initial client without later manager response flag;
- client→manager / manager→client interval counts;
- manager handoff count;
- last human role;
- client-tail-after-dialogue;
- distinct manager count.

### `conversation_factual_metrics_by_manager`

Convenience view for factual manager response counts and simple response-time aggregates.

## Explicit non-goals

- no SLA breach flag;
- no good/bad rating;
- no business-hours adjustment;
- no AI scoring;
- no Bitrix writes.

## Next

Stage 4.9D3 will audit manager/channel/CRM aggregation and response evidence before these facts are
wired into ROP reports.
