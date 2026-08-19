# Stage 4.11A1 - Secure Bitrix Real-Time Event Inbox

Status: LOCAL / NOT COMMITTED

Purpose:
prepare the safe event-ingestion foundation before exposing
a public Bitrix event handler.

Supported event families:

CRM:
- lead add/update/delete
- deal add/update/delete
- deal category move
- activity add/update/delete

Telephony:
- call init
- call conversation start
- call end

Security:
- auth.application_token is mandatory;
- constant-time token comparison;
- optional member_id restriction;
- optional Bitrix domain restriction;
- unsupported events are rejected;
- request body size is bounded.

Secret handling:
OAuth access_token, refresh_token and application_token are
never persisted into bitrix_event_inbox.

Persistence:
bitrix_event_inbox is append-only at ingestion time and has
an event_key UNIQUE constraint for retry/idempotency safety.

Stored factual fields:
- normalized event name
- Bitrix event timestamp
- handler ID
- entity type / entity ID
- CALL_ID
- answering USER_ID
- member_id / domain
- event data only

The event callback only enqueues factual events.
It does NOT call OpenAI and does NOT perform CRM writes.

Next Stage 4.11A2:
- add FastAPI POST handler;
- add environment settings;
- return HTTP 202 quickly;
- test JSON and form callbacks through TestClient.

After that:
Stage 4.11A3 will create the Bitrix application subscription
plan and bind CRM + Voximplant events.

Safety:
- production SQLite writes during this stage: NONE
- Bitrix calls: NONE
- CRM writes: NONE
- OpenAI calls: NONE
- commit/push: NONE
