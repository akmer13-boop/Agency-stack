from app.semantic.conversation_intelligence_contract import (
    EvidenceRef,
    EvidenceStrength,
    ExtractedFact,
    FactType,
    SemanticChunkExtraction,
)
from app.services.conversation_semantic_plan import (
    ChunkPlanRow,
    deterministic_sample,
    extraction_instructions_sha256,
    extraction_schema_sha256,
)


def _row(
    *,
    chat: str,
    channel: str,
    scope: str,
    band: str,
    content_hash: str,
) -> ChunkPlanRow:
    return ChunkPlanRow(
        chat_id=chat,
        episode_index=1,
        chunk_index=1,
        channel=channel,
        scope=scope,
        size_band=band,
        text_chars=100,
        source_fingerprint_sha256="a" * 64,
        content_fingerprint_sha256=content_hash,
    )


def test_semantic_contract_requires_evidence_ids() -> None:
    extraction = SemanticChunkExtraction(
        source_fingerprint_sha256="a" * 64,
        content_fingerprint_sha256="b" * 64,
        short_summary="Клиент запросил тур.",
        customer_intents=[],
        travel_and_service_facts=[
            ExtractedFact(
                fact_type=FactType.DESTINATION,
                value_text="Турция",
                evidence=EvidenceRef(
                    message_ids=["123"],
                    strength=EvidenceStrength.EXPLICIT,
                ),
            )
        ],
        has_client_content=True,
        has_manager_content=False,
    )

    assert extraction.schema_version == "4.9E4-v1"
    assert extraction.travel_and_service_facts[0].evidence.message_ids == ["123"]


def test_schema_and_instruction_hashes_are_stable_shape() -> None:
    assert len(extraction_schema_sha256()) == 64
    assert len(extraction_instructions_sha256()) == 64


def test_deterministic_sample_round_robins_groups() -> None:
    rows = [
        _row(
            chat="1",
            channel="Telegram",
            scope="dialogue",
            band="S_0_1k",
            content_hash="1" * 64,
        ),
        _row(
            chat="2",
            channel="Telegram",
            scope="dialogue",
            band="S_0_1k",
            content_hash="2" * 64,
        ),
        _row(
            chat="3",
            channel="WhatsApp",
            scope="client_only",
            band="M_1_4k",
            content_hash="3" * 64,
        ),
        _row(
            chat="4",
            channel="MAX",
            scope="dialogue",
            band="XL_8_10k",
            content_hash="4" * 64,
        ),
    ]

    sample = deterministic_sample(rows, 3)

    assert len(sample) == 3
    assert len({(item.channel, item.scope, item.size_band) for item in sample}) == 3
