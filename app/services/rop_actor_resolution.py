from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import aiosqlite

from app.semantic.repository import SemanticRepository
from app.services.rop_directory import load_rop_directory
from app.storage.crm_store import CrmStore

_SALES_OWNER_TYPES = frozenset({"1", "2"})


class ActorKind(StrEnum):
    DIRECTORY_USER = "directory_user"
    SPECIAL_ACTOR_CANDIDATE = "special_actor_candidate"
    UNRESOLVED_ACTOR = "unresolved_actor"


@dataclass(frozen=True, slots=True)
class ActorResolution:
    actor_id: str
    kind: ActorKind
    directory_mapped: bool
    employee_active: bool | None
    display_name: str
    technical_signals: tuple[str, ...]
    deal_references: int
    lead_references: int
    activity_references: int
    self_created_leads: int
    self_authored_activities: int

    @property
    def total_references(self) -> int:
        return self.deal_references + self.lead_references + self.activity_references

    @property
    def resolved(self) -> bool:
        return self.kind is not ActorKind.UNRESOLVED_ACTOR


@dataclass(frozen=True, slots=True)
class ActorResolutionReport:
    generated_at: datetime
    actors: tuple[ActorResolution, ...]
    notes: tuple[str, ...]

    @property
    def observed(self) -> int:
        return len(self.actors)

    @property
    def directory_users(self) -> int:
        return sum(item.kind is ActorKind.DIRECTORY_USER for item in self.actors)

    @property
    def special_actor_candidates(self) -> int:
        return sum(item.kind is ActorKind.SPECIAL_ACTOR_CANDIDATE for item in self.actors)

    @property
    def unresolved_actors(self) -> int:
        return sum(item.kind is ActorKind.UNRESOLVED_ACTOR for item in self.actors)

    @property
    def resolved(self) -> int:
        return sum(item.resolved for item in self.actors)


async def _load_active_payloads(
    database_path: str,
    entity_type: str,
) -> list[dict[str, Any]]:
    """Stream active payloads without a SQLite sort/temp-file requirement."""

    store = CrmStore(database_path)
    await store.initialize()

    result: list[dict[str, Any]] = []
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_active_entities
            WHERE entity_type = ?
            """,
            (entity_type,),
        )

        async for row in cursor:
            payload_json = row[0]
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                result.append(payload)

    return result


def _text_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _first_id(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text_id(item.get(key))
        if value is not None:
            return value
    return None


def _is_open_channel_source(value: Any) -> bool:
    if value in (None, ""):
        return False
    text = str(value).strip().upper()
    if "WZ_" in text:
        return True
    return any(marker in text for marker in ("|TELEGRAM", "|WHATSAPP", "|INSTAGRAM", "|VK"))


def _actor_sort_key(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


async def build_actor_resolution_report(
    database_path: str,
    *,
    now: datetime | None = None,
) -> ActorResolutionReport:
    """Resolve observed responsible/assigned IDs without inventing human-manager identity."""

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    repository = SemanticRepository(database_path)
    deals = await repository.deals()
    leads = await repository.leads()
    activities = await repository.activities()
    directory = await load_rop_directory(database_path)

    sales_activities = [item for item in activities if item.owner_entity_type in _SALES_OWNER_TYPES]

    actor_ids: set[str] = {
        item.assigned_user_id for item in deals if item.assigned_user_id is not None
    }
    actor_ids.update(item.assigned_user_id for item in leads if item.assigned_user_id is not None)
    actor_ids.update(
        item.responsible_user_id
        for item in sales_activities
        if item.responsible_user_id is not None
    )

    deal_refs: Counter[str] = Counter(
        item.assigned_user_id for item in deals if item.assigned_user_id is not None
    )
    lead_refs: Counter[str] = Counter(
        item.assigned_user_id for item in leads if item.assigned_user_id is not None
    )
    activity_refs: Counter[str] = Counter(
        item.responsible_user_id
        for item in sales_activities
        if item.responsible_user_id is not None
    )

    raw_leads = await _load_active_payloads(database_path, "lead")
    raw_activities = await _load_active_payloads(database_path, "activity")

    self_created_leads: Counter[str] = Counter()
    open_channel_created_leads: Counter[str] = Counter()
    for item in raw_leads:
        creator_id = _first_id(item, "CREATED_BY_ID", "CREATED_BY")
        if creator_id is None or creator_id not in actor_ids:
            continue
        self_created_leads[creator_id] += 1
        if _is_open_channel_source(item.get("SOURCE_ID")):
            open_channel_created_leads[creator_id] += 1

    self_authored_activities: Counter[str] = Counter()
    provider_responsible: defaultdict[str, Counter[str]] = defaultdict(Counter)
    provider_authored: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for item in raw_activities:
        owner_type = _text_id(item.get("OWNER_TYPE_ID"))
        if owner_type not in _SALES_OWNER_TYPES:
            continue

        provider = str(item.get("PROVIDER_ID") or "").strip().upper()
        responsible_id = _first_id(item, "RESPONSIBLE_ID")
        author_id = _first_id(item, "AUTHOR_ID", "CREATED_BY")

        if responsible_id is not None and responsible_id in actor_ids and provider:
            provider_responsible[responsible_id][provider] += 1
        if author_id is not None and author_id in actor_ids:
            self_authored_activities[author_id] += 1
            if provider:
                provider_authored[author_id][provider] += 1

    actors: list[ActorResolution] = []
    for actor_id in sorted(actor_ids, key=_actor_sort_key):
        identity = directory.users.get(actor_id)
        signals: list[str] = []
        responsible_providers = provider_responsible[actor_id]
        authored_providers = provider_authored[actor_id]

        if responsible_providers["IMOPENLINES_SESSION"]:
            signals.append("openlines_related")
        if authored_providers["IMOPENLINES_SESSION"]:
            signals.append("openlines_self_authored")
        if open_channel_created_leads[actor_id]:
            signals.append("open_channel_lead_creator")
        if responsible_providers["VOXIMPLANT_CALL"]:
            signals.append("telephony_related")
        if responsible_providers["CRM_TODO"]:
            signals.append("crm_todo_related")

        if identity is not None:
            kind = ActorKind.DIRECTORY_USER
            display_name = identity.display_name
            employee_active = identity.active
        elif (
            authored_providers["IMOPENLINES_SESSION"] > 0
            or open_channel_created_leads[actor_id] > 0
        ):
            kind = ActorKind.SPECIAL_ACTOR_CANDIDATE
            display_name = f"ID {actor_id}"
            employee_active = None
        else:
            kind = ActorKind.UNRESOLVED_ACTOR
            display_name = f"ID {actor_id}"
            employee_active = None

        actors.append(
            ActorResolution(
                actor_id=actor_id,
                kind=kind,
                directory_mapped=identity is not None,
                employee_active=employee_active,
                display_name=display_name,
                technical_signals=tuple(signals),
                deal_references=int(deal_refs[actor_id]),
                lead_references=int(lead_refs[actor_id]),
                activity_references=int(activity_refs[actor_id]),
                self_created_leads=int(self_created_leads[actor_id]),
                self_authored_activities=int(self_authored_activities[actor_id]),
            )
        )

    return ActorResolutionReport(
        generated_at=reference,
        actors=tuple(actors),
        notes=(
            "Actor resolution uses only the current active local CRM view.",
            (
                "directory_user means the ID exists in the synchronized Bitrix user "
                "directory; it does not by itself prove a sales-manager role."
            ),
            (
                "special_actor_candidate is a conservative local classification based "
                "on high-confidence Open Lines technical signatures; it is not proof "
                "that the actor is a bot, system account or integration identity."
            ),
            (
                "telephony_related and crm_todo_related are signals only and do not "
                "promote an absent ID to special_actor_candidate by themselves."
            ),
            (
                "unresolved_actor means identity type is not established "
                "from current read-only evidence."
            ),
            "No CRM write, user mutation or automatic repair is performed.",
        ),
    )


def format_actor_resolution_for_ai(
    report: ActorResolutionReport,
    *,
    actor_id: str | None = None,
) -> str:
    selected = list(report.actors)
    if actor_id is not None:
        selected = [item for item in selected if item.actor_id == str(actor_id)]

    lines = [
        "ИИ-РОП · Actor Resolution",
        f"Generated UTC: {report.generated_at.isoformat()}",
        "",
        "SUMMARY:",
        f"• observed responsible/assigned actor IDs: {report.observed}",
        f"• directory_user: {report.directory_users}",
        f"• special_actor_candidate: {report.special_actor_candidates}",
        f"• unresolved_actor: {report.unresolved_actors}",
        f"• resolved identity type: {report.resolved}/{report.observed}",
        "",
        "ACTORS:",
    ]

    if not selected:
        lines.append(
            "• no observed active sales references for this actor ID"
            if actor_id is not None
            else "• none"
        )
    else:
        for item in selected:
            signals = ", ".join(item.technical_signals) or "none"
            lines.append(
                f"• ID {item.actor_id} | kind {item.kind.value} | "
                f"directory_mapped {str(item.directory_mapped).lower()} | "
                f"refs deals/leads/activities "
                f"{item.deal_references}/{item.lead_references}/{item.activity_references} | "
                f"total {item.total_references} | signals {signals}"
            )

    lines.extend(["", "GUARDRAILS:"])
    for note in report.notes:
        lines.append(f"• {note}")
    lines.extend(
        [
            "• Do not call special_actor_candidate a confirmed bot or system user.",
            "• Do not call unresolved_actor deleted, inactive or fired.",
            (
                "• Do not treat a non-directory actor as a human manager for ranking, "
                "SLA blame or performance conclusions without separate identity evidence."
            ),
            "• Client text, contacts and message contents are not included.",
        ]
    )
    return "\n".join(lines)
