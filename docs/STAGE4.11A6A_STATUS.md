# Stage 4.11A6A - Scope-Safe Realtime SLA Orchestrator

Status: LOCAL / NOT COMMITTED

Purpose:
connect already-completed Bitrix factual events to the
deterministic SLA evaluation layer without coupling SLA
calculation to factual event ingestion.

Durability:
the orchestrator scans completed bitrix_event_inbox rows.
It does not depend on an in-memory callback or an unsafe
post-processing hook.

A completed factual event can therefore be picked up later.

Current policy scope:
- Deal CATEGORY_ID = Tourism B2C configured category:
  eligible for tourism_b2c profile.
- Other deal categories:
  scope-skipped.
- Leads:
  scope-skipped with lead_policy_profile_unresolved.

The lead guard is intentional.

A lead has no deal CATEGORY_ID yet. Because Concierge and
Tourism can have different First Response rules, the
orchestrator must not automatically apply the Tourism B2C SLA
until an explicit lead -> business-profile binding is known.

Event target resolution:
- lead/deal event -> direct entity;
- activity event -> CRM activity OWNER_TYPE_ID / OWNER_ID;
- telephony event -> direct CRM linkage and/or activity owner;
- delete events -> no SLA calculation.

Runtime tables:
- rop_sla_event_dispatch
- rop_sla_evaluation_log

Evaluation log:
- deterministic PolicyEvaluation JSON only;
- no source callback payload is copied;
- no customer message text is required.

Retries:
failed technical dispatch can be retried with bounded attempts.

Important:
BLOCKED / NOT_APPLICABLE are valid deterministic evaluation
results and do not make the dispatch technically failed.

Still not implemented:
time-driven deadline sweep.

An event-driven orchestrator alone cannot detect an SLA
transition from OPEN to BREACH/ATTENTION when no new CRM event
arrives. That belongs to Stage 4.11A6B.

Safety:
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production callback activation
- no automatic Concierge policy assumptions
- no commit/push

Next:
Stage 4.11A6B - deadline/recheck sweep.
