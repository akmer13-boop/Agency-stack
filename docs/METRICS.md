# Agency Stack — Metric Contracts

## Principle
Code calculates CRM numbers. LLM may explain them but must not silently recalculate them.

## M01 New Leads
COUNT leads by DATE_CREATE inside explicit period. IMPLEMENTED.

## M02 New Deals
COUNT deals by DATE_CREATE inside explicit period. IMPLEMENTED.
Not lead→deal cohort conversion.

## M03 Current Lead P/S/F
Current STATUS_SEMANTIC_ID for selected cohort. IMPLEMENTED.

## M04 Closed Deal Conversion
Current technical KPI: WON / (WON + LOST) * 100.
IMPLEMENTED, but universal business meaning requires approval.

## M05 Stage Aging
Time on measured stage. IMPLEMENTED.
Does not prove absence of client contact.

## M06 3+/5+ signals
Configurable attention/critical stage-age signals. IMPLEMENTED/PARTIAL.

## M07 Any CRM Activity
At least one CRM activity exists. IMPLEMENTED.
Not equal to human processing.

## M08 Confirmed Communication
Completed activity explicitly classified as meeting/call/e-mail. IMPLEMENTED.

## M09 Time to First Confirmed CRM Communication
first_confirmed_communication_at - lead_created_at.
IMPLEMENTED for weekend cohort.
This is NOT First Response SLA.

## M10 First Response
BLOCKED_BY_BUSINESS_DECISION:
timer start, qualifying response, working hours, weekends, reassignment, SLA.

## M11 Pipeline Amount
CRM OPPORTUNITY of relevant active deals grouped by currency.
Not accounting revenue/payment/margin.

## M12 WON Amount
CRM OPPORTUNITY of successful deals.
Not accounting revenue unless finance confirms equivalence.

## M13 Manager Workload
Deterministic entity/activity counts. PARTIAL/IMPLEMENTED.
Current ASSIGNED_BY_ID does not prove historical owner.

## M14 Manager Rating
BLOCKED_BY_BUSINESS_DECISION.
No hardcoded final weights.

## M15 КП Staleness
BLOCKED_BY_BUSINESS_DECISION.
Need exact "КП sent" event, limit and reset rules.

## M16 Script Compliance
BLOCKED_BY_INPUT.
Need approved scripts, sources and privacy rules.

## Output contract
New metrics should expose:
metric, period_start, period_end, timezone, filters, value, sample_size,
source entity IDs when applicable, limitations, calculated_at.

## Stage 4.0C runtime integration

M01 New Leads is the first metric migrated to the semantic contract layer.

Runtime path:

`crm_raw_entities → SemanticRepository → SemanticLead → CountMetricContract → ROP Snapshot`

The business formula and current inclusive period boundaries are unchanged.
Deal metrics remain on the previous implementation until migrated separately.
\n

## Stage 4.2 — CRM activity evidence classification

Activity rows are not treated as equivalent proof of manager work.

Deterministic classes:

- `confirmed_communication`: completed meeting/call/e-mail;
- `human_action`: completed `TYPE_ID=6` User Action unless stronger system evidence applies;
- `system_activity`: completed non-communication activity with positive `AUTOCOMPLETE_RULE`;
- `unknown`: insufficient evidence; no business meaning is guessed.

Manager-side evidence is derived conservatively from completed User Action or
outgoing completed call/e-mail (`DIRECTION=2`) without autocomplete evidence.
Incoming call/e-mail and meetings remain communication evidence but are not
automatically credited as manager actions. Missing direction is not guessed.

This taxonomy is not the customer-approved First Response SLA definition.

## Stage 4.3 — Lead response evidence

This is an evidence contract, not an approved SLA metric.

For a rolling lead cohort the deterministic layer records:

- `DATE_CREATE` as the technical observation start;
- first manager-side evidence after creation;
- first confirmed CRM communication after creation;
- calendar elapsed seconds;
- evidence activity ID;
- timestamp source and fallback warnings.

Aggregates include median and p90 elapsed time plus evidence coverage.

The following are intentionally not inferred:

- business-hours calendar;
- holidays/weekends;
- historical reassignment;
- normative first-response event;
- SLA threshold;
- compliance/pass/fail.

Until those decisions are approved, outputs must be labelled observed response
evidence rather than First Response SLA.
