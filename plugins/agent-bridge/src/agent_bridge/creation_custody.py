"""Original keyed-creation DB custody, not allocation or native authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from typing import TYPE_CHECKING, Literal
import uuid

from .acquisition_contract import AcquisitionRefused
from .acquisition_custody import decode
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, encode, nonblank,
)

if TYPE_CHECKING:
    from .db import Database

CreationKind = Literal["workspace", "provider"]
TABLES = {"workspace": "workspace_embodiments", "provider": "provider_embodiments"}


def creation_table(kind: CreationKind) -> str:
    if type(kind) is not str or kind not in TABLES:
        raise RuntimeCustodyError("Unsupported original creation kind")
    return TABLES[kind]


@dataclass(frozen=True)
class CreationRequest:
    kind: CreationKind
    key_digest: str
    facts_json: str

    def __post_init__(self) -> None:
        creation_table(self.kind)
        if type(self.key_digest) is not str or re.fullmatch(r"[0-9a-f]{64}", self.key_digest) is None:
            raise RuntimeCustodyError("Creation requires its original key digest")
        facts = decode(self.facts_json)
        fields = {"target", "options", "prompt"} if self.kind == "workspace" else {"target", "options"}
        if (
            set(facts) != fields or type(facts["target"]) is not dict
            or type(facts["options"]) is not dict
            or self.kind == "workspace" and facts["prompt"] is not None
            and type(facts["prompt"]) is not str
        ):
            raise RuntimeCustodyError("Unsupported original creation facts")
        try:
            if encode(facts) != self.facts_json:
                raise RuntimeCustodyError("Creation facts must retain their canonical document")
        except (TypeError, ValueError) as exc:
            raise RuntimeCustodyError("Malformed original creation facts") from exc

    @classmethod
    def capture(cls, kind: CreationKind, key_digest: str, facts: dict) -> CreationRequest:
        try:
            document = encode(facts)
        except (TypeError, ValueError) as exc:
            raise RuntimeCustodyError("Malformed original creation facts") from exc
        return cls(kind, key_digest, document)

    @property
    def facts_digest(self) -> str:
        # Match the existing creation owners' digest, including JSON spacing.
        return hashlib.sha256(json.dumps(decode(self.facts_json), sort_keys=True).encode()).hexdigest()


def original_creation(row: sqlite3.Row | dict, kind: CreationKind) -> CreationRequest | None:
    association, facts = row["runtime_custody_json"], row["creation_facts_json"]
    if association is None and facts is None:
        return None
    if association is None or facts is None:
        raise RuntimeCustodyError("Creation lost its original admission or facts")
    RuntimeAdmission.parse(association)
    original = CreationRequest(kind, row["key_digest"], facts)
    nonblank(row["session_id"])
    if original.facts_digest != row["facts_digest"]:
        raise RuntimeCustodyError("Creation facts disagree with their original digest")
    return original


def legacy_creation(
    db: Database, kind: CreationKind, digest: str, facts_digest: str,
) -> dict:
    """Keep legacy replay observational; associated rows cannot downgrade."""
    table = creation_table(kind)
    rows = db.execute_read(f"SELECT * FROM {table} WHERE key_digest=?", (digest,))
    if not rows:
        state = "allocating" if kind == "workspace" else "prepared"
        db.execute_write(
            f"INSERT INTO {table} (key_digest,facts_digest,state,session_id) VALUES (?,?,?,?)",
            (digest, facts_digest, state, str(uuid.uuid4())[:12]),
        )
        rows = db.execute_read(f"SELECT * FROM {table} WHERE key_digest=?", (digest,))
    row = dict(rows[0])
    if row["runtime_custody_json"] is not None or row["creation_facts_json"] is not None:
        raise AcquisitionRefused(
            "creation_custody_required",
            "original creation custody cannot use the ordinary allocation or launch path",
            effect="not_started",
        )
    return row


class CreationJournal:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _original(
        row: sqlite3.Row | None, request: CreationRequest, admission: RuntimeAdmission | None = None,
    ) -> dict:
        if row is None:
            raise RuntimeCustodyError("Creation requires its retained original attempt")
        if original_creation(row, request.kind) != request:
            raise RuntimeCustodyError("Creation requires its original admission and facts")
        if admission is not None and row["runtime_custody_json"] != admission.document():
            raise RuntimeCustodyError("Creation cannot replace its original admission")
        return dict(row)

    def claim(
        self, request: CreationRequest, installation: RuntimeInstallation, admission: RuntimeAdmission,
    ) -> dict:
        """Retain original inputs before effects; replay does not grant effects."""
        if type(request) is not CreationRequest or type(admission) is not RuntimeAdmission:
            raise RuntimeCustodyError("Creation requires original request and admission observations")
        identity = self.db._runtime_installation(installation)
        if admission.installation != identity:
            raise RuntimeCustodyError("Creation admission belongs to another installation")
        table = creation_table(request.kind)
        with self.db._runtime_transaction(write=True) as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE key_digest=?", (request.key_digest,),
            ).fetchone()
            if row is not None:
                return self._original(row, request, admission)
            self.db._runtime_admission(conn, identity, admission, cohort="Creation")
            state = "allocating" if request.kind == "workspace" else "prepared"
            conn.execute(
                f"INSERT INTO {table} (key_digest,facts_digest,state,session_id,"
                "runtime_custody_json,creation_facts_json) VALUES (?,?,?,?,?,?)",
                (request.key_digest, request.facts_digest, state, str(uuid.uuid4())[:12],
                 admission.document(), request.facts_json),
            )
            return self._original(conn.execute(
                f"SELECT * FROM {table} WHERE key_digest=?", (request.key_digest,),
            ).fetchone(), request, admission)

    def read(self, request: CreationRequest) -> dict:
        """Recover the same attempt after closure, lost replies or process exit."""
        if type(request) is not CreationRequest:
            raise RuntimeCustodyError("Creation requires its original request")
        table = creation_table(request.kind)
        with self.db._runtime_transaction(write=False) as conn:
            return self._original(conn.execute(
                f"SELECT * FROM {table} WHERE key_digest=?", (request.key_digest,),
            ).fetchone(), request)
