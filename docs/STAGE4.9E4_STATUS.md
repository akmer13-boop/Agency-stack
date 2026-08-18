# Stage 4.9E4 — Model / Token / Semantic Schema Dry-Run Plan

## Goal

Define the first grounded Conversation Intelligence extraction contract and quantify the future
model workload before sending any customer conversation to an LLM.

## Current model binding

Agency Stack runtime configuration supplies `OPENAI_MODEL`.

The repository default at this stage is `gpt-5-mini`.

E4 does not change the model setting.

## Token planning

If `tiktoken` is already installed, E4 counts the reconstructed unique chunk content and static
extraction contract locally.

If it is not installed, E4 does not install anything and does not pretend to know the exact token
count. It prints a transparent character-based envelope and defers exact billed usage to the small
E5 pilot.

## Exact-content reuse

Chunks with the same content fingerprint are represented once in the full-run plan.

Source provenance is never removed: each source chunk still retains its own source fingerprint.

A later semantic-result cache can reuse the semantic result only when:

- content fingerprint matches;
- schema hash matches;
- extraction instruction hash matches;
- model/version policy matches.

## Extraction schema

`SemanticChunkExtraction` captures only grounded facts:

- customer intents;
- travel/service facts;
- customer questions;
- objections;
- complaints;
- manager actions;
- explicit manager promises;
- next steps;
- unanswered customer questions;
- compact factual summary.

Every fact-like item must carry one or more source message IDs.

## Explicit non-goals

E4 and the first semantic pilot do **not** produce:

- SLA compliance;
- breach flags;
- manager rating;
- good/bad quality score;
- blame;
- inferred reasons for WON/LOST;
- current CRM ownership inferred from historical text.

## Pilot strategy

A deterministic 40-chunk sample is selected from unique text chunks by round-robin across:

- channel;
- dialogue/client-only/manager-only scope;
- chunk size band.

Message text is not printed by the planning script.

## Next

Stage 4.9E5 can perform the first small paid structured-output pilot on only the approved 40 chunks,
persisting usage and validation results separately from production semantic facts.
