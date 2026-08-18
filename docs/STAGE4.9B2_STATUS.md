# Stage 4.9B2 — Open Lines Text Ingestion Foundation

Status: local validation stage.

## Goal

Persist CRM-linked Bitrix24 Open Lines conversations locally without changing Bitrix24.

## Scope

- Read-only Open Lines methods only:
  - `imopenlines.crm.chat.get`
  - `imopenlines.session.history.get`
- Discover CRM-linked Open Lines conversations from already-synced CRM activities.
- Persist locally:
  - chat IDs and connector metadata;
  - CRM links (lead/deal/contact/company);
  - session IDs;
  - raw Bitrix message IDs;
  - sender IDs;
  - source timestamps;
  - full message text;
  - deterministic sender role.
- Human manager role is allowed only when the sender ID is present in the local Bitrix user directory.
- System sender `0` stays system.
- Bot metadata stays bot.
- Other non-system/non-directory senders are treated as client-side participants.

## Explicit non-goals

- No message send/edit/delete.
- No Bitrix24 CRM write.
- No AI scoring.
- No quality rating.
- No summaries or recommendations yet.
- No call transcription.
- No external speech-to-text.

## Storage

New local tables:

- `openlines_chats`
- `openlines_crm_links`
- `openlines_sessions`
- `openlines_messages`

Message text is stored only in the local Agency Stack SQLite database.

## Next

After live validation, Stage 4.9B3 will add scalable/resumable full-history synchronization and
conversation-level read models for later AI analysis.
