from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.semantic.conversation_intelligence_contract import (
    SemanticChunkExtraction,
)
from app.services.conversation_semantic_plan import (
    STATIC_EXTRACTION_INSTRUCTIONS,
    build_semantic_run_plan,
    extraction_instructions_sha256,
    extraction_schema_sha256,
)


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:.2f}"


async def _main() -> None:
    settings = Settings()
    plan = await build_semantic_run_plan(
        settings.database_path,
        settings.openai_model,
        sample_size=40,
    )

    schema_json = json.dumps(
        SemanticChunkExtraction.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )

    print("=" * 92)
    print(" CONVERSATION INTELLIGENCE · MODEL / TOKEN / SCHEMA DRY-RUN PLAN")
    print("=" * 92)
    print("Configured model         :", plan.configured_model)
    print("LLM/API calls            : NONE")
    print("Text chunks              :", plan.text_chunks)
    print("Unique content chunks    :", plan.unique_text_chunks)
    print("Reusable duplicates      :", plan.reusable_duplicate_chunks)
    print("Unique text chars        :", plan.unique_text_chars)
    print("Dry-run sample rows      :", plan.sample_size)
    print()
    print("Schema version           : 4.9E4-v1")
    print("Schema JSON chars        :", len(schema_json))
    print("Schema SHA256            :", extraction_schema_sha256())
    print("Instruction chars        :", len(STATIC_EXTRACTION_INSTRUCTIONS))
    print("Instruction SHA256       :", extraction_instructions_sha256())
    print()
    print("Tokenizer mode           :", plan.token_estimate.tokenizer_mode)

    if plan.token_estimate.content_tokens is not None:
        print(
            "Unique content tokens    :",
            plan.token_estimate.content_tokens,
        )
        print(
            "Static prompt tokens/req :",
            plan.token_estimate.static_prompt_tokens,
        )
        print(
            "Full unique input tokens :",
            plan.token_estimate.full_unique_input_tokens,
        )
        print(
            "Est. GPT-5 mini input $  :",
            _money(plan.estimated_input_cost_usd),
        )
    else:
        print("Exact local tokens       : unavailable (tiktoken not installed)")
        print(
            "Fallback token envelope  :",
            f"{plan.token_estimate.fallback_low_tokens} .. "
            f"{plan.token_estimate.fallback_high_tokens}",
        )
        print(
            "Fallback midpoint        :",
            plan.token_estimate.fallback_mid_tokens,
        )
        print("Input cost claim         : NOT MADE")

    if plan.output_cost_scenarios_usd:
        print()
        print("Output-cost scenarios for ALL unique chunks")
        print("(budget assumption only; no request is sent):")
        for output_tokens, cost in plan.output_cost_scenarios_usd:
            print(f"  {output_tokens:4d} output tok/request : ${cost:.2f}")

    print()
    print("===== 40-CHUNK DETERMINISTIC SAMPLE PLAN =====")
    print("No message text is printed.")
    for index, row in enumerate(plan.sample_rows, start=1):
        print(
            f"#{index:02d} "
            f"chat={row.chat_id} "
            f"episode={row.episode_index} "
            f"chunk={row.chunk_index} "
            f"channel={row.channel} "
            f"scope={row.scope} "
            f"band={row.size_band} "
            f"chars={row.text_chars}"
        )

    print()
    print("===== EXTRACTION CONTRACT =====")
    print("Extract:")
    print("• customer intent(s);")
    print("• travel/service facts with message evidence;")
    print("• customer questions;")
    print("• objections and complaints;")
    print("• manager actions and explicit promises;")
    print("• next steps;")
    print("• unanswered customer questions;")
    print("• compact factual summary.")
    print()
    print("Explicitly DO NOT extract:")
    print("• SLA compliance / breach;")
    print("• manager rating or good/bad score;")
    print("• blame or causal reason for LOST/WON;")
    print("• current CRM ownership inferred from historical text;")
    print("• facts without evidence message IDs.")
    print()
    print("DECISION                 : READY FOR SMALL PAID E5 PILOT")
    print("Recommended first pilot  : 40 unique text chunks above")
    print("Full-corpus execution    : NOT APPROVED / NOT STARTED")
    print("=" * 92)


if __name__ == "__main__":
    asyncio.run(_main())
