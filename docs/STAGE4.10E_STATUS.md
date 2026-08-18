# Stage 4.10E - Business Time Calculator

Status: LOCAL / NOT COMMITTED

Business decision:
all configured stage timers use business time.

Work schedule:
- Monday-Friday
- 09:00-19:00
- Europe/Moscow
- weekends closed
- Russian federal non-working dates closed

Duration resolution under this schedule:
- 15 minutes = 900 business seconds
- 3 business days = 30 business hours = 108000 seconds
- 4 business hours = 14400 seconds
- 24 business hours = 86400 seconds
- 2 business days = 20 business hours = 72000 seconds

The calculator:
- starts out-of-hours timers at the next business instant;
- pauses at 19:00;
- resumes at the next business day 09:00;
- skips weekends;
- skips the configured federal non-working dates;
- restarts a stage inactivity timer after qualifying activity;
- fails closed if an unsupported calendar year is requested.

The official 2026 calendar is stored as auditable JSON.
No runtime web request is required.

Only calendar year 2026 is enabled in this stage.
Other years fail closed until their official calendars are added.

Safety:
- CRM writes: NONE
- Bitrix calls: NONE
- SQLite writes: NONE
- OpenAI calls: NONE
- commit/push: NONE
