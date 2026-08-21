# Stage 4.13A3.1 - Voximplant Reconciliation

Status: COMPLETE

Implemented:
- voximplant.statistic.get added to the read-only sync client;
- complete next-cursor pagination;
- bounded reconciliation window;
- sanitized local Voximplant statistic facts;
- statistic CALL_ID matched against realtime CALLSTART/CALLEND evidence;
- successful calls require CALLSTART for realtime completeness;
- every statistic row requires CALLEND for realtime completeness;
- reconciliation run history;
- standalone safe CLI.

Privacy:
- phone numbers are not persisted;
- recordings are not fetched;
- client text is not fetched;
- only statistic ID, CALL_ID, call start timestamp, result code,
  duration and CRM activity ID are persisted.

Safety:
- no Bitrix writes;
- no CRM writes;
- no OpenAI calls;
- no realtime endpoint activation;
- no source coverage initialization;
- no coverage watermark advancement.

Important:
voximplant.statistic.get is a reconciliation source.
It does not replace ONVOXIMPLANTCALLSTART as the exact successful
conversation response timestamp.

Stage 4.13A3.2 will activate and verify the outgoing Bitrix event
delivery path before any voximplant_realtime coverage is allowed.
