# Stage 4.11A4.1 - PII-minimized Call Inbox

Status: LOCAL / NOT COMMITTED

Telephony callback data is minimized before event identity
generation and before SQLite persistence.

Persisted call allowlist:
- CALL_ID
- USER_ID
- CALL_FAILED_CODE
- CALL_DURATION
- CALL_TYPE
- CRM_ACTIVITY_ID
- CRM_ENTITY_TYPE
- CRM_ENTITY_ID

Dropped:
- PHONE_NUMBER
- COMMENT
- SUBJECT
- CALL_RECORD_URL
- nested arbitrary data
- arbitrary extra call fields

Bitrix auth secrets remain excluded.

Defense in depth:
BitrixEventInboxStore also sanitizes direct internal enqueue()
calls.

CRM lead/deal/activity callback data is unchanged.

Safety:
- Amvera: NOT USED
- Bitrix calls: NONE
- Bitrix CRM writes: NONE
- OpenAI calls: NONE
- production callback: NOT ENABLED
- commit/push: NONE

Next:
Stage 4.11A5 - realtime Evidence/SLA trigger bridge.
