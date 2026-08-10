# Stage 4.5 — Lead Response Evidence Trend

Status: accepted locally on real CRM, pending GitHub review.

## Goal

Add deterministic week-over-week trend analysis on top of Stage 4.3 response
evidence without entering blocked Stage 4.4B SLA compliance work.

## Cohorts

The trend uses non-overlapping calendar weeks in `ROP_TIMEZONE`.

Only mature cohorts are included. A cohort is mature only after the technical
observation horizon has fully passed.

Default technical observation horizon: 7×24 hours after lead `DATE_CREATE`.

The horizon exists only to make weekly cohorts comparable. It is not an SLA
threshold and is not copied into the First Response policy.

## Metrics per week

- lead cohort size;
- manager-side evidence coverage;
- confirmed communication coverage;
- manager evidence median and p90;
- confirmed communication median and p90.

Evidence outside the observation horizon is not counted for that cohort.

## Latest vs previous

The deterministic layer returns signed deltas and descriptive directions:

- coverage: `higher` / `lower` / `same`;
- elapsed time: `faster` / `slower` / `same`.

No statistical significance, causal explanation, SLA compliance, or manager
performance judgement is inferred.

## Tool

`get_rop_lead_response_trend(weeks)` supports 2–12 mature weekly cohorts.

## Safety

- Stage 4.4B remains blocked by customer policy decisions;
- no CRM write;
- no DB migration;
- no LLM metric calculation;
- no customer SLA threshold invented;
- no manager ranking.

## Next

After real-CRM validation, publish the trend layer separately. First Response
compliance remains gated by the customer-approved policy contract.

## Real CRM validation

Validated on the local synchronized Bitrix24 SQLite using four mature
non-overlapping weekly lead cohorts in `Europe/Moscow`.

Observed cohorts:

- 2026-07-06 — 2026-07-13: n=565; manager coverage 72.7%;
  manager median 2 min; manager p90 21 h 30 min; communication coverage 71.7%;
  communication median 1 h 41 min; communication p90 14 h 8 min.
- 2026-07-13 — 2026-07-20: n=601; manager coverage 77.2%;
  manager median 8 min; manager p90 1 d 2 h; communication coverage 60.4%;
  communication median 2 h 52 min; communication p90 19 h 33 min.
- 2026-07-20 — 2026-07-27: n=487; manager coverage 73.9%;
  manager median 4 min; manager p90 1 d 5 h; communication coverage 66.5%;
  communication median 4 h 5 min; communication p90 22 h 59 min.
- 2026-07-27 — 2026-08-03: n=395; manager coverage 72.2%;
  manager median 23 min; manager p90 1 d 5 h; communication coverage 65.8%;
  communication median 3 h 49 min; communication p90 21 h 27 min.

Latest mature week vs previous:

- manager coverage: -1.8 pp (`lower`);
- manager median: +19 min (`slower`);
- manager p90: +8 min (`slower`);
- communication coverage: -0.7 pp (`lower`);
- communication median: -16 min (`faster`);
- communication p90: -1 h 32 min (`faster`).

Real SQLite response evidence trend audit: PASS.

These are descriptive observed CRM changes only. Statistical significance,
causality, SLA compliance and manager performance are not inferred.
