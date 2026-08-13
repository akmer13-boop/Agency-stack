# Stage 4.8A — Deterministic Management Fact Layer

Status: locally validated; pending GitHub review.

## Goal

Build a stable policy-free management fact layer on top of the validated
`crm_active_entities` / semantic projections while business policy answers are pending.

## Added

- current deal facts: active / WON / LOST;
- current lead facts: active / successful / failed;
- current responsible assignment facts;
- CRM `OPPORTUNITY` for currently WON deals, kept separate by currency;
- sales CRM activity facts linked to lead/deal owners;
- conservative activity evidence classification from the existing semantic layer;
- current stage/status distributions;
- stored deal/lead stage-history event counts;
- employee FIO / department enrichment when available locally;
- AI tool `get_rop_management_facts(manager_id: int | None = None)`.

## Explicitly NOT calculated

Until business answers are approved:

- First Response SLA compliance;
- stale-deal verdict;
- proposal/КП stale verdict;
- business conversion KPI;
- manager rating/score;
- sales plan/fact;
- mandatory management escalation verdict.

These are returned as `pending_business_approval`, not guessed.

## Safety

- source is the active CRM semantic layer;
- no Bitrix24 request is required to build facts;
- no Bitrix24 write;
- no SQLite schema change;
- no CRM row mutation;
- no manager best/worst sorting;
- WON `OPPORTUNITY` is not called accounting revenue/payment;
- current assignment is not treated as historical ownership after reassignment;
- no business thresholds are hard-coded.

## Version

Development validation remains on Agency Stack `0.4.25`.
Publish will bump the version only after local validation passes.

## Local validation evidence

Validated on the live local CRM snapshot before publication:

- Python compile: PASS;
- Ruff: PASS;
- targeted Stage 4.8A tests: 30 passed;
- full pytest: 181 passed;
- live read-only fact smoke: PASS;
- active CRM fact source: YES;
- explicit pending business-rule guardrails: PASS;
- manager rating: NOT CALCULATED;
- First Response SLA compliance: NOT CALCULATED;
- stale / proposal verdict: NOT CALCULATED;
- business conversion KPI: NOT CALCULATED;
- plan / fact: NOT CALCULATED;
- Bitrix24 CRM write: NONE;
- SQLite schema migration: NONE;
- `git diff --check`: PASS;
- strict Git scope: PASS.

Observed live smoke snapshot during validation:

- deals active / WON / LOST: `1344 / 1323 / 5574`;
- leads active / successful / failed: `167 / 7386 / 11161`;
- sales CRM activities linked to lead/deal: `116321`;
- responsible IDs represented in facts: `90`;
- pending business rules remained explicit:
  `first_response_sla`, `stale_deal`, `proposal_stale`,
  `business_conversion`, `manager_rating`, `sales_plan_fact`,
  `management_escalation`.

Publication version: Agency Stack `0.4.26`.

This stage still does not assign business meaning to the fact counts.
Those policies are bound only after explicit business approval.
