# Stage 4.9D5 — ROP Open Lines Reporting Bridge

## Goal

Expose the factual Open Lines response layer to the existing AI-ROP read-only tool surface without
silently replacing the legacy CRM lead response evidence contract.

## New ROP source

`get_rop_openlines_response(days=7, manager_id=None)` answers questions about actual messenger /
Open Lines response timing.

The rolling window is event-based:

- a client→manager response event belongs to the window by the timestamp at which the manager turn
  begins;
- wait is measured from the end of the client turn to the beginning of the manager turn;
- first-manager-response is the first manager turn after the first client turn in that chat.

## Source separation

The existing `get_rop_lead_response_evidence` remains unchanged.

It represents observed CRM lead evidence.

The new `get_rop_openlines_response` represents actual Open Lines human-message response evidence.

Neither source is silently treated as First Response SLA.

## Manager attribution

Only DIRECTORY_USER manager IDs from the already-validated Open Lines factual layer are surfaced.

Inactive users remain visible as historical employees and are explicitly labelled
`inactive/history`.

## Tail semantics

Team reports may show:

- current client-tail candidates;
- initial client threads with no later manager response observed.

These are filtered by last human activity inside the rolling window.

When a specific manager is requested, tail/no-response counts are deliberately omitted because
current ownership and reassignment history have not been proven for those unresolved tails.

## Explicit non-goals

- no SLA compliance;
- no business-hours adjustment;
- no ranking;
- no good/bad manager verdict;
- no causal inference;
- no CRM write;
- no Bitrix request.

## Next

Stage 4.9D6 should validate routing and side-by-side source selection on real local data.
