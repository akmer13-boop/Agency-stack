# Stage 4.10A — Business Policy Registry

Status: LOCAL / NOT COMMITTED

Source:
customer-completed AI-ROP questionnaire received 2026-08-18.

## Approved business inputs

First response:
- start = CRM lead creation
- threshold = 15 minutes
- business time only
- out-of-hours timer starts next working day
- reassignment does not reset timer

Stale activity:
- outbound call
- inbound call
- client message
- commercial proposal

Stage thresholds:
- Новая заявка = 15m
- Выявление потребностей = 3d
- Подбор пакетного тура = 4h
- Запрос отправлен партнеру = 24h
- Коммерческое предложение отправлено = 2d
- Потенциальный клиент = return-to-client date

Proposal:
- requires both proposal stage + client message
- no client response attention = 2d

Conversion:
- each funnel separately
- questionnaire stage names preserved

## Still blocked

- workday start/end
- working weekdays
- holiday calendar
- precedence between general 15m and stage stale thresholds
- rating weights
- rating normalization
- Excel plan schema
- plan allocation/ownership
- numeric escalation thresholds
- real Bitrix category/status/stage IDs

## Guardrails

- no SQLite writes
- no Bitrix calls
- no OpenAI calls
- no CRM writes
- no SLA/KPI verdict activation
- no commit/push

Next: Stage 4.10B — Bitrix funnel/stage ID binding.
