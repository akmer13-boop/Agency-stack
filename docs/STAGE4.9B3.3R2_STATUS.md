# Stage 4.9B3.3R2 — Backfill Performance Isolation

## Problem

The first split-mode implementation still executed discovery preparation during `backfill-only`.
After more than two thousand discovered chats, the legacy discovery seed query became expensive
enough to make a five-chat backfill smoke test appear hung.

## Fix

- `backfill-only` no longer calls:
  - `seed_discovery_from_existing_links`;
  - `_discover_crm_objects`;
  - CRM chat batch discovery.
- The Bitrix user directory is loaded only when message backfill is enabled.
- Discovery bootstrap is a one-time migration bridge.
- The bootstrap SQL aggregates Open Lines activities once instead of running a correlated
  activity scan for every CRM link.

## Safety

- Bitrix write: none.
- Existing per-chat resume state is unchanged.
- Existing message storage is unchanged.
- Generic batch remains unavailable.
