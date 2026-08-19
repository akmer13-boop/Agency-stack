# Stage 4.11A2-R2 - FastAPI Bitrix Event Endpoint

Status: LOCAL / NOT COMMITTED

Endpoint:
POST /api/v1/bitrix/events

Default:
DISABLED.

Dedicated event security:
- BITRIX_EVENT_APPLICATION_TOKEN
- BITRIX_EVENT_MEMBER_ID
- BITRIX_EVENT_DOMAIN
- BITRIX_EVENT_MAX_BODY_BYTES

The callback does not use AGENT_API_TOKEN.

Behavior:
1. endpoint disabled by default;
2. JSON/form request accepted;
3. application token verified;
4. member/domain optionally restricted;
5. event normalized;
6. normalized factual data enqueued;
7. duplicates are idempotent;
8. HTTP 202 returned.

Secret safety:
- application_token is never persisted;
- Bitrix access_token is never persisted;
- refresh_token is never persisted.

Health:
bitrix_realtime_events_enabled is exposed as
a non-secret runtime boolean.

Legacy health test was updated to include this new
safe field.

No event.bind subscription is performed yet.

Next:
Stage 4.11A3 - subscription discovery and controlled
Bitrix event.bind planning.

Safety:
- CRM writes: NONE
- external Bitrix calls: NONE
- OpenAI calls: NONE
- production activation: NONE
- commit/push: NONE
