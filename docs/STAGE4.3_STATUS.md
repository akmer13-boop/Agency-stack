# Stage 4.3 — Lead Response Evidence Contract

Status: accepted locally on real CRM, pending GitHub review.

## Goal

Provide deterministic evidence for questions about how quickly new leads receive
observable CRM action or communication without declaring a First Response SLA.

## Contract

For each lead in a rolling cohort the system can determine:

- lead `DATE_CREATE`;
- first manager-side evidence after lead creation;
- first confirmed CRM communication after lead creation;
- calendar elapsed seconds to each event;
- activity ID used as evidence;
- timestamp source used for the event;
- fallback timestamp warnings;
- pre-creation activities ignored from response evidence.

## Aggregated report

The ROP tool exposes:

- cohort size;
- coverage by manager-side evidence;
- coverage by confirmed communication;
- median and p90 calendar elapsed time;
- timestamp source distribution;
- fallback timestamp counts.

No lead title, phone, e-mail, activity subject or raw message text is sent to the
LLM.

## Explicitly not implemented

This stage does not define or calculate First Response SLA compliance.

The following business decisions remain blocked:

- normative timer start;
- which event legally/business-wise counts as first response;
- working-hours calendar;
- weekends and holidays;
- reassignment handling;
- SLA threshold;
- pass/fail or overdue status.

For technical evidence only, `DATE_CREATE` is used as the observation start.

## Tool

`get_rop_lead_response_evidence(days)` is available for questions about observed
lead reaction/response speed. Its output explicitly states that it is not an SLA.

## Safety

- no CRM write;
- no DB migration;
- no new DB tables;
- no LLM calculation;
- no manager ranking from response evidence;
- ambiguous activity semantics continue to follow Stage 4.2.

## Next

Stage 4.4 — customer-approved First Response business rule configuration after
the unresolved SLA decisions are formally approved.

## Real CRM validation

Validated on the local synchronized Bitrix24 SQLite.

Rolling 7 days:

- lead cohort: 496;
- leads with manager-side evidence: 350;
- leads with confirmed communication: 279;
- manager evidence median: 5,890.5 seconds;
- manager evidence p90: 98,580 seconds;
- confirmed communication median: 14,450 seconds;
- confirmed communication p90: 54,216 seconds;
- manager timestamp sources: END_TIME 340, LAST_UPDATED 10;
- communication timestamp sources: END_TIME 279;
- fallback timestamps manager/communication: 10/0;
- skipped leads without DATE_CREATE: 0.

Rolling 30 days:

- lead cohort: 2,124;
- leads with manager-side evidence: 1,584;
- leads with confirmed communication: 1,317;
- manager evidence median: 881 seconds;
- manager evidence p90: 104,507 seconds;
- confirmed communication median: 12,210 seconds;
- confirmed communication p90: 73,288 seconds;
- manager timestamp sources: END_TIME 1,561, LAST_UPDATED 23;
- communication timestamp sources: END_TIME 1,317;
- fallback timestamps manager/communication: 23/0;
- skipped leads without DATE_CREATE: 0.

Real SQLite response evidence audit: PASS.

These values are observed CRM evidence. They are not a First Response SLA,
compliance score, overdue calculation, or manager ranking.
