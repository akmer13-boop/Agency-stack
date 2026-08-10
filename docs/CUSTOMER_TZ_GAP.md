# Customer TZ → Agency Stack GAP

## Baseline
- Agency Stack: 0.4.15
- Branch: stage-3.3b-full-read-sync
- Commit: a6621dd41285e9bab5211ed2569f8bb567001452
- CRM mode: read-only
- Analytics source: synchronized local SQLite

## Decision
Customer TZ v1.0 is accepted as the target requirements.
We do not restart from generic Stage 0 and do not rewrite working components.

Target flow:
Bitrix24 → raw sync → mapping/semantic layer → deterministic analytics → tools → LLM explanation.

## Status matrix
| Requirement | Status | Next action |
|---|---|---|
| Bitrix audit | DONE | preserve |
| Full sync | DONE | preserve |
| Incremental sync | DONE | preserve |
| Local SQLite | DONE | preserve |
| Leads/deals/activities/history | DONE | preserve |
| User/department directory | DONE | formalize mapping |
| Pipeline/stage mapping | PARTIAL | move to config |
| Bitrix data dictionary | PARTIAL | formalize |
| Normalized semantic layer | PARTIAL | Stage 4.0B |
| Metric contracts | PARTIAL | Stage 4.0C |
| Deal/lead analytics | DONE/PARTIAL | align contracts |
| Natural-language tools | DONE/PARTIAL | expand deterministic routing |
| Bitrix entity URLs | MISSING | add URL builder |
| Daily report | PARTIAL | align customer blocks |
| Daily/weekly scheduler | MISSING | add configurable scheduler |
| Deleted entity reconciliation | MISSING | add tombstone/reconciliation |
| First response / SLA | BLOCKED_BY_BUSINESS_DECISION | approve event + rules |
| КП staleness | BLOCKED_BY_BUSINESS_DECISION | approve КП event + limits |
| Conversion | PARTIAL | approve source→target definitions |
| Manager rating | BLOCKED_BY_BUSINESS_DECISION | approve weights/sample |
| Script compliance | BLOCKED_BY_INPUT | scripts + sources + privacy |
| AI Coach | FUTURE | after analytics stability |
| Observability | PARTIAL | readiness/dependency checks |

## Preserve
- Bitrix read-only safety
- full/incremental sync
- local SQLite
- Lead Intelligence
- Deal Intelligence
- Weekend Lead Intelligence
- manager analytics
- stage aging / SLA candidates
- OpenAI tool layer
- Telegram interface
- PII-minimized AI tool output

## Business decisions still required
P1 First response: start event, response event, working hours, weekends, reassignment, SLA.
P2 Stale object: which relevant event resets inactivity.
P3 КП: sent event, allowed duration, reset event, pipeline differences.
P4 Conversion: required source→target KPI definitions.
P5 Rating: KPI list, weights, scale, minimum sample, B2B/B2C differences.
P6 Script compliance: approved scripts, channels, evidence, privacy rules.

## Next
Stage 4.0B — Semantic Data Layer.
