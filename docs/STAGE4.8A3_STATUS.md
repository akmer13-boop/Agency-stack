# Stage 4.8A3 — Business Policy Registry Foundation

Status: locally validated; pending GitHub review.

## Purpose

Create one fail-closed registry for all business rules that are still waiting
for approval. The registry separates four different concepts:

1. observed CRM facts;
2. source-data coverage;
3. business approval;
4. technical activation/binding.

These concepts must not be collapsed into one another.

## Policies in scope

- `first_response_sla`
- `stale_deal`
- `proposal_stale`
- `business_conversion`
- `manager_rating`
- `sales_plan_fact`
- `management_escalation`

The default tracked configuration keeps every policy in `pending`.

## Safety contract

- `approved` does not mean operational;
- no policy is activated automatically;
- First Response configuration `READY` does not mean SLA is approved or active;
- data coverage has no automatic pass/fail threshold;
- no manager rating, stale verdict, conversion KPI, plan/fact or escalation is calculated;
- no Bitrix24 write;
- no SQLite schema migration;
- no automatic data repair.

## Deliverables

- tracked policy document: `config/rop-business-policies.json`;
- typed loader and fail-closed validator;
- one combined policy/status view with descriptive data gaps;
- AI tool `get_rop_business_policy_status`;
- agent guardrails;
- automated tests;
- live read-only validation against the current local CRM.

## Next

After business answers are formally approved, Stage 4.8B will validate
policy-specific parameters and bind approved rules one by one.

## Local validation evidence

Validated before publication:

- Python compile: PASS;
- Ruff: PASS;
- targeted policy-registry tests: PASS;
- full pytest: PASS;
- live read-only policy registry smoke: PASS;
- registry policies: 7;
- default approvals: PENDING;
- operational rules: 0;
- automatic activation: NONE;
- business KPI calculations: NONE;
- coverage acceptance thresholds: NONE;
- Bitrix24 CRM write: NONE;
- SQLite schema migration: NONE;
- `git diff --check`: PASS;
- strict Git scope: PASS.

Publication version: Agency Stack `0.4.28`.

The registry intentionally separates source facts, source-data coverage,
business approval and technical activation. Business approval alone cannot
activate a KPI or management verdict.
