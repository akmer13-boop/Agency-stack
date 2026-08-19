from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

TOURISM_B2C_PROFILE = "tourism_b2c"


@dataclass(frozen=True, slots=True)
class StoredLeadPolicyProfile:
    lead_id: int
    profile_key: str
    evidence_kind: str
    evidence_ref: str
    first_resolved_at: str
    last_confirmed_at: str


class RopLeadPolicyProfileStore:
    def __init__(
        self,
        database_path: str,
    ) -> None:
        self.database_path = database_path

    def _connect(
        self,
    ) -> sqlite3.Connection:
        path = Path(self.database_path)

        if path.parent != Path("."):
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        connection = sqlite3.connect(self.database_path)

        connection.row_factory = sqlite3.Row

        connection.execute("PRAGMA busy_timeout=5000")

        return connection

    def initialize(
        self,
    ) -> None:
        connection = self._connect()

        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS
                    rop_lead_policy_profile (
                        lead_id INTEGER PRIMARY KEY,
                        profile_key TEXT NOT NULL,
                        evidence_kind TEXT NOT NULL,
                        evidence_ref TEXT NOT NULL,
                        first_resolved_at TEXT NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,
                        last_confirmed_at TEXT NOT NULL
                            DEFAULT CURRENT_TIMESTAMP,
                        CHECK (
                            profile_key =
                            'tourism_b2c'
                        )
                    )
                """
            )

            connection.commit()

        finally:
            connection.close()

    def get(
        self,
        lead_id: int,
    ) -> StoredLeadPolicyProfile | None:
        self.initialize()

        connection = self._connect()

        try:
            row = connection.execute(
                """
                SELECT
                    lead_id,
                    profile_key,
                    evidence_kind,
                    evidence_ref,
                    first_resolved_at,
                    last_confirmed_at
                FROM rop_lead_policy_profile
                WHERE lead_id = ?
                LIMIT 1
                """,
                (lead_id,),
            ).fetchone()

        finally:
            connection.close()

        if row is None:
            return None

        return StoredLeadPolicyProfile(
            lead_id=int(row["lead_id"]),
            profile_key=str(row["profile_key"]),
            evidence_kind=str(row["evidence_kind"]),
            evidence_ref=str(row["evidence_ref"]),
            first_resolved_at=str(row["first_resolved_at"]),
            last_confirmed_at=str(row["last_confirmed_at"]),
        )

    def confirm_tourism_b2c(
        self,
        *,
        lead_id: int,
        evidence_kind: str,
        evidence_ref: str,
    ) -> StoredLeadPolicyProfile:
        if lead_id <= 0:
            raise ValueError("lead_id_invalid")

        kind = (evidence_kind.strip() or "unknown")[:80]

        ref = (evidence_ref.strip() or "unknown")[:160]

        self.initialize()

        connection = self._connect()

        try:
            connection.execute(
                """
                INSERT INTO
                    rop_lead_policy_profile (
                        lead_id,
                        profile_key,
                        evidence_kind,
                        evidence_ref
                    )
                VALUES (
                    ?,
                    'tourism_b2c',
                    ?,
                    ?
                )
                ON CONFLICT (lead_id)
                DO UPDATE SET
                    profile_key =
                        'tourism_b2c',
                    evidence_kind =
                        excluded.evidence_kind,
                    evidence_ref =
                        excluded.evidence_ref,
                    last_confirmed_at =
                        CURRENT_TIMESTAMP
                """,
                (
                    lead_id,
                    kind,
                    ref,
                ),
            )

            connection.commit()

        finally:
            connection.close()

        result = self.get(lead_id)

        if result is None:
            raise RuntimeError("lead_policy_profile_persist_failed")

        return result
