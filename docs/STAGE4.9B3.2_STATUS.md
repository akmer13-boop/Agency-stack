# Stage 4.9B3.2 — Batch Open Lines CRM Discovery

Status: local validation stage.

## Goal

Accelerate discovery of CRM-linked Open Lines chats without widening write permissions.

## Proven live capability

The Bitrix24 On-Premise portal accepts `imopenlines.crm.chat.get` inside REST `batch`.
A live read-only probe executed 20 commands successfully in one HTTP request with zero errors.

## Design

- Up to 50 CRM objects per REST batch.
- Every internal command is constructed by Agency Stack and is always `imopenlines.crm.chat.get`.
- Generic `batch` is not added to the public read-only method allowlist.
- Per-item Bitrix errors are preserved and checkpointed independently.
- Successful zero-chat results are valid discovery checkpoints.

## Unchanged

- Full history: `im.dialog.messages.get`.
- Historical pagination: `LAST_ID`.
- Incremental refresh: `FIRST_ID`.
- Native session metadata: `imopenlines.session.history.get`.
- Manager attribution: local Bitrix user directory.
- Bitrix writes: none.
- AI scoring: none.
