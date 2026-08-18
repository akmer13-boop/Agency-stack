# Stage 4.9E2 — Deterministic Conversation Episodes

## Goal

Create a deterministic semantic-analysis unit above human Open Lines turns before any LLM call.

## Episode boundary

A new episode starts after a human-turn inactivity gap of at least **72 hours**
(`259200` seconds).

Why 72 hours:

- Stage 4.9E1 observed p95 adjacent human-turn gap at about 46 hours;
- 72 hours sits beyond the normal p95 pause;
- it avoids treating every 24-hour pause as a new request;
- it remains a technical segmentation rule, not a business SLA.

The threshold says nothing about manager performance or customer intent.

## Important separation

An `episode` is a deterministic conversation segment.

It is **not** yet an LLM chunk.

Very large episodes may still require technical size-based chunking in Stage 4.9E3.

## Tables

### `conversation_episodes`

One row per deterministic episode with:

- chat and channel;
- split reason and gap before episode;
- first/last turn and message;
- timestamps and duration;
- human turn/message/text counts;
- client/manager counts;
- distinct managers;
- first/last role;
- client/manager/dialogue flags;
- inherited CRM-link count.

### `conversation_episode_turns`

Exact mapping of every human turn to exactly one episode.

### `conversation_episode_messages`

View preserving raw message provenance and full human text for later local semantic processing.

### `conversation_episode_crm_links`

View preserving all CRM links. It remains many-to-many and must not be summed globally.

## Preserved edge cases

- client-only episodes are preserved;
- manager-only episodes are preserved;
- zero-text human episodes are preserved for provenance but can be skipped by semantic LLM work;
- system/bot messages are excluded because the source `conversation_turns` is human-only;
- native/session-history versus recovered/dialog-history provenance stays available at message level.

## Non-goals

- no LLM call;
- no semantic extraction;
- no intent classification;
- no manager quality score;
- no SLA;
- no business-rule threshold;
- no Bitrix write.

## Next

Stage 4.9E3 should build cost-safe technical chunks inside episodes and quantify the future LLM workload.
