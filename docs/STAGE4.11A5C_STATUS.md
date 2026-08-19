# Stage 4.11A5C - Realtime Deal Stage SLA

Status: LOCAL / NOT COMMITTED

Purpose:
connect current deal stage, realtime source coverage and
qualifying manager activity to the existing deterministic
stage timer.

Strict source coverage required:
- crm_realtime
- openlines
- voximplant_realtime

Evaluation window:
current stage entry -> as_of.

Stage-entry evidence:
1. latest matching deal_stage_history event;
2. fallback to current deal MOVED_TIME.

Qualifying activity materialized without customer text:
- successful outgoing call -> outbound_call
- successful incoming call -> inbound_call
- human Open Lines manager message -> message_to_client
- completed outgoing CRM e-mail -> message_to_client

Successful telephony calls still require exact CallStart
evidence. A successful CallEnd without valid CallStart fails
closed because it could otherwise incorrectly produce an
attention verdict.

Unspecified stages remain NOT_APPLICABLE through the existing
policy engine.

Potential Client remains BLOCKED until the return-to-client
field is bound.

This stage does not invent a separate BREACH status.
The existing stage timer currently returns:
- OPEN while elapsed business time is below threshold;
- ATTENTION when elapsed business time reaches/exceeds
  threshold.

All business-time thresholds remain sourced from the existing
approved policy registry.

Safety:
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production callback activation
- no customer message text analysis
- no commit/push

Next:
pre-commit audit of Stage 4.11A5A-A5C.
