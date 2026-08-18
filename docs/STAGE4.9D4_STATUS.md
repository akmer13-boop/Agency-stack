# Stage 4.9D4 — Safe Conversation Aggregates

## Goal

Materialize reporting-safe aggregates above factual Open Lines response events without introducing
SLA thresholds, AI scoring, or CRM double counting.

## Canonical global total

`conversation_global_metrics` contains exactly one row. This is the canonical global denominator
for Open Lines conversation facts.

Global totals must never be obtained by summing CRM entity aggregates.

## Manager metrics

`conversation_manager_metrics` contains factual client→manager response aggregates per
`DIRECTORY_USER` manager:

- response interval count;
- distinct response chats;
- first response count;
- median and p90 response wait;
- median and p90 first-response wait;
- directory active/inactive flag.

Inactive directory users are intentionally preserved for historical reporting. They are not
silently dropped and should be separable from the current active team.

## Channel metrics

`conversation_channel_metrics` contains one row per Open Lines connector/channel. Counts conserve
the canonical global response-event totals.

## CRM entity metrics

`conversation_crm_entity_metrics` is keyed by `(entity_type, entity_id)`.

It is intentionally entity-scoped. A response event may be attributed to multiple CRM entities
because one chat may link to a lead, deal, contact, or company.

Therefore:

- per-entity metrics are valid;
- per-entity-type analysis is valid;
- summing all entity rows to obtain a global total is invalid.

## Event provenance

`conversation_response_event_crm_links` preserves the factual response event key:

`chat_id + from_turn_index + to_turn_index`

alongside each CRM link.

## Explicit non-goals

- no SLA breach;
- no pass/fail;
- no good/bad manager rating;
- no working-hours adjustment;
- no AI analysis;
- no Bitrix write.

## Next

Stage 4.9D5 will wire these safe facts into the existing ROP reporting layer without replacing the
legacy CRM evidence contract silently.
