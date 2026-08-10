# Stage 4.0A — Customer TZ Alignment Foundation

Status: IMPLEMENTED LOCALLY — pending review.

Baseline:
- version 0.4.15
- branch stage-3.3b-full-read-sync
- commit a6621dd41285e9bab5211ed2569f8bb567001452

Added:
- docs/CUSTOMER_TZ_GAP.md
- docs/BITRIX_DATA_DICTIONARY.md
- docs/METRICS.md
- docs/STAGE4.0A_STATUS.md
- config/bitrix-field-map.yaml
- config/bitrix-pipelines.yaml

Runtime changes: none.
DB migrations: none.
ENV changes: none.
CRM writes: none.
Git commit/push: none.

Decision:
raw crm_raw_entities remains source/evidence layer.
Stage 4.0B adds semantic models above raw storage without replacing working sync.

Next:
Stage 4.0B — Semantic Data Layer.
