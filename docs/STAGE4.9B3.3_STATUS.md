# Stage 4.9B3.3 — Split Discovery / Backfill Controller

## Purpose

Separate Open Lines chat discovery from message-history backfill so a large Bitrix24 portal can
be processed in controlled resumable phases.

## Modes

Full discovery only:

```bash
./.venv/bin/python scripts/sync_openlines_history.py --full --discovery-only
```

Bounded backfill only:

```bash
./.venv/bin/python scripts/sync_openlines_history.py \
  --backfill-only \
  --max-chats 250 \
  --max-pages-per-chat 20
```

Combined mode remains available.

## Safety

- Bitrix write: none.
- Discovery batches contain only `imopenlines.crm.chat.get`.
- Generic `batch` is not exposed in the normal read-only allowlist.
- Backfill remains resumable per chat.
- AI scoring is not performed.
