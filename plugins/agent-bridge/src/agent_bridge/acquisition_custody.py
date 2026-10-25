"""Original acquisition launch observations; native owner integration is separate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sqlite3
from typing import TYPE_CHECKING
import uuid

from .acquisition_contract import AcquisitionRequest
from .inbox_custody import observed_time
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, encode, nonblank,
)

if TYPE_CHECKING:
    from .db import Database


@dataclass(frozen=True)
class AcquisitionLaunch:
    session_id: str
    native_session_id: str | None
    consumer_incarnation: str

    def __post_init__(self) -> None:
        nonblank(self.session_id)
        nonblank(self.consumer_incarnation)
        if self.native_session_id is not None:
            nonblank(self.native_session_id)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeCustodyError("Duplicate acquisition custody field")
        result[key] = value
    return result


def decode(raw: str) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(value, dict):
            raise RuntimeCustodyError("Acquisition custody requires an object")
        return value
    except (ValueError, TypeError) as exc:
        raise RuntimeCustodyError("Malformed retained acquisition custody") from exc


def original_launch(row: sqlite3.Row | dict) -> dict:
    handoff = decode(row["launch_handoff_json"])
    try:
        if (
            set(handoff) != {"version", "id", "delivery_id", "request_digest", "admission", "consumer"}
            or type(handoff["version"]) is not int or handoff["version"] != 1
            or handoff["delivery_id"] != row["delivery_id"]
            or handoff["request_digest"] != row["request_digest"]
            or handoff["admission"] != row["runtime_custody_json"]
            or type(handoff["consumer"]) is not dict
        ):
            raise RuntimeCustodyError("Original acquisition launch changed")
        nonblank(handoff["id"])
        RuntimeAdmission.parse(handoff["admission"])
        AcquisitionLaunch(**handoff["consumer"])
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeCustodyError("Unsupported original acquisition launch") from exc
    return handoff


def launch_observation(row: sqlite3.Row | dict, handoff_id: str | None = None) -> dict:
    handoff = original_launch(row)
    if handoff_id is not None and handoff["id"] != handoff_id:
        raise RuntimeCustodyError("Acquisition launch handoff identity changed")
    if row["launch_started_at"] is not None:
        observed_time(row["launch_started_at"])
    result = decode(row["launch_result_json"]) if row["launch_result_json"] is not None else None
    if result is not None:
        if set(result) != {"native_session_id", "accepted_at"} or row["launch_started_at"] is None:
            raise RuntimeCustodyError("Unsupported acquisition launch outcome")
        nonblank(result["native_session_id"])
        observed_time(result["accepted_at"])
        if result["accepted_at"] < row["launch_started_at"]:
            raise RuntimeCustodyError("Acquisition launch acceptance precedes final admission")
        consumer = AcquisitionLaunch(**handoff["consumer"])
        if consumer.native_session_id not in (None, result["native_session_id"]):
            raise RuntimeCustodyError("Acquisition launch outcome changed native identity")
    return {
        "handoff": handoff, "started_at": row["launch_started_at"], "result": result,
        "stage": "native_accepted" if result is not None
        else "starting" if row["launch_started_at"] is not None else "retained",
    }


class _AcquisitionCustodyMixin:
    db: Database

    def original_admission(self, request: AcquisitionRequest) -> RuntimeAdmission | None:
        rows = self.db.execute_read(
            "SELECT runtime_custody_json FROM acquisition_attempts WHERE delivery_id=?",
            (request.delivery_id,),
        )
        return (
            RuntimeAdmission.parse(rows[0]["runtime_custody_json"])
            if rows and rows[0]["runtime_custody_json"] is not None else None
        )

    def _launch_row(self, conn: sqlite3.Connection, request: AcquisitionRequest) -> sqlite3.Row:
        from .acquisition_journal import _digest

        row = conn.execute(
            "SELECT * FROM acquisition_attempts WHERE delivery_id=?", (request.delivery_id,),
        ).fetchone()
        if row is None or row["request_digest"] != _digest(request):
            raise RuntimeCustodyError("Acquisition launch requires its original request")
        effect = conn.execute(
            "SELECT data_json FROM acquisition_effects WHERE delivery_id=?", (request.delivery_id,),
        ).fetchone()
        if effect is None:
            raise RuntimeCustodyError("Acquisition launch lost its original recovery facts")
        try:
            retained = AcquisitionRequest.model_validate(decode(effect["data_json"])["_request"])
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeCustodyError("Acquisition launch recovery facts are malformed") from exc
        if _digest(retained) != row["request_digest"]:
            raise RuntimeCustodyError("Acquisition launch recovery request changed")
        return row

    def prepare_launch(
        self, request: AcquisitionRequest, installation: RuntimeInstallation,
        admission: RuntimeAdmission, *, session_id: str, native_session_id: str | None,
        consumer_incarnation: str,
    ) -> dict:
        """Retain a prospective original owner, never upgrade legacy uncertainty."""
        consumer = AcquisitionLaunch(session_id, native_session_id, consumer_incarnation)
        if request.mode != "acp":
            raise RuntimeCustodyError("Acquisition launch custody supports internal ACP only")
        if (
            native_session_id is None and request.intent == "continue"
            or native_session_id is not None and (
                request.allocation == "created"
                or request.intent == "fresh" or request.conversation_retired is True
            )
        ):
            raise RuntimeCustodyError("Acquisition launch contradicts original conversation intent")
        retired_creation = native_session_id is None and request.conversation_retired is True
        if retired_creation and session_id == request.prior_session_id:
            raise RuntimeCustodyError("Acquisition creation cannot reuse the retired bridge identity")
        if not retired_creation and (
            request.prior_session_id not in (None, session_id)
            or request.prior_native_session_id not in (None, native_session_id)
        ):
            raise RuntimeCustodyError("Acquisition launch changed the carried conversation")
        identity = self.db._runtime_installation(installation)
        with self.db._runtime_transaction(write=True) as conn:
            self.db._runtime_admission(conn, identity, admission, cohort="Acquisition launch")
            row = self._launch_row(conn, request)
            if row["runtime_custody_json"] != admission.document() or row["state"] != "pending":
                raise RuntimeCustodyError("Acquisition launch requires original pending admission")
            if row["launch_handoff_json"] is not None:
                handoff = original_launch(row)
                if handoff["consumer"] != asdict(consumer):
                    raise RuntimeCustodyError("Acquisition launch owner cannot be replaced")
                return handoff
            effect = conn.execute(
                "SELECT stage FROM acquisition_effects WHERE delivery_id=?", (request.delivery_id,),
            ).fetchone()
            if effect is None or effect["stage"] not in ("prepared", "launching"):
                raise RuntimeCustodyError("Acquisition launch is no longer prospective")
            handoff = {
                "version": 1, "id": uuid.uuid4().hex, "delivery_id": request.delivery_id,
                "request_digest": row["request_digest"], "admission": admission.document(),
                "consumer": asdict(consumer),
            }
            conn.execute(
                "UPDATE acquisition_attempts SET launch_handoff_json=? WHERE delivery_id=?",
                (encode(handoff), request.delivery_id),
            )
            return handoff

    def begin_launch(
        self, request: AcquisitionRequest, installation: RuntimeInstallation,
        admission: RuntimeAdmission, handoff_id: str, *, now: float,
    ) -> bool:
        """One final DB admission; only a future wired owner may invoke its effect."""
        observed_time(now)
        identity = self.db._runtime_installation(installation)
        with self.db._runtime_transaction(write=True) as conn:
            self.db._runtime_admission(conn, identity, admission, cohort="Acquisition launch")
            row = self._launch_row(conn, request)
            handoff = original_launch(row)
            if (
                handoff["id"] != handoff_id or handoff["admission"] != admission.document()
                or row["state"] != "pending"
            ):
                raise RuntimeCustodyError("Acquisition launch requires its original pending handoff")
            if row["launch_started_at"] is not None:
                return False
            effect = conn.execute(
                "SELECT stage FROM acquisition_effects WHERE delivery_id=?", (request.delivery_id,),
            ).fetchone()
            if effect["stage"] not in ("prepared", "launching"):
                raise RuntimeCustodyError("Acquisition launch is no longer prospective")
            conn.execute(
                "UPDATE acquisition_attempts SET launch_started_at=? WHERE delivery_id=?",
                (now, request.delivery_id),
            )
            return True

    def read_launch(self, request: AcquisitionRequest, handoff_id: str | None = None) -> dict:
        """Identity-bound reporting remains available after closure or owner loss."""
        with self.db._runtime_transaction(write=False) as conn:
            row = self._launch_row(conn, request)
            return launch_observation(row, handoff_id)

    def report_launch(
        self, request: AcquisitionRequest, handoff_id: str, *,
        native_session_id: str, now: float,
    ) -> dict:
        """Record acceptance, not consumption, completion or permission to retry."""
        nonblank(native_session_id)
        observed_time(now)
        with self.db._runtime_transaction(write=True) as conn:
            row = self._launch_row(conn, request)
            report = launch_observation(row, handoff_id)
            if row["launch_started_at"] is None:
                raise RuntimeCustodyError("Acquisition launch never reached final admission")
            if now < row["launch_started_at"]:
                raise RuntimeCustodyError("Acquisition launch acceptance precedes final admission")
            consumer = AcquisitionLaunch(**report["handoff"]["consumer"])
            if consumer.native_session_id is None and native_session_id == request.prior_native_session_id:
                raise RuntimeCustodyError("Acquisition creation cannot reuse the retired native identity")
            if consumer.native_session_id not in (None, native_session_id):
                raise RuntimeCustodyError("Acquisition launch returned another native conversation")
            if report["result"] is not None:
                if report["result"]["native_session_id"] != native_session_id:
                    raise RuntimeCustodyError("Acquisition launch outcome cannot be replaced")
                return report
            conn.execute(
                "UPDATE acquisition_attempts SET launch_result_json=? WHERE delivery_id=?",
                (encode({"native_session_id": native_session_id, "accepted_at": now}),
                 request.delivery_id),
            )
            return launch_observation(self._launch_row(conn, request), handoff_id)
