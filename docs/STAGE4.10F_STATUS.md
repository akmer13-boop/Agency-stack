# Stage 4.10F - Deterministic CRM Policy Evaluation Bridge

Status: LOCAL / NOT COMMITTED

Purpose:
connect the approved policy engine to factual CRM/Open Lines
evidence without requiring a live database or live CRM call.

Implemented:

First Response evaluation:
- lead creation timestamp;
- human manager response timestamp;
- response must have evidence;
- response actor must be DIRECTORY_USER;
- business-time elapsed;
- deadline;
- OK / OPEN / BREACH;
- evidence references.

Stage inactivity evaluation:
- deal ID;
- Bitrix stage ID;
- stage-entry evidence;
- optional last qualifying activity;
- activity kind validation;
- activity evidence;
- business-time deadline;
- OPEN / ATTENTION;
- unsupported funnel = NOT_APPLICABLE;
- incomplete policy = BLOCKED.

Evidence-first guardrails:
- no missing event is inferred;
- no customer message text is required;
- only source IDs and factual timestamps are needed;
- bot/system response cannot satisfy human First Response;
- non-qualifying activity cannot reset a stage timer.

Potential client:
C7:FINAL_INVOICE remains BLOCKED until the concrete
return-to-client CRM field is discovered and bound.

This stage does not yet query SQLite.
The next adapter will read factual events from CRM/Open Lines
tables and feed them into these pure evaluation functions.

Safety:
- CRM writes: NONE
- Bitrix calls: NONE
- SQLite writes: NONE
- OpenAI calls: NONE
- commit/push: NONE
