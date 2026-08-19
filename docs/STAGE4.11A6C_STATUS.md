# Stage 4.11A6C - Operational Coverage Watermark

Status: LOCAL / NOT COMMITTED

Purpose:
prevent SLA dispatch and deadline sweeps from consuming work
while factual source coverage is incomplete.

Required operational sources:
- crm_realtime
- openlines
- voximplant_realtime

Safe coverage is represented by explicit bounded watermarks.

The operational runner never assumes an open-ended source is
healthy forever.

Before an SLA cycle:
all required sources must be explicitly covered through the
requested as_of timestamp.

If any source is:
- missing; or
- covered only up to an earlier timestamp,

the operational cycle returns GUARDED and does not claim:
- completed Bitrix events;
- SLA deadline sweep candidates.

This is important because consuming an event during a source
outage could create a false deterministic result.

Watermark rules:
- first activation requires explicit coverage_start;
- covered_through cannot be later than observed_at;
- watermarks never move backwards;
- later advances create bounded continuous coverage intervals;
- no production open-ended interval is created by this layer.

Operational cycle:
1. verify source watermarks;
2. process completed factual events through A6A;
3. process due time transitions through A6B.

Still intentionally blocked:
- automatic Concierge SLA;
- automatic lead First Response SLA until lead policy profile
  is resolved.

Safety:
- no Bitrix API calls
- no Bitrix CRM writes
- no OpenAI calls
- no Amvera
- no production callback activation
- no customer message text analysis
- no commit/push

Next:
pre-commit audit of complete Stage 4.11A6A-A6C.
