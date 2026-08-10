# Bitrix24 Data Dictionary

## Rules
- Bitrix ID is the technical identity.
- FIO is display data, not a unique key.
- Pipeline/stage IDs are technical identifiers.
- UF_* business meaning must never be guessed.
- Raw payload remains source evidence.
- Analytics should consume normalized semantic values.

## Deal
| Semantic | Bitrix |
|---|---|
| id | ID |
| pipeline_id | CATEGORY_ID |
| stage_id | STAGE_ID |
| stage_semantic | STAGE_SEMANTIC_ID |
| amount | OPPORTUNITY |
| currency | CURRENCY_ID |
| assigned_user_id | ASSIGNED_BY_ID |
| created_at | DATE_CREATE |
| updated_at | DATE_MODIFY |
| moved_at | MOVED_TIME |
| close_date | CLOSEDATE |

## Lead
| Semantic | Bitrix |
|---|---|
| id | ID |
| status_id | STATUS_ID |
| status_semantic | STATUS_SEMANTIC_ID |
| source_id | SOURCE_ID |
| assigned_user_id | ASSIGNED_BY_ID |
| created_at | DATE_CREATE |
| updated_at | DATE_MODIFY |
| amount | OPPORTUNITY |
| currency | CURRENCY_ID |

## User
ID, NAME, LAST_NAME, SECOND_NAME, ACTIVE, WORK_POSITION, UF_DEPARTMENT.

## Department
ID, NAME, PARENT.

## Stage history
Deal history uses deal stage fields and CREATED_TIME.
Lead history uses STATUS_ID, STATUS_SEMANTIC_ID and CREATED_TIME.

## Activity
Stage 4.2 must deterministically classify:
- system_activity
- human_action
- confirmed_communication
- unknown

"Any CRM activity" is not automatically equal to "manager processed the lead".

## UF_* custom fields
No business mapping is approved in Stage 4.0A.
Every UF_* mapping must be verified against the real portal before use.
