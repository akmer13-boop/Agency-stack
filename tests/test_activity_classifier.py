from app.semantic.activity_classifier import (
    ActivityClassification,
    classify_activity,
)
from app.semantic.normalizer import normalize_activity


def _activity(**overrides: object):
    payload: dict[str, object] = {
        "ID": "1",
        "OWNER_TYPE_ID": "1",
        "OWNER_ID": "100",
        "TYPE_ID": "3",
        "COMPLETED": "Y",
    }
    payload.update(overrides)
    return normalize_activity(payload)


def test_outgoing_completed_call_is_manager_evidence() -> None:
    evidence = classify_activity(_activity(TYPE_ID="2", DIRECTION="2"))
    assert evidence.classification is ActivityClassification.CONFIRMED_COMMUNICATION
    assert evidence.is_manager_evidence is True


def test_incoming_completed_call_is_not_manager_evidence() -> None:
    evidence = classify_activity(_activity(TYPE_ID="2", DIRECTION="1"))
    assert evidence.classification is ActivityClassification.CONFIRMED_COMMUNICATION
    assert evidence.is_manager_evidence is False


def test_completed_meeting_is_not_manager_evidence_without_approved_rule() -> None:
    evidence = classify_activity(_activity(TYPE_ID="1"))
    assert evidence.classification is ActivityClassification.CONFIRMED_COMMUNICATION
    assert evidence.is_manager_evidence is False


def test_completed_user_action_is_human_action() -> None:
    evidence = classify_activity(_activity(TYPE_ID="6"))
    assert evidence.classification is ActivityClassification.HUMAN_ACTION
    assert evidence.is_manager_evidence is True


def test_autocompleted_task_is_system_activity() -> None:
    evidence = classify_activity(_activity(TYPE_ID="3", AUTOCOMPLETE_RULE="1"))
    assert evidence.classification is ActivityClassification.SYSTEM_ACTIVITY
    assert evidence.is_manager_evidence is False


def test_completed_task_without_strong_evidence_is_unknown() -> None:
    evidence = classify_activity(_activity(TYPE_ID="3", AUTOCOMPLETE_RULE="0"))
    assert evidence.classification is ActivityClassification.UNKNOWN


def test_incomplete_call_is_unknown() -> None:
    evidence = classify_activity(_activity(TYPE_ID="2", DIRECTION="2", COMPLETED="N"))
    assert evidence.classification is ActivityClassification.UNKNOWN


def test_autocompleted_outgoing_email_is_not_manager_evidence() -> None:
    evidence = classify_activity(_activity(TYPE_ID="4", DIRECTION="2", AUTOCOMPLETE_RULE="1"))
    assert evidence.classification is ActivityClassification.CONFIRMED_COMMUNICATION
    assert evidence.is_manager_evidence is False
