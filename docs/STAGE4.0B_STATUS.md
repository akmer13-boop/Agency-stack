# Stage 4.0B — Semantic Data Layer

Status: implemented locally, pending review.

## Goal

Introduce a deterministic semantic projection above `crm_raw_entities` without
replacing the existing Bitrix24 sync or analytics runtime.

## Added

- `app/semantic/__init__.py`
- `app/semantic/models.py`
- `app/semantic/normalizer.py`
- `app/semantic/repository.py`
- `tests/test_semantic_normalizer.py`
- `docs/STAGE4.0B_STATUS.md`

## Semantic models

- `SemanticLead`
- `SemanticDeal`
- `SemanticActivity`
- `SemanticStageEvent`
- `SemanticUser`

## Rules

- raw CRM remains source evidence;
- no new DB tables are introduced in Stage 4.0B;
- no CRM writes;
- no LLM dependency;
- missing required IDs or invalid typed values raise `SemanticMappingError`;
- `UF_*` business semantics are not guessed;
- current analytics runtime is not switched to semantic models yet.

## Migration

None.

## ENV

None.

## Next

Stage 4.0C — Metric Contracts integration:
start moving selected analytics reads from ad-hoc raw JSON parsing to the semantic
projection one bounded metric at a time, with regression tests.
