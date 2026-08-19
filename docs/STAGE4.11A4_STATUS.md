# Stage 4.11A4 - Bitrix Event Processor

Status: LOCAL / NOT COMMITTED

Purpose:
process events already accepted by the secure realtime inbox.

Flow:

bitrix_event_inbox
-> atomic claim
-> pending/failed -> processing
-> factual processing
-> completed or failed.

CRM refresh events:
- lead add/update;
- deal add/update/category move;
- activity add/update.

For these events the processor performs only read-only
Bitrix API calls and refreshes the corresponding local
crm_raw_entities row through the existing CrmStore.

Delete events:
delete callbacks are recorded into
bitrix_entity_delete_observations.

A realtime delete event does NOT physically delete historical
local CRM evidence and does not invent a reconciliation
tombstone.

Call events:
- ONVOXIMPLANTCALLINIT
- ONVOXIMPLANTCALLSTART
- ONVOXIMPLANTCALLEND

Safe call evidence is materialized into bitrix_call_evidence.

Only the following call fields are copied into that
materialized table:
- CALL_ID
- event timestamp
- USER_ID
- CALL_FAILED_CODE
- CALL_DURATION
- CRM_ACTIVITY_ID
- CRM_ENTITY_TYPE
- CRM_ENTITY_ID

Phone numbers and customer text are not copied into the
materialized call evidence table.

The original normalized inbox event remains the source event.

Retry:
failed events may be claimed again while attempts are below
max_attempts.

Once attempts reaches max_attempts, that event remains failed
and is not automatically retried.

No background worker is started automatically in this stage.

Utility:
scripts/process_bitrix_event_inbox.py

The utility can later drain the local queue, but Stage 4.11A4
validation uses synthetic temporary SQLite databases only.

Safety:
- Amvera: NOT USED
- outgoing Bitrix writes: NONE
- CRM writes in Bitrix: NONE
- OpenAI calls: NONE
- production callback activation: NONE
- commit/push: NONE

Next:
Stage 4.11A5 - Evidence/SLA trigger bridge from completed
realtime factual events.
