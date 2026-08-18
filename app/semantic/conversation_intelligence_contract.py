from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "4.9E4-v1"


class EvidenceStrength(StrEnum):
    EXPLICIT = "explicit"
    CONTEXTUAL = "contextual"


class FactType(StrEnum):
    DESTINATION = "destination"
    DEPARTURE_CITY = "departure_city"
    TRAVEL_DATE = "travel_date"
    DURATION = "duration"
    ADULTS = "adults"
    CHILDREN = "children"
    CHILD_AGE = "child_age"
    BUDGET = "budget"
    HOTEL_PREFERENCE = "hotel_preference"
    ROOM_PREFERENCE = "room_preference"
    MEAL_PREFERENCE = "meal_preference"
    FLIGHT_PREFERENCE = "flight_preference"
    SERVICE_REQUEST = "service_request"
    OTHER_PREFERENCE = "other_preference"


class IntentType(StrEnum):
    TOUR_SELECTION = "tour_selection"
    ONE_TIME_SERVICE = "one_time_service"
    INFORMATION_REQUEST = "information_request"
    COMPLEX_EXPERT_QUESTION = "complex_expert_question"
    COMPLAINT = "complaint"
    MANAGER_HANDOFF_REQUEST = "manager_handoff_request"
    OTHER = "other"


class ObjectionType(StrEnum):
    PRICE = "price"
    TIMING = "timing"
    AVAILABILITY = "availability"
    TRUST = "trust"
    COMPARISON = "comparison"
    CONDITIONS = "conditions"
    DOCUMENTS = "documents"
    OTHER = "other"


class ManagerActionType(StrEnum):
    ASKED_CLARIFYING_QUESTION = "asked_clarifying_question"
    PROVIDED_INFORMATION = "provided_information"
    SENT_OFFER = "sent_offer"
    SENT_LINK_OR_FILE = "sent_link_or_file"
    PROPOSED_NEXT_STEP = "proposed_next_step"
    PROMISED_FOLLOW_UP = "promised_follow_up"
    OTHER = "other"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(min_length=1)
    strength: EvidenceStrength = EvidenceStrength.EXPLICIT

    @model_validator(mode="after")
    def unique_message_ids(self) -> EvidenceRef:
        if len(self.message_ids) != len(set(self.message_ids)):
            raise ValueError("Evidence message_ids must be unique")
        return self


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: FactType
    value_text: str = Field(min_length=1, max_length=500)
    normalized_number: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=12)
    evidence: EvidenceRef


class IntentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentType
    detail: str | None = Field(default=None, max_length=500)
    evidence: EvidenceRef


class ObjectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objection: ObjectionType
    detail: str = Field(min_length=1, max_length=800)
    evidence: EvidenceRef


class EvidenceTextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    evidence: EvidenceRef


class ManagerActionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ManagerActionType
    detail: str = Field(min_length=1, max_length=800)
    evidence: EvidenceRef


class ManagerPromise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promise: str = Field(min_length=1, max_length=1000)
    due_text: str | None = Field(default=None, max_length=300)
    evidence: EvidenceRef


class SemanticChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["4.9E4-v1"] = SCHEMA_VERSION
    source_fingerprint_sha256: str = Field(
        min_length=64,
        max_length=64,
    )
    content_fingerprint_sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    language: str | None = Field(default=None, max_length=40)
    short_summary: str = Field(max_length=1200)

    customer_intents: list[IntentEvidence] = Field(default_factory=list)
    travel_and_service_facts: list[ExtractedFact] = Field(default_factory=list)
    customer_questions: list[EvidenceTextItem] = Field(default_factory=list)
    objections: list[ObjectionEvidence] = Field(default_factory=list)
    complaints: list[EvidenceTextItem] = Field(default_factory=list)

    manager_actions: list[ManagerActionEvidence] = Field(default_factory=list)
    manager_promises: list[ManagerPromise] = Field(default_factory=list)
    next_steps: list[EvidenceTextItem] = Field(default_factory=list)
    unanswered_customer_questions: list[EvidenceTextItem] = Field(default_factory=list)

    has_client_content: bool
    has_manager_content: bool

    analysis_notes: list[str] = Field(
        default_factory=list,
        description=("Only uncertainty/data-quality notes. Never manager quality judgments."),
    )
