"""Original workspace creation's concrete local Host and native owner."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from .creation_custody import CreationJournal, CreationRequest, original_creation
from .local_host_admission import child_database, context_fields
from .local_launch_owner import LocalLaunchOwner
from .models import SessionStatus
from .runtime_custody import RuntimeCustodyError, encode

if TYPE_CHECKING:
    from .creation_allocation import CreationFrontend


@dataclass
class CreationLaunchOwner(LocalLaunchOwner):
    frontend: CreationFrontend
    request: CreationRequest
    session_id: str
    handoff_id: str
    native_session_id: None = None

    @property
    def worktree_id(self) -> str:
        return self.read_launch()["handoff"]["workspace"]["id"]

    def read_launch(self) -> dict:
        return CreationJournal(self.frontend.db).read_launch(self.request, self.handoff_id)

    def require_custody(self, *, accepted: bool) -> None:
        report = self.read_launch()
        if (
            report["handoff"]["admission"] != self.admission.document()
            or report["handoff"]["consumer"]["session_id"] != self.session_id
            or report["handoff"]["consumer"]["native_session_id"] is not None
            or (report["result"] is not None) != accepted
        ):
            raise RuntimeCustodyError("Original creation launch changed or already accepted")

    def begin_launch(self) -> bool:
        return CreationJournal(self.frontend.db).begin_launch(
            self.request, self.frontend.installation, self.admission, self.handoff_id, now=time.time(),
        )

    def report_launch(self, native_session_id: str) -> None:
        CreationJournal(self.frontend.db).report_launch(
            self.request, self.handoff_id, native_session_id=native_session_id, now=time.time(),
        )

    def child_context(self, child_argv: list[str], cwd: str | None) -> str:
        self.require_current()
        if self._target is None or cwd != self._target.cwd:
            raise RuntimeCustodyError("Original creation child workspace changed")
        return encode({
            **context_fields(self, child_argv, cwd), "operation": "workspace-creation",
            "key_digest": self.request.key_digest, "facts_digest": self.request.facts_digest,
        })

    def require_ready(self) -> None:
        self.frontend.require_current()
        self.require_custody(accepted=True)
        session = self._session
        report = self.read_launch()
        if (
            session is None or self.frontend.manager.get_session(self.session_id) is not session
            or session.target is not self._target or session.target.to_json() != self._target_json
            or session.status != SessionStatus.IDLE or session._handoff_in_progress
            or session.client is None or session.client is not self._client
            or session.acp_session_id != report["result"]["native_session_id"]
            or session.client.acp_session_id != session.acp_session_id
        ):
            raise RuntimeCustodyError("Original creation accepted session changed")
        self._require_transport(session.client)


def require_child_admission(
    value: dict, *, cwd: str, session_id: str, argv: list[str] | None,
) -> None:
    if set(value) != {
        "version", "installation", "admission", "producer", "database_identity",
        "handoff_id", "session_id", "argv", "cwd", "operation", "key_digest", "facts_digest",
    } or type(value["version"]) is not int or value["version"] != 1:
        raise RuntimeCustodyError("Unsupported original creation child context")
    with child_database(value, cwd=cwd, session_id=session_id, argv=argv) as (db, admission):
        rows = db.execute_read(
            "SELECT * FROM workspace_embodiments WHERE key_digest=?", (value["key_digest"],),
        )
        original = original_creation(rows[0], "workspace") if rows else None
        if original is None or original.facts_digest != value["facts_digest"]:
            raise RuntimeCustodyError("Original creation child facts changed")
        report = CreationJournal(db).read_launch(original, value["handoff_id"])
        if (
            report["handoff"]["admission"] != admission.document()
            or report["handoff"]["consumer"]["session_id"] != session_id
            or report["handoff"]["workspace"]["path"] != cwd
            or report["started_at"] is None or report["result"] is not None
        ):
            raise RuntimeCustodyError("Original creation child launch is not outstanding")
