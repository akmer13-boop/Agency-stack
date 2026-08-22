# Stage 4.13A2 - SLA Background Runtime Wiring

Status: COMPLETE

Implemented:
- dedicated SLA background worker;
- FastAPI lifespan integration;
- local Bitrix event inbox processing;
- guarded operational SLA cycle;
- local runtime table initialization;
- worker batch/poll/retry configuration.

Safety:
- ROP_SLA_WORKER_ENABLED=false by default;
- realtime endpoint remains disabled;
- CRM writes remain disabled;
- no synthetic operational coverage;
- no synthetic watermarks;
- no OpenAI calls;
- no Amvera changes;
- existing /health response contract is unchanged.

The operational SLA cycle remains fail-closed until verified
crm_realtime, openlines and voximplant_realtime coverage is present.

Stage 4.13A3 will handle realtime activation and verified source
coverage.
