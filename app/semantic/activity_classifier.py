from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.semantic.models import SemanticActivity

_COMMUNICATION_TYPE_IDS = frozenset({"1", "2", "4"})
_USER_ACTION_TYPE_ID = "6"
_OUTGOING_DIRECTION = "2"


class ActivityClassification(StrEnum):
    CONFIRMED_COMMUNICATION = "confirmed_communication"
    HUMAN_ACTION = "human_action"
    SYSTEM_ACTIVITY = "system_activity"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ActivityEvidence:
    classification: ActivityClassification
    reason: str
    activity_type: str | None
    direction: str | None
    provider_id: str | None
    provider_type_id: str | None
    completed: bool
    autocomplete_rule: str | None

    @property
    def is_manager_evidence(self) -> bool:
        if self.classification is ActivityClassification.HUMAN_ACTION:
            return True
        if self.classification is not ActivityClassification.CONFIRMED_COMMUNICATION:
            return False
        if _autocomplete_triggered(self.autocomplete_rule):
            return False
        return self.activity_type in {"2", "4"} and self.direction == _OUTGOING_DIRECTION


def _autocomplete_triggered(value: str | None) -> bool:
    if value in (None, "", "0"):
        return False
    try:
        return int(value) > 0
    except ValueError:
        return False


def classify_activity(activity: SemanticActivity) -> ActivityEvidence:
    """Classify CRM activity evidence without guessing unsupported business meaning."""

    type_id = activity.activity_type

    if activity.completed and type_id in _COMMUNICATION_TYPE_IDS:
        return ActivityEvidence(
            classification=ActivityClassification.CONFIRMED_COMMUNICATION,
            reason="completed_standard_communication_type",
            activity_type=type_id,
            direction=activity.direction,
            provider_id=activity.provider_id,
            provider_type_id=activity.provider_type_id,
            completed=activity.completed,
            autocomplete_rule=activity.autocomplete_rule,
        )

    if activity.completed and _autocomplete_triggered(activity.autocomplete_rule):
        return ActivityEvidence(
            classification=ActivityClassification.SYSTEM_ACTIVITY,
            reason="completed_with_autocomplete_rule",
            activity_type=type_id,
            direction=activity.direction,
            provider_id=activity.provider_id,
            provider_type_id=activity.provider_type_id,
            completed=activity.completed,
            autocomplete_rule=activity.autocomplete_rule,
        )

    if activity.completed and type_id == _USER_ACTION_TYPE_ID:
        return ActivityEvidence(
            classification=ActivityClassification.HUMAN_ACTION,
            reason="completed_user_action_type",
            activity_type=type_id,
            direction=activity.direction,
            provider_id=activity.provider_id,
            provider_type_id=activity.provider_type_id,
            completed=activity.completed,
            autocomplete_rule=activity.autocomplete_rule,
        )

    return ActivityEvidence(
        classification=ActivityClassification.UNKNOWN,
        reason="insufficient_evidence",
        activity_type=type_id,
        direction=activity.direction,
        provider_id=activity.provider_id,
        provider_type_id=activity.provider_type_id,
        completed=activity.completed,
        autocomplete_rule=activity.autocomplete_rule,
    )
