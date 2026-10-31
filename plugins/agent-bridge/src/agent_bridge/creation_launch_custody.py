"""Original native observations on each existing creation row."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
import uuid

from .acquisition_custody import decode
from .inbox_custody import observed_time
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, encode, nonblank

if TYPE_CHECKING:
    from .creation_custody import CreationKind, CreationRequest
    from .db import Database

LAUNCH_COLUMNS = (
    "launch_handoff_json", "launch_started_at", "launch_result_json",
    "binding_started_at", "binding_accepted_at",
)
PROVIDER_LAUNCH_COLUMNS = LAUNCH_COLUMNS[:3]


def launch_observation(
    row: sqlite3.Row | dict, handoff_id: str | None = None, *, kind: CreationKind = "workspace",
) -> dict | None:
    from .creation_custody import creation_table

    creation_table(kind)
    workspace = kind == "workspace"
    columns = LAUNCH_COLUMNS if workspace else PROVIDER_LAUNCH_COLUMNS
    raw = row["launch_handoff_json"]
    if raw is None:
        if any(row[name] is not None for name in columns[1:]):
            raise RuntimeCustodyError("Creation launch lost its original handoff")
        return None
    handoff = decode(raw)
    placement = "workspace" if workspace else "provider"
    original_placement = decode(row["worktree_json"]) if workspace else decode(row["creation_facts_json"])["target"]
    if (
        set(handoff) != {
            "version", "id", "key_digest", "facts_digest", "admission", "consumer", placement,
        }
        or type(handoff["version"]) is not int or handoff["version"] != 1
        or handoff["key_digest"] != row["key_digest"]
        or handoff["facts_digest"] != row["facts_digest"]
        or handoff["admission"] != row["runtime_custody_json"]
        or handoff[placement] != original_placement
        or type(handoff["consumer"]) is not dict
        or set(handoff["consumer"]) != {"session_id", "native_session_id", "consumer_incarnation"}
        or handoff["consumer"]["session_id"] != row["session_id"]
        or handoff["consumer"]["native_session_id"] is not None
    ):
        raise RuntimeCustodyError("Original creation launch changed")
    nonblank(handoff["id"])
    nonblank(handoff["consumer"]["consumer_incarnation"])
    RuntimeAdmission.parse(handoff["admission"])
    if handoff_id is not None and handoff["id"] != handoff_id:
        raise RuntimeCustodyError("Original creation launch handoff changed")
    started = row["launch_started_at"]
    result = decode(row["launch_result_json"]) if row["launch_result_json"] is not None else None
    if started is not None:
        observed_time(started)
    if result is not None:
        if set(result) != {"native_session_id", "accepted_at"} or started is None:
            raise RuntimeCustodyError("Unsupported original creation native outcome")
        nonblank(result["native_session_id"])
        observed_time(result["accepted_at"])
        if result["accepted_at"] < started or result["native_session_id"] != row["native_session_id"]:
            raise RuntimeCustodyError("Original creation native acceptance changed")
    elif row["native_session_id"] is not None:
        raise RuntimeCustodyError("Original creation native identity has no acceptance")
    report = {"handoff": handoff, "started_at": started, "result": result}
    if not workspace:
        expected_state = "complete" if result is not None else "launching" if started is not None else "preparing"
        if row["state"] != expected_state:
            raise RuntimeCustodyError("Creation state disagrees with its original native observations")
        return report
    binding_started, binding_accepted = row["binding_started_at"], row["binding_accepted_at"]
    if binding_started is not None:
        observed_time(binding_started)
        if result is None or binding_started < result["accepted_at"]:
            raise RuntimeCustodyError("Creation binding precedes original native acceptance")
    if binding_accepted is not None:
        observed_time(binding_accepted)
        if binding_started is None or binding_accepted < binding_started:
            raise RuntimeCustodyError("Creation binding acceptance precedes its original attempt")
    expected_states = (
        ("bound", "delivering", "complete") if binding_accepted is not None
        else ("binding_started",) if binding_started is not None
        else ("native_accepted",) if result is not None
        else ("launching",) if started is not None else ("preparing",)
    )
    if row["state"] not in expected_states:
        raise RuntimeCustodyError("Creation state disagrees with its original native observations")
    turn_index = row["turn_index"]
    prompt = decode(row["creation_facts_json"])["prompt"]
    if (
        turn_index is not None and (
            type(turn_index) is not int or turn_index != 0
            or row["state"] not in ("delivering", "complete") or not prompt
        )
        or row["state"] == "delivering" and turn_index is None
        or row["state"] == "complete" and bool(prompt) != (turn_index is not None)
    ):
        raise RuntimeCustodyError("Creation first-turn observation changed")
    return {
        **report,
        "binding_started_at": binding_started, "binding_accepted_at": binding_accepted,
    }


class _CreationLaunchCustodyMixin:
    db: Database

    def _launch_row(
        self, conn: sqlite3.Connection, request: CreationRequest,
        admission: RuntimeAdmission | None = None,
    ) -> dict:
        from .creation_custody import creation_table

        table = creation_table(request.kind)
        return self._original(conn.execute(
            f"SELECT * FROM {table} WHERE key_digest=?", (request.key_digest,),
        ).fetchone(), request, admission)

    @staticmethod
    def _workspace_only(request: CreationRequest) -> None:
        if request.kind != "workspace":
            raise RuntimeCustodyError("Creation binding and first prompt support original workspaces only")

    def prepare_launch(
        self, request: CreationRequest, installation: RuntimeInstallation,
        admission: RuntimeAdmission, *, consumer_incarnation: str,
    ) -> dict:
        """Retain one prospective native owner before any launch preparation."""
        from .creation_custody import creation_table

        nonblank(consumer_incarnation)
        identity = self.db._runtime_installation(installation)
        if identity != admission.installation:
            raise RuntimeCustodyError("Creation native launch belongs to another installation")
        with self.db._runtime_transaction(write=True) as conn:
            row = self._launch_row(conn, request, admission)
            report = launch_observation(row, kind=request.kind)
            if report is not None:
                if report["handoff"]["consumer"]["consumer_incarnation"] != consumer_incarnation:
                    raise RuntimeCustodyError("Original creation launch owner cannot be replaced")
                return report["handoff"]
            self.db._runtime_admission(conn, identity, admission, cohort="Creation native launch")
            workspace = request.kind == "workspace"
            allocation_missing = workspace and (
                row["state"] != "allocated" or row["worktree_json"] is None or row["turn_index"] is not None
            )
            if (
                row["native_session_id"] is not None or allocation_missing
                or not workspace and row["state"] != "prepared"
            ):
                raise RuntimeCustodyError("Creation native launch requires its original prepared placement")
            handoff = {
                "version": 1, "id": uuid.uuid4().hex, "key_digest": request.key_digest,
                "facts_digest": request.facts_digest, "admission": admission.document(),
                "consumer": {
                    "session_id": row["session_id"], "native_session_id": None,
                    "consumer_incarnation": consumer_incarnation,
                },
                "workspace" if workspace else "provider": (
                    decode(row["worktree_json"]) if workspace else decode(request.facts_json)["target"]
                ),
            }
            conn.execute(
                f"UPDATE {creation_table(request.kind)} SET state='preparing',launch_handoff_json=? "
                "WHERE key_digest=?", (encode(handoff), request.key_digest),
            )
            return handoff

    def read_launch(self, request: CreationRequest, handoff_id: str | None = None) -> dict:
        """Recover original observations after closure; do not reacquire effects."""
        with self.db._runtime_transaction(write=False) as conn:
            report = launch_observation(self._launch_row(conn, request), handoff_id, kind=request.kind)
            if report is None:
                raise RuntimeCustodyError("Creation has no original native handoff")
            return report

    def begin_launch(
        self, request: CreationRequest, installation: RuntimeInstallation,
        admission: RuntimeAdmission, handoff_id: str, *, now: float,
    ) -> bool:
        from .creation_custody import creation_table

        observed_time(now)
        identity = self.db._runtime_installation(installation)
        with self.db._runtime_transaction(write=True) as conn:
            self.db._runtime_admission(conn, identity, admission, cohort="Creation native launch")
            row = self._launch_row(conn, request, admission)
            report = launch_observation(row, handoff_id, kind=request.kind)
            if report is None:
                raise RuntimeCustodyError("Creation requires its original native handoff")
            if report["started_at"] is not None:
                return False
            conn.execute(
                f"UPDATE {creation_table(request.kind)} SET state='launching',launch_started_at=? WHERE key_digest=?",
                (now, request.key_digest),
            )
            return True

    def report_launch(
        self, request: CreationRequest, handoff_id: str, *, native_session_id: str, now: float,
    ) -> dict:
        from .creation_custody import creation_table

        nonblank(native_session_id)
        observed_time(now)
        with self.db._runtime_transaction(write=True) as conn:
            report = launch_observation(self._launch_row(conn, request), handoff_id, kind=request.kind)
            if report is None or report["started_at"] is None or now < report["started_at"]:
                raise RuntimeCustodyError("Creation native result has no original started handoff")
            if report["result"] is not None:
                if report["result"]["native_session_id"] != native_session_id:
                    raise RuntimeCustodyError("Original creation native outcome cannot be replaced")
                return report
            conn.execute(
                f"UPDATE {creation_table(request.kind)} SET state=?,native_session_id=?,"
                "launch_result_json=? WHERE key_digest=?",
                ("native_accepted" if request.kind == "workspace" else "complete",
                 native_session_id, encode({"native_session_id": native_session_id, "accepted_at": now}),
                 request.key_digest),
            )
            return launch_observation(self._launch_row(conn, request), handoff_id, kind=request.kind)

    def begin_binding(
        self, request: CreationRequest, installation: RuntimeInstallation,
        admission: RuntimeAdmission, handoff_id: str, *, now: float,
    ) -> bool:
        self._workspace_only(request)
        observed_time(now)
        identity = self.db._runtime_installation(installation)
        with self.db._runtime_transaction(write=True) as conn:
            self.db._runtime_admission(conn, identity, admission, cohort="Creation native binding")
            report = launch_observation(self._launch_row(conn, request, admission), handoff_id)
            if report is None or report["result"] is None or now < report["result"]["accepted_at"]:
                raise RuntimeCustodyError("Creation binding requires its original native acceptance")
            if report["binding_started_at"] is not None:
                return False
            conn.execute(
                "UPDATE workspace_embodiments SET state='binding_started',binding_started_at=? "
                "WHERE key_digest=?", (now, request.key_digest),
            )
            return True

    def report_binding(self, request: CreationRequest, handoff_id: str, *, now: float) -> dict:
        self._workspace_only(request)
        observed_time(now)
        with self.db._runtime_transaction(write=True) as conn:
            report = launch_observation(self._launch_row(conn, request), handoff_id)
            if report is None or report["binding_started_at"] is None or now < report["binding_started_at"]:
                raise RuntimeCustodyError("Creation binding result has no original started handoff")
            if report["binding_accepted_at"] is None:
                conn.execute(
                    "UPDATE workspace_embodiments SET state='bound',binding_accepted_at=? WHERE key_digest=?",
                    (now, request.key_digest),
                )
            return launch_observation(self._launch_row(conn, request), handoff_id)

    def begin_delivery(
        self, request: CreationRequest, installation: RuntimeInstallation,
        admission: RuntimeAdmission, handoff_id: str, *, turn_index: int,
    ) -> None:
        self._workspace_only(request)
        if type(turn_index) is not int or turn_index != 0:
            raise RuntimeCustodyError("Creation first prompt requires its original zero turn")
        identity = self.db._runtime_installation(installation)
        with self.db._runtime_transaction(write=True) as conn:
            self.db._runtime_admission(conn, identity, admission, cohort="Creation first prompt")
            row = self._launch_row(conn, request, admission)
            report = launch_observation(row, handoff_id)
            if (
                report is None or report["binding_accepted_at"] is None
                or row["state"] != "bound" or row["turn_index"] is not None
            ):
                raise RuntimeCustodyError("Creation first prompt remains held; do not replay")
            conn.execute(
                "UPDATE workspace_embodiments SET state='delivering',turn_index=? WHERE key_digest=?",
                (turn_index, request.key_digest),
            )

    def complete_launch(self, request: CreationRequest, handoff_id: str) -> None:
        """Observe a retained binding and, when requested, its exact first-turn row."""
        self._workspace_only(request)
        with self.db._runtime_transaction(write=True) as conn:
            row = self._launch_row(conn, request)
            report = launch_observation(row, handoff_id)
            if report is None or report["binding_accepted_at"] is None:
                raise RuntimeCustodyError("Creation completion has no original accepted binding")
            prompt = decode(request.facts_json)["prompt"]
            if prompt:
                turns = conn.execute(
                    "SELECT session_id,turn_index,prompt,runtime_custody_json FROM turns "
                    "WHERE acquisition_delivery_id=?", (f"creation:{request.key_digest}",),
                ).fetchall()
                if len(turns) != 1 or (
                    turns[0]["session_id"] != row["session_id"]
                    or turns[0]["turn_index"] != row["turn_index"]
                    or turns[0]["prompt"] != prompt
                    or turns[0]["runtime_custody_json"] != row["runtime_custody_json"]
                ):
                    raise RuntimeCustodyError("Creation completion lacks its original prompt admission")
            elif row["turn_index"] is not None:
                raise RuntimeCustodyError("Creation without a prompt cannot acquire a first turn")
            if row["state"] != "complete":
                conn.execute(
                    "UPDATE workspace_embodiments SET state='complete' WHERE key_digest=?",
                    (request.key_digest,),
                )
