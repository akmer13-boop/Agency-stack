# Stage 4.11A5B - Realtime First Response SLA

Status: LOCAL / NOT COMMITTED

Purpose:
connect completed realtime factual evidence to the existing
deterministic First Response SLA evaluator.

New source-coverage contract:
- openlines
- voximplant_realtime

A source is not considered complete merely because its tables
exist.

Coverage must be explicitly recorded as a verified interval.

Evaluation window:
lead DATE_CREATE -> requested as_of.

Both Open Lines and realtime telephony must continuously cover
that window before a strict no-response or earliest-response
verdict can be produced.

This prevents:
- evaluating a lead created before realtime call monitoring
  was activated;
- silently treating an ingestion outage as complete data;
- declaring BREACH when an unobserved response may exist.

Realtime call logic:
- successful CallEnd requires CALL_FAILED_CODE=200;
- successful call linked to the evaluated lead must have valid
  exact CallStart evidence;
- failed calls never satisfy First Response;
- incomplete successful-call evidence blocks the verdict.

If source coverage and factual evidence are complete, the
existing:
build_first_response_case_from_sources()
and:
evaluate_first_response_case()
remain the source of truth.

Outputs:
- READY / BLOCKED
- OK / OPEN / BREACH
- exact response source
- coverage diagnostics
- relevant successful call ids

No business threshold is duplicated in this stage.

The existing approved 15-business-minute policy remains the
source of truth.

Safety:
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production callback activation
- local SQLite coverage metadata only
- no commit/push

Next:
Stage 4.11A5C - realtime Deal Stage SLA bridge.
