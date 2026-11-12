"""Native local standing admission, separate from ordinary finite allocation."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from .assignment_admission import StandingAdmission
from .assignment_relationship_owner import WorktreeRelationshipWriter
from .assignment_workspace import AssignmentWorkspace
from .identity import canonicalize_remote, resolve_machine
from .ordinary_writer_admission import OrdinaryWriterAdmission
from .pool_source_snapshot import read_supervisor_declarations
from .procutil import agent_bridge_launch_prefix
from .queue_records import TaskError
from .registrar_discovery import RegistrarSources
from .registrar_standing import standing_source_entries


def _materialization_path(root: Path, reference: str) -> Path:
    windows, posix = PureWindowsPath(reference), PurePosixPath(reference)
    if windows.drive or windows.is_absolute() or posix.is_absolute() or (
        ".." in windows.parts or ".." in posix.parts
    ):
        raise TaskError("native standing materialization requires a source-relative path")
    root = root.resolve(strict=True)
    path = (root / reference).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise TaskError("native standing materialization is outside its trusted source")
    return path


def _materialization_command() -> tuple[str, ...]:
    from plugin_activation import resolve_active_plugins
    from .registrar import RegistrarError
    from .registrar_recipes import _resolve_plugin_ref_path

    try:
        script = _resolve_plugin_ref_path(
            "plugin:agent-roles:skills/managing-agent-topology/scripts/topology.py",
            activation=resolve_active_plugins(plugin_names=("agent-roles",)),
        )
    except RegistrarError as exc:
        raise TaskError("native standing materialization owner is unavailable") from exc
    if not script.is_file():
        raise TaskError("native standing materialization owner is unavailable")
    return sys.executable, str(script)


class LocalStandingRelationshipWriter:
    """Assemble native owners on demand; construction never discovers or launches."""

    def __init__(self, queue, *, sources: RegistrarSources | None = None) -> None:
        self.queue = queue
        self.sources = sources if sources is not None else RegistrarSources()
        self._source_lock = threading.RLock()

    def standing_attachment_supported(self) -> bool:
        # Advertise the assembly, not readiness of a particular workspace or sibling.
        return True

    def _source(self, task) -> Path:
        with self._source_lock:
            observed = read_supervisor_declarations(self.sources)
        if not observed.complete:
            raise TaskError("native standing declaration source is unconfirmed")
        matches = [
            entry for entry in standing_source_entries(observed)
            if entry.enabled and canonicalize_remote(entry.repo) == task.repo
            and dict(entry.declaration) == task.standing_charter["declaration"]
        ]
        if len(matches) != 1:
            raise TaskError("native standing task has no exact enabled trusted declaration")
        entry = matches[0]
        observations = [
            item for item in observed.source_observations if item.namespace == entry.namespace
        ]
        roots = {item.root for item in observations}
        if (
            not observations or len(roots) != 1 or None in roots
            or any(item.status != "complete" or item.revision_state not in {"ordered", "unordered"}
                   for item in observations)
            or not entry.source_path
        ):
            raise TaskError("native standing declaration has no complete trusted source owner")
        root = next(iter(roots)).resolve(strict=True)
        source = Path(entry.source_path).resolve(strict=True)
        if not source.is_relative_to(root):
            raise TaskError("native standing declaration is outside its source owner")
        return root

    def _original(self, request: dict, *, source_required: bool):
        workspace = AssignmentWorkspace.from_dict(request.get("workspace"))
        task = self.queue.get(request.get("task_id"))
        retained = self.queue.task_relationship(
            request.get("task_id"), event_id=request.get("event_id"),
        )
        if (
            task is None or task.kind != "standing-charter"
            or request.get("authority") != "local" or request.get("reservation_key") is not None
            or retained is None or retained.get("request") != request
        ):
            raise TaskError("native local standing admission requires its original queue request")
        snapshot = self.queue._admit_original_relationship_request(request)
        if source_required and snapshot["release_outcome"] not in {"completed", "unassigned_queued"}:
            self._source(task)
        return workspace, task

    def _writer(
        self, request: dict, *, standing: bool = False, source_required: bool = True,
    ) -> WorktreeRelationshipWriter:
        original = copy.deepcopy(request)
        workspace, task = self._original(original, source_required=source_required)
        machine = resolve_machine()
        if not machine or machine.lower() != workspace.machine.lower():
            raise TaskError("native standing admission requires its actual host-local owner")
        bridge_prefix = agent_bridge_launch_prefix()
        if not bridge_prefix:
            raise TaskError("native standing Bridge writer owner is unavailable")
        policy = None
        if standing:
            from .config import overrides_path

            root = self._source(task)
            policy = StandingAdmission(
                machine, overrides_path().resolve(), _materialization_command(),
                lambda reference: _materialization_path(root, reference),
            )
        selected = None

        def bridge_run(arguments: list[str], *, read_only: bool):
            if not read_only or selected is None:
                raise TaskError("native standing admission only permits owner reads")
            value = selected._run(arguments, prefix=bridge_prefix, owner="Bridge")
            return subprocess.CompletedProcess(arguments, 0, json.dumps(value), "")

        def admit_workspace(observed: AssignmentWorkspace, identity: dict) -> None:
            if observed != workspace:
                raise TaskError("native standing workspace changed during admission")
            admission = OrdinaryWriterAdmission({
                "version": 1, "identity": identity, "creation_nonce": workspace.creation_nonce,
            }, {
                "worktree": workspace.worktree, "allocation_project": workspace.project,
                "worktree_path": workspace.path, "creation_nonce": workspace.creation_nonce,
            })
            record = selected._run([
                "--project", workspace.project, "record", "--worktree-id", workspace.worktree, "--json",
            ])
            row = record.get("worktree")
            if record.get("ok") is not True:
                raise TaskError("native standing workspace owner read is unconfirmed")
            admission.check_record(row)
            native_session = row.get("last_session_id")
            if native_session is None:
                native_session = task.owner_session_id
            retained = self.queue.task_relationship(original["task_id"], event_id=original["event_id"])
            participant = retained.get("native_participant", {}) if retained is not None else {}
            release = task.standing_charter.get("release", {})
            if native_session is None and (
                release.get("assignment_event_id") == original["event_id"]
                and release.get("request_id") == original["request_id"]
            ):
                native_session = release.get("owner_session_id")
            if native_session is None:
                native_session = participant.get("native_session_id")
            response = admission.inspect_workspace(bridge_run, native_session_id=native_session)
            observed = response["worktree_writer_observation"]
            digest = observed.get("key_digest")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise TaskError("native standing Bridge journal identity is unconfirmed")
            self.queue.retain_standing_native_participant(original, {
                "version": 1, "journal_key": digest,
                "bridge_installation": observed["bridge_instance"]["installation_root"],
                "bridge_session_id": response["session_id"],
                "native_session_id": response["acp_session_id"],
                "writer_requirement": observed["worktree_writer"],
            })

        def admit_request(observed: AssignmentWorkspace, _identity: dict, audited: dict) -> None:
            if observed != workspace or audited != original:
                raise TaskError("native standing original request changed during admission")
            self._original(audited, source_required=source_required)

        selected = WorktreeRelationshipWriter.selected(
            admit_workspace=admit_workspace, admit_request=admit_request, standing=policy,
        )
        return selected

    def bind_request(self, request: dict):
        return self._writer(request).bind_request(request)

    @contextmanager
    def admit_standing(self, request: dict, declaration: dict, expected_head: dict):
        writer = self._writer(request, standing=True)
        with writer.admit_standing(request, declaration, expected_head) as receipt:
            yield receipt

    def inspect_conclusion(self, request: dict, expected_conclusion: dict) -> dict:
        return self._writer(request, source_required=False).inspect_conclusion(request, expected_conclusion)

    def __call__(self, workspace: AssignmentWorkspace, operation: str, payload: dict) -> dict:
        raise TaskError("native standing writer requires its bound original request")
