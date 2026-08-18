# Stage 4.10D-R1 - Business Blocker Resolution

Business confirmations incorporated:

- C7:NEW is the questionnaire New application stage.
- C7:EXECUTING is the questionnaire Commercial proposal sent stage.
- Monday-Friday 09:00-19:00 Europe/Moscow.
- Saturday/Sunday are non-working.
- Russian federal holidays are non-working.

Stage inactivity behavior:

- timer starts on stage entry;
- qualifying activity restarts inactivity timer;
- stage exit stops the current timer;
- entering the next stage starts the next stage timer;
- explicitly configured stage threshold is authoritative;
- unspecified stages are not applicable;
- the questionnaire general 15-minute value is retained
  as source information but is not a global fallback.

Historical Stage 4.10A/B/C tests were updated because their
previous BLOCKED/UNCONFIRMED assertions were intentionally
superseded by the new business confirmations.

Russian holiday dates remain an external calendar-provider
implementation task for Stage 4.10E.

Safety:
- CRM writes: NONE
- Bitrix calls: NONE
- SQLite writes: NONE
- OpenAI calls: NONE
- commit/push: NONE
