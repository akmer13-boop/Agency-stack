# Stage 4.9B3 — Full Open Lines History Sync

Status: local validation stage.

## Goal

Build a read-only, resumable full-history synchronization path for Bitrix24 Open Lines.

## API path

- `imopenlines.crm.chat.get` — CRM object to Open Lines chat linkage.
- `imopenlines.session.history.get` — latest/native Open Lines session metadata.
- `im.dialog.messages.get` — complete chat history:
  - `LAST_ID` for older messages;
  - `FIRST_ID` for newer messages;
  - maximum page size 50.

## Safety

- Bitrix24 writes: none.
- Message send/edit/delete: blocked by the Open Lines client allowlist.
- Full message text is stored only in the local Agency Stack SQLite database.
- No message text is printed by the sync CLI.
- No AI scoring is performed.

## Resumability

`openlines_chat_sync_state` stores per-chat history progress and message-ID bounds.
Re-running the sync continues from the oldest stored message ID.

## Incremental refresh

Once historical backfill is complete, new messages are fetched with `FIRST_ID` using the
newest locally stored message ID.

## CRM discovery

`openlines_crm_discovery_state` remembers the latest Open Lines CRM activity observed for
each CRM object. A CRM object is queried again when a newer Open Lines activity appears.

## Attribution

- manager: sender ID exists in the local Bitrix user directory;
- client: explicit Open Lines/extranet evidence;
- system: sender ID 0;
- bot: explicit bot evidence;
- unknown: no safe evidence for another role.

Unknown senders are never silently converted into clients.

## Session binding

Native session-history messages keep their real `SESSION_ID`.
Older messages recovered only through complete IM chat history use an explicit synthetic
chat-history binding until a real historical session ID becomes available.

## Operational commands

Bounded resumable batch:

```bash
./.venv/bin/python scripts/sync_openlines_history.py
```

Full resumable backfill:

```bash
./.venv/bin/python scripts/sync_openlines_history.py --full
```

## Next

After complete backfill and audit, build deterministic conversation read models. AI quality
scoring still waits for business-approved criteria.
