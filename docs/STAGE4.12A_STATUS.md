# Stage 4.12A - Lead Policy Profile Resolution

Status: LOCAL / NOT COMMITTED

Purpose:
resolve Tourism B2C scope for leads without applying B2C
policy to Concierge, B2B, Russian Tour or ambiguous leads.

Confirmed local facts:
- Tourism B2C department: 19
- B2B department: 20
- Russian Tour department: 24
- Concierge departments: 36, 41, 44
- Tourism root department: 1
- Call Center department: 103
- Tourism B2C deal category: 7
- Bitrix deal field LEAD_ID is the canonical lead -> deal link

Lead resolution rules:
1. Explicit current non-B2C department wins:
   - 20 -> excluded B2B
   - 24 -> excluded Russian Tour
   - 36/41/44 -> excluded Concierge
2. Exactly one linked funnel and category 7:
   -> Tourism B2C.
3. Multiple linked categories including category 7:
   -> unresolved / fail closed.
4. Linked non-B2C deal only:
   -> out of B2C scope.
5. Current department 19:
   -> Tourism B2C.
6. Previously confirmed B2C moved to a neutral/service department:
   -> sticky Tourism B2C.
7. Department 1, 103 and service/unknown departments:
   -> unresolved until stronger evidence appears.

Sticky scope:
only a positive Tourism B2C resolution is persisted locally.
Dynamic exclusions are not persisted.

This permits:
- a lead to start in Call Center;
- manager response evidence to occur there;
- the lead to later become proven B2C;
- First Response to be evaluated retrospectively from the
  original lead DATE_CREATE using already-collected evidence.

Deal scope:
- category 7 remains the B2C funnel proof;
- a deal currently assigned to Concierge department 36/41/44
  is excluded from the B2C ROP surface.

Realtime:
a direct Deal Add/Update target also surfaces its LEAD_ID so
the lead profile can become resolved when the B2C deal exists.

Safety:
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production activation
- no customer message text analysis
- local profile persistence only
- no commit/push
