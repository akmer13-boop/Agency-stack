# Stage 4.9E3 — Cost-Safe Episode Chunking

## Goal

Create deterministic technical chunks inside Stage 4.9E2 episodes before any LLM request.

## Character cap

`MAX_CHUNK_TEXT_CHARS = 10000`.

This is a conservative transport/pre-processing cap. It is **not**:

- an LLM context-window claim;
- a token guarantee;
- a business rule;
- an SLA.

Before the first real model call, Stage 4.9E4 must still calculate model-specific token budgets.

## Boundary policy

Normal messages are kept whole.

If adding a normal message would exceed the character cap, a new chunk starts before that message.

A raw message is split only if the message itself exceeds the cap. In that case:

- `message_id` is preserved;
- exact `char_start` / `char_end` offsets are stored;
- `message_segment_index` / `message_segment_count` are stored;
- segment SHA-256 is stored.

## Tables

### `conversation_semantic_chunks`

One row per technical chunk with:

- source episode;
- sequence;
- first/last message and role;
- text size;
- client/manager segment counts;
- distinct managers;
- mid-turn continuation flags;
- source and content fingerprints.

### `conversation_semantic_chunk_segments`

Exact message/character provenance for every chunk segment.

### `conversation_semantic_chunk_crm_links`

Many-to-many CRM provenance inherited from the episode. It must not be summed globally.

## Fingerprints

`source_fingerprint_sha256` includes source message identity and offsets. It is used for deterministic
provenance / change detection.

`content_fingerprint_sha256` is content-oriented for text segments and can identify exact reusable
text chunks across different source records.

Non-text messages intentionally include message identity in the content fingerprint so unrelated
attachments are never declared semantically reusable just because their text is empty.

## Non-goals

- no LLM request;
- no token-cost claim;
- no semantic extraction;
- no template removal from provenance;
- no SLA;
- no manager rating;
- no Bitrix write.

## Next

Stage 4.9E4 should produce model-specific token estimates, semantic schemas and a small dry-run batch
plan before any paid full-corpus semantic processing.
