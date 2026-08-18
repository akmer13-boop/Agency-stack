from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

import aiosqlite

from app.semantic.conversation_intelligence_contract import (
    SemanticChunkExtraction,
)

GPT5_MINI_INPUT_USD_PER_M = 0.25
GPT5_MINI_OUTPUT_USD_PER_M = 2.00

STATIC_EXTRACTION_INSTRUCTIONS = """You extract only grounded conversation facts.

Rules:
- Use only the supplied human Open Lines messages.
- Never invent dates, budgets, destinations, objections, promises or unanswered questions.
- Every extracted fact/item/action/promise must cite one or more source message IDs.
- Prefer explicit evidence. Mark contextual evidence only when the meaning is strongly implied.
- System/bot messages are not present and must not be reconstructed.
- Client-only chunks may contain customer intent/request facts but no manager-quality conclusion.
- Do not calculate SLA compliance, manager rating, blame, conversion, sentiment score or causality.
- Do not infer current CRM ownership from a historical message.
- Keep summaries compact and factual.
"""


@dataclass(frozen=True, slots=True)
class ChunkPlanRow:
    chat_id: str
    episode_index: int
    chunk_index: int
    channel: str
    scope: str
    size_band: str
    text_chars: int
    source_fingerprint_sha256: str
    content_fingerprint_sha256: str


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    tokenizer_mode: str
    content_tokens: int | None
    static_prompt_tokens: int | None
    full_unique_input_tokens: int | None
    fallback_low_tokens: int
    fallback_mid_tokens: int
    fallback_high_tokens: int


@dataclass(frozen=True, slots=True)
class SemanticRunPlan:
    configured_model: str
    text_chunks: int
    unique_text_chunks: int
    reusable_duplicate_chunks: int
    unique_text_chars: int
    sample_size: int
    sample_rows: tuple[ChunkPlanRow, ...]
    token_estimate: TokenEstimate
    estimated_input_cost_usd: float | None
    output_cost_scenarios_usd: tuple[tuple[int, float], ...]


def extraction_json_schema() -> dict:
    return SemanticChunkExtraction.model_json_schema()


def extraction_schema_sha256() -> str:
    payload = json.dumps(
        extraction_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extraction_instructions_sha256() -> str:
    return hashlib.sha256(STATIC_EXTRACTION_INSTRUCTIONS.encode("utf-8")).hexdigest()


def _scope(has_client: int, has_manager: int) -> str:
    if has_client and has_manager:
        return "dialogue"
    if has_client:
        return "client_only"
    return "manager_only"


def _size_band(text_chars: int) -> str:
    if text_chars <= 1000:
        return "S_0_1k"
    if text_chars <= 4000:
        return "M_1_4k"
    if text_chars <= 8000:
        return "L_4_8k"
    return "XL_8_10k"


async def _load_inventory(
    database_path: str,
) -> tuple[list[ChunkPlanRow], dict[str, tuple[str, int, int]]]:
    async with aiosqlite.connect(database_path) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA query_only=ON")

        cursor = await database.execute(
            """
            SELECT
                chunk.chat_id,
                chunk.episode_index,
                chunk.chunk_index,
                chunk.text_chars,
                chunk.source_fingerprint_sha256,
                chunk.content_fingerprint_sha256,
                episode.channel,
                episode.has_client,
                episode.has_manager
            FROM conversation_semantic_chunks AS chunk
            JOIN conversation_episodes AS episode
              ON episode.chat_id = chunk.chat_id
             AND episode.episode_index = chunk.episode_index
            WHERE chunk.text_chars > 0
            ORDER BY
                CAST(chunk.chat_id AS INTEGER),
                chunk.episode_index,
                chunk.chunk_index
            """
        )
        rows = await cursor.fetchall()

    inventory = [
        ChunkPlanRow(
            chat_id=str(row["chat_id"]),
            episode_index=int(row["episode_index"]),
            chunk_index=int(row["chunk_index"]),
            channel=str(row["channel"]),
            scope=_scope(
                int(row["has_client"]),
                int(row["has_manager"]),
            ),
            size_band=_size_band(int(row["text_chars"])),
            text_chars=int(row["text_chars"]),
            source_fingerprint_sha256=str(row["source_fingerprint_sha256"]),
            content_fingerprint_sha256=str(row["content_fingerprint_sha256"]),
        )
        for row in rows
    ]

    canonical: dict[str, tuple[str, int, int]] = {}
    for row in inventory:
        canonical.setdefault(
            row.content_fingerprint_sha256,
            (row.chat_id, row.episode_index, row.chunk_index),
        )

    return inventory, canonical


def _canonical_rows(
    inventory: list[ChunkPlanRow],
) -> list[ChunkPlanRow]:
    seen: set[str] = set()
    result: list[ChunkPlanRow] = []

    for row in inventory:
        fingerprint = row.content_fingerprint_sha256
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)

    return result


def deterministic_sample(
    canonical_rows: list[ChunkPlanRow],
    limit: int,
) -> tuple[ChunkPlanRow, ...]:
    if limit <= 0:
        return ()

    groups: dict[tuple[str, str, str], deque[ChunkPlanRow]] = defaultdict(deque)
    for row in canonical_rows:
        groups[(row.channel, row.scope, row.size_band)].append(row)

    keys = sorted(groups)
    result: list[ChunkPlanRow] = []

    while keys and len(result) < limit:
        next_keys: list[tuple[str, str, str]] = []
        for key in keys:
            queue = groups[key]
            if queue and len(result) < limit:
                result.append(queue.popleft())
            if queue:
                next_keys.append(key)
        keys = next_keys

    return tuple(result)


async def _unique_chunk_texts(
    database_path: str,
    rows: list[ChunkPlanRow],
) -> list[str]:
    if not rows:
        return []

    keys = {(row.chat_id, row.episode_index, row.chunk_index) for row in rows}
    by_key: dict[tuple[str, int, int], list[tuple[int, str]]] = defaultdict(list)

    async with aiosqlite.connect(database_path) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA query_only=ON")

        cursor = await database.execute(
            """
            SELECT
                segment.chat_id,
                segment.episode_index,
                segment.chunk_index,
                segment.segment_index,
                segment.sender_role,
                segment.message_id,
                segment.char_start,
                segment.char_end,
                message.text_content
            FROM conversation_semantic_chunk_segments AS segment
            JOIN openlines_messages AS message
              ON message.message_id = segment.message_id
            ORDER BY
                CAST(segment.chat_id AS INTEGER),
                segment.episode_index,
                segment.chunk_index,
                segment.segment_index
            """
        )

        async for item in cursor:
            key = (
                str(item["chat_id"]),
                int(item["episode_index"]),
                int(item["chunk_index"]),
            )
            if key not in keys:
                continue

            source_text = str(item["text_content"] or "")
            start = int(item["char_start"])
            end = int(item["char_end"])
            text = source_text[start:end]
            envelope = (
                f"[{str(item['sender_role']).upper()}]"
                f"[message_id={str(item['message_id'])}] "
                f"{text}\n"
            )
            by_key[key].append((int(item["segment_index"]), envelope))

    result: list[str] = []
    for row in rows:
        key = (row.chat_id, row.episode_index, row.chunk_index)
        pieces = sorted(by_key[key])
        result.append("".join(piece for _, piece in pieces))
    return result


def _load_token_counter(
    model: str,
) -> tuple[str, Callable[[str], int] | None]:
    if importlib.util.find_spec("tiktoken") is None:
        return "fallback_chars_no_tiktoken", None

    import tiktoken

    try:
        encoding = tiktoken.encoding_for_model(model)
        mode = f"tiktoken:{encoding.name}:model"
    except KeyError:
        encoding = tiktoken.get_encoding("o200k_base")
        mode = "tiktoken:o200k_base:fallback"

    return mode, lambda text: len(encoding.encode(text))


async def build_semantic_run_plan(
    database_path: str,
    configured_model: str,
    *,
    sample_size: int = 40,
) -> SemanticRunPlan:
    inventory, _canonical = await _load_inventory(database_path)
    canonical_rows = _canonical_rows(inventory)
    sample_rows = deterministic_sample(canonical_rows, sample_size)

    text_chunks = len(inventory)
    unique_text_chunks = len(canonical_rows)
    reusable_duplicate_chunks = text_chunks - unique_text_chunks
    unique_text_chars = sum(row.text_chars for row in canonical_rows)

    tokenizer_mode, counter = _load_token_counter(configured_model)

    # Transparent fallback envelope. It is intentionally a range, not a
    # claim about exact GPT tokenization.
    fallback_low = math.ceil(unique_text_chars / 4)
    fallback_mid = math.ceil(unique_text_chars / 2)
    fallback_high = unique_text_chars

    content_tokens: int | None = None
    static_prompt_tokens: int | None = None
    full_unique_input_tokens: int | None = None

    if counter is not None:
        unique_texts = await _unique_chunk_texts(
            database_path,
            canonical_rows,
        )
        content_tokens = sum(counter(text) for text in unique_texts)

        schema_text = json.dumps(
            extraction_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
        )
        static_prompt_tokens = counter(STATIC_EXTRACTION_INSTRUCTIONS + "\n" + schema_text)
        full_unique_input_tokens = content_tokens + static_prompt_tokens * unique_text_chunks

    token_estimate = TokenEstimate(
        tokenizer_mode=tokenizer_mode,
        content_tokens=content_tokens,
        static_prompt_tokens=static_prompt_tokens,
        full_unique_input_tokens=full_unique_input_tokens,
        fallback_low_tokens=fallback_low,
        fallback_mid_tokens=fallback_mid,
        fallback_high_tokens=fallback_high,
    )

    input_cost: float | None = None
    output_scenarios: list[tuple[int, float]] = []

    if configured_model == "gpt-5-mini":
        if full_unique_input_tokens is not None:
            input_cost = full_unique_input_tokens / 1_000_000 * GPT5_MINI_INPUT_USD_PER_M

        for output_tokens_per_request in (250, 500, 1000):
            cost = (
                unique_text_chunks
                * output_tokens_per_request
                / 1_000_000
                * GPT5_MINI_OUTPUT_USD_PER_M
            )
            output_scenarios.append((output_tokens_per_request, cost))

    return SemanticRunPlan(
        configured_model=configured_model,
        text_chunks=text_chunks,
        unique_text_chunks=unique_text_chunks,
        reusable_duplicate_chunks=reusable_duplicate_chunks,
        unique_text_chars=unique_text_chars,
        sample_size=len(sample_rows),
        sample_rows=sample_rows,
        token_estimate=token_estimate,
        estimated_input_cost_usd=input_cost,
        output_cost_scenarios_usd=tuple(output_scenarios),
    )
