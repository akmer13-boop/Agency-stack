# Stage 4.0C — Metric Contracts Integration

Status: implemented locally, pending review.

## Goal

Move the first production metric from ad-hoc raw Bitrix JSON parsing to the
Stage 4 semantic layer without changing its business meaning.

## Integrated metric

M01 — New Leads.

The metric now uses:

`crm_raw_entities → SemanticRepository → SemanticLead → CountMetricContract`

instead of counting `DATE_CREATE` directly from raw lead dictionaries inside the
ROP snapshot.

## Runtime change

Changed:

- `app/services/rop_analytics.py`

Added:

- `app/semantic/metrics.py`
- `tests/test_semantic_metrics.py`
- `docs/STAGE4.0C_STATUS.md`

## Contract fields

- metric
- period_start
- period_end
- timezone
- value
- sample_size
- source_entity_ids
- calculated_at
- warnings

## Compatibility

- Lead total remains based on the same synchronized CRM dataset.
- New-leads 24h uses the semantic metric.
- Today/week/month `new_leads` use the semantic metric.
- Deal analytics remains on the existing implementation in this stage.
- Existing period boundaries remain inclusive to preserve current behavior.

## Safety

- no CRM write;
- no new DB tables;
- no DB migration;
- no LLM calculation;
- invalid semantic CRM values fail explicitly rather than being guessed.

## Acceptance

Required:

- semantic metric unit tests pass;
- existing ROP analytics regression tests pass;
- full Ruff passes;
- full pytest passes;
- real SQLite parity test confirms semantic lead count equals legacy raw count
  for today/week/month.

## Next

Stage 4.1 — Bitrix URL Builder / deterministic entity references.
