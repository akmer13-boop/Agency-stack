# Stage 4.1 — Bitrix URL Builder

Status: implemented locally, pending review.

## Goal

Return deterministic browser links to concrete Bitrix24 CRM entities without
exposing the webhook REST path or secret.

## Added

- `app/integrations/bitrix24/urls.py`
- `tests/test_bitrix24_urls.py`
- `tests/test_rop_deal_urls.py`
- `docs/STAGE4.1_STATUS.md`

## Changed

- `app/services/rop_deal.py`

## Supported URLs

- deal: `/crm/deal/details/<ID>/`
- lead: `/crm/lead/details/<ID>/`

The public portal origin is derived from the configured HTTPS webhook, but the
`/rest/<user>/<secret>/` portion is never copied into the generated browser URL.

## Deal drill-down integration

`DealDrilldown` now carries an optional `bitrix_url`.

Both:

- human deal drill-down;
- compact AI deal facts

can expose the secret-free browser URL when Bitrix24 is configured.

## Safety

- no CRM write;
- no DB migration;
- no new DB tables;
- no webhook secret in generated URL;
- invalid IDs are rejected;
- unsupported entity types are rejected;
- unsafe webhook configuration is rejected by the existing URL validator.

## Lead integration

The lead URL builder is ready. Per-lead links will be attached when a deterministic
per-lead drill-down/list contract is introduced. Aggregated Lead Intelligence is
not changed in Stage 4.1.

## Next

Stage 4.2 — deterministic CRM activity classification:
system activity vs human action vs confirmed communication vs unknown.
