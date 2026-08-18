# Stage 4.10G2 - Response Evidence Source Adapter

Status: LOCAL / NOT COMMITTED

Business response evidence:

Exact First Response may be:
1. human manager Open Lines message;
2. successful phone conversation start.

Phone outcome:
- CALL_FAILED_CODE=200 is a successful call;
- other completion codes do not satisfy First Response;
- CALL_DURATION is supporting evidence only;
- zero CALL_DURATION does not override successful code 200.

Call timing:
voximplant.statistic.get proves the successful call but
CALL_START_DATE is retained only as call-start timing.

For strict First Response SLA, exact conversation-start
evidence is produced by OnVoximplantCallStart and joined by
CALL_ID to the successful statistic record.

Historical successful statistics without an exact
conversation-start event are NOT assigned an invented answer
timestamp.

Completeness guard:
strict First Response SLA is produced only when both:
- Open Lines source is complete;
- call evidence source is complete.

This prevents a later message from being incorrectly called
the first response when an earlier successful call was
omitted from the input.

CRM linkage:
a call can be linked to a lead either:
- directly through CRM_ENTITY_TYPE / CRM_ENTITY_ID;
- through CRM_ACTIVITY_ID and the local CRM activity owner.

No customer message text is inspected by this adapter.

Next:
Stage 4.10G3 will connect these completeness contracts to the
full Golden SQLite snapshot and a complete Voximplant time
window.

Safety:
- CRM writes: NONE
- SQLite production writes: NONE
- OpenAI calls: NONE
- commit/push: NONE
