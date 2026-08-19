# Stage 4.11A5A - Exact Realtime Call Evidence Bridge

Status: LOCAL / NOT COMMITTED

Purpose:
convert materialized Bitrix realtime telephony events into
ExactCallResponse evidence accepted by the existing ROP
first-response evidence adapter.

Input:
- bitrix_event_inbox
- bitrix_call_evidence

Successful live call requires:
- ONVOXIMPLANTCALLSTART;
- ONVOXIMPLANTCALLEND;
- CALL_FAILED_CODE = 200;
- CALL_TYPE in {1, 2};
- valid directory user id;
- CRM entity link or CRM activity link.

Exact response timestamp:
event_ts from ONVOXIMPLANTCALLSTART.

The bridge fails closed when:
- successful end exists without exact start;
- call type is absent/invalid;
- start/end manager ids conflict;
- CRM linkage is missing;
- required local evidence tables are missing.

Failed calls are observed but never become ExactCallResponse.

Important:
this stage does NOT claim source completeness and does NOT
calculate First Response SLA yet.

That is intentional:
strict SLA requires confidence that all response sources are
complete for the evaluated time window.

Safety:
- SQLite read-only bridge
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production callback activation
- no commit/push

Next:
Stage 4.11A5B:
source coverage/completeness contract + First Response SLA
evaluation trigger.
