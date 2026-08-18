# Stage 4.10B - Bitrix Funnel and Stage Binding

Status: LOCAL / NOT COMMITTED

Discovery source:
data/stage410b_bitrix_funnels.json

Primary business-policy funnel:
- category_id: 7
- entity_id: DEAL_STAGE_7

Lead:
- NEW -> unprocessed
- CONVERTED -> qualified

The discovered B2C stage IDs were validated against
the exact names returned by Bitrix.

Two questionnaire/Bitrix name variants remain explicitly
unconfirmed:
1. new application -> C7:NEW
2. commercial proposal sent -> C7:EXECUTING

Other deal funnels remain unbound because the questionnaire
requires funnels to be analyzed separately and no separate
stage-SLA contract was provided for those funnels.

Safety:
- CRM writes: NONE
- Bitrix calls in this binding step: NONE
- SQLite writes: NONE
- OpenAI calls: NONE
- SLA/KPI activation: NONE
- commit/push: NONE
