"""Use the selected Worktrees writer without adopting legacy workspace names."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence

from agent_procutil.owned_process import OwnedCleanupError

from .assignment_workspace import AssignmentWorkspace, RelationshipWriter
from .procutil import agent_worktrees_environment, run_background_capture
from .queue_records import TaskError
from .queue_task_relationships import _matches_receipt, valid_record_writer

_pending_cleanup: list[OwnedCleanupError] = []
_cleanup_lock = threading.Lock()


class WorktreeRelationshipWriter:
    """Finite owner calls behind an independently qualified writer admission.

    Construction does not activate ordinary allocation. The installing owner
    must supply its real preservation/cohort admission, not a copied receipt.
    Request-bound admission additionally receives the original audited request;
    its callback cannot be bypassed through the legacy unbound call.
    """

    @classmethod
    def selected(
        cls,
        *,
        admit_workspace: Callable[[AssignmentWorkspace, dict], None],
        admit_request: Callable[[AssignmentWorkspace, dict, dict], None],
        timeout: float = 30,
    ) -> WorktreeRelationshipWriter:
        """Observe actual CLI/resident participants, not the remaining cohort."""
        from .worktree_owner_launch import retained_selection

        try:
            prefix = retained_selection()
        except (OSError, ValueError, ImportError) as exc:
            raise TaskError("selected Worktrees build cannot be retained") from exc
        if not callable(admit_request):
            raise ValueError("selected relationship writer requires original request admission")
        return cls(
            prefix, admit_workspace=admit_workspace, admit_request=admit_request,
            timeout=timeout, require_resident=True,
        )

    def __init__(
        self,
        prefix: Sequence[str],
        *,
        admit_workspace: Callable[[AssignmentWorkspace, dict], None],
        admit_request: Callable[[AssignmentWorkspace, dict, dict], None] | None = None,
        timeout: float = 30,
        require_resident: bool = False,
    ) -> None:
        if not prefix or not callable(admit_workspace):
            raise ValueError(
                "relationship writer requires a selected command and preservation admission"
            )
        self.prefix = tuple(prefix)
        self.admit_workspace = admit_workspace
        if admit_request is not None and not callable(admit_request):
            raise ValueError("relationship request admission must be callable")
        self._admit_request = admit_request
        self.timeout = timeout
        self._require_resident = require_resident
        support = self._run(["resource-relationship", "capabilities", "--json"])
        identity = support.get("writer_identity")
        if not (
            support.get("schema") == "copilot-extensions.resource-relationship"
            and type(support.get("version")) is int
            and support["version"] == 1
            and support.get("expected_creation_nonce") is True
            and support.get("expected_writer_identity") is True
            and support.get("writer_scope") == "executing-process"
            and valid_record_writer(support.get("writer_contract"))
            and support.get("inspect_absence") is True
            and support.get("metadata_version") == 2
            and set(support.get("operations", [])) >= {"reserve", "bind", "inspect", "close"}
            and isinstance(identity, dict)
            and set(identity) == {"installation_root", "runtime_generation"}
            and isinstance(identity["installation_root"], str)
            and bool(identity["installation_root"].strip())
            and isinstance(identity["runtime_generation"], str)
            and re.fullmatch(r"[0-9a-f]{32}", identity["runtime_generation"]) is not None
            and (not require_resident or (
                type(support.get("resident_inspect")) is int and support["resident_inspect"] == 1
            ))
        ):
            raise TaskError(
                "selected Worktrees writer cannot protect incarnation-bound task relationships"
            )
        self._writer_identity = dict(identity)

    def _inspect_resident(self, workspace: AssignmentWorkspace, request_id: str) -> None:
        result = self._run(
            ["--project", workspace.project, "resource-relationship", "inspect",
             "--resident-only", "--owner-ref", workspace.owner_ref, "--json"],
            {"request_id": request_id, "allow_missing": True,
             "expected_creation_nonce": workspace.creation_nonce},
        )
        identity = result.get("writer_identity")
        resident = result.get("resident")
        if not (
            result.get("schema") == "copilot-extensions.resource-relationship"
            and type(result.get("version")) is int and result["version"] == 1
            and result.get("ok") is True
            and result.get("owner") == workspace.owner_ref
            and result.get("request_id") == request_id
            and result.get("creation_nonce") == workspace.creation_nonce
            and valid_record_writer(result.get("writer_contract"))
            and isinstance(identity, dict)
            and set(identity) == {"installation_root", "runtime_generation"}
            and identity["installation_root"] == self._writer_identity["installation_root"]
            and isinstance(identity["runtime_generation"], str)
            and re.fullmatch(r"[0-9a-f]{32}", identity["runtime_generation"]) is not None
            and isinstance(resident, dict)
            and set(resident) == {"generation", "runtime_generation"}
            and all(isinstance(value, str) and value.strip() for value in resident.values())
        ):
            raise TaskError("actual Worktrees resident is unconfirmed; original assignment retained")

    def bind_request(self, request: dict) -> RelationshipWriter:
        """Bind one audited request without sharing mutable callback state."""
        admission = self._admit_request
        if admission is None:
            raise TaskError("selected relationship writer has no original request admission")
        if not isinstance(request, dict):
            raise TaskError("original relationship request must be an object")
        original = copy.deepcopy(request)
        workspace = AssignmentWorkspace.from_dict(original.get("workspace"))
        authority = original.get("authority")
        event_id = original.get("event_id")
        identity = {"assignment_event_id": event_id, "worker_id": workspace.worker_id}
        recovery = original.get("recovery")
        if not (
            authority in {"local", "central"}
            and original.get("namespace") == f"dispatch-{authority}-task"
            and type(event_id) is int and 1 <= event_id <= 2**63 - 1
            and type(original.get("generation")) is int and original["generation"] >= 0
            and original.get("identity") == identity
            and isinstance(original.get("request_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", original["request_id"])
            and isinstance(original.get("task_id"), str)
            and re.fullmatch(r"[0-9a-f]{32}", original["task_id"])
            and "reservation_key" in original
            and (
                original["reservation_key"] is None
                or isinstance(original["reservation_key"], str) and original["reservation_key"].strip()
            )
            and isinstance(recovery, dict)
            and isinstance(recovery.get("queue_identity"), str)
            and re.fullmatch(r"[0-9a-f]{32}", recovery["queue_identity"])
            and type(recovery.get("version")) is int
            and recovery == {
                "schema": "dispatch-task-assignment", "version": 2, "authority": authority,
                "task_id": original["task_id"], "queue_identity": recovery["queue_identity"],
                **identity,
            }
        ):
            raise TaskError("original relationship request is inconsistent; assignment retained")
        token = None

        def write(selected: AssignmentWorkspace, operation: str, payload: dict) -> dict:
            nonlocal token
            facts = {
                "reserve": {
                    "kind": "task", "namespace": original["namespace"],
                    "request_id": original["request_id"], "recovery": original["recovery"],
                },
                "bind": {"token": token, "identity": original["identity"]},
                "inspect": {"request_id": original["request_id"], "allow_missing": True},
                "close": {"token": token},
            }.get(operation)
            guards = {
                "expected_creation_nonce": workspace.creation_nonce,
                "expected_writer_identity": self._writer_identity,
            }
            if (
                selected != workspace or facts is None or not isinstance(payload, dict)
                or (operation in {"bind", "close"} and token is None)
                or (facts is not None and (
                    not facts.keys() <= payload.keys()
                    or not payload.keys() <= facts.keys() | guards.keys()
                    or any(payload[key] != value for key, value in facts.items())
                    or any(key in payload and payload[key] != value for key, value in guards.items())
                ))
            ):
                raise TaskError("relationship operation conflicts with its original request")
            admission(workspace, dict(self._writer_identity), copy.deepcopy(original))
            result = self._write(workspace, operation, copy.deepcopy(payload))
            phase = (
                "absent" if operation == "inspect" and result.get("absent") is True
                else {"bind": "bound", "close": "closed"}.get(operation)
            )
            if not _matches_receipt(result, original, phase=phase):
                raise TaskError("ground owner did not confirm the original admitted request")
            token = result.get("token")
            return result

        return write

    def _run(self, arguments: list[str], payload: dict | None = None) -> dict:
        deadline = time.monotonic() + self.timeout
        if not _cleanup_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise TaskError("selected Worktrees cleanup is still owned by another request")
        try:
            for failure in tuple(_pending_cleanup):
                try:
                    failure.retry_cleanup(deadline=deadline)
                except Exception as exc:
                    raise TaskError(
                        "selected Worktrees cleanup is unresolved; native custody retained"
                    ) from exc
                _pending_cleanup.remove(failure)
            try:
                input_text = json.dumps(payload) if payload is not None else ""
                env = agent_worktrees_environment()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TaskError("selected Worktrees request budget expired during cleanup")
                result = run_background_capture(
                    [*self.prefix, *arguments],
                    input_text=input_text,
                    timeout=remaining,
                    env=env,
                )
                if result is None:
                    raise TaskError(
                        "selected Worktrees request is unresolved; retain the original assignment"
                    )
                value = json.loads(result.stdout or "{}")
                if result.returncode or not isinstance(value, dict) or value.get("error"):
                    raise TaskError("selected Worktrees relationship result is unconfirmed")
                return value
            except OwnedCleanupError as exc:
                # This module outlives failed construction, queue/HTTP translation
                # and the writer instance. Keep the actual native objects first.
                if not any(pending is exc for pending in _pending_cleanup):
                    _pending_cleanup.append(exc)
                raise TaskError(
                    "selected Worktrees cleanup is unresolved; native custody retained"
                ) from exc
            except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
                raise TaskError(
                    "selected Worktrees relationship request is unresolved; retain and reconcile"
                ) from exc
        finally:
            _cleanup_lock.release()

    def __call__(
        self,
        workspace: AssignmentWorkspace,
        operation: str,
        payload: dict,
    ) -> dict:
        if self._admit_request is not None:
            raise TaskError("relationship writer requires its bound original request")
        return self._write(workspace, operation, payload)

    def _write(self, workspace: AssignmentWorkspace, operation: str, payload: dict) -> dict:
        self.admit_workspace(workspace, dict(self._writer_identity))
        if operation not in {"reserve", "bind", "inspect", "close"}:
            raise TaskError("unsupported task relationship operation")
        expected = {
            "expected_creation_nonce": workspace.creation_nonce,
            "expected_writer_identity": self._writer_identity,
        }
        if not isinstance(payload, dict) or any(
            key in payload and payload[key] != value for key, value in expected.items()
        ):
            raise TaskError("task relationship input conflicts with its admitted writer or workspace")
        if self._require_resident:
            self._inspect_resident(workspace, payload.get("request_id", "dispatch-writer-inspection"))
        # Exact identity was captured by the assigning owner. This read only
        # checks it; it cannot adopt a path or reconstruct a missing nonce.
        record = self._run(
            [
                "--project",
                workspace.project,
                "record",
                "--worktree-id",
                workspace.worktree,
                "--json",
            ]
        )
        row = record.get("worktree")
        if not (
            record.get("ok") is True
            and isinstance(row, dict)
            and row.get("id") == workspace.worktree
            and row.get("repo") == workspace.project
            and row.get("path") == workspace.path
            and row.get("creation_nonce") == workspace.creation_nonce
        ):
            raise TaskError("original assignment workspace is unconfirmed; relationship retained")
        result = self._run(
            [
                "--project",
                workspace.project,
                "resource-relationship",
                operation,
                "--owner-ref",
                workspace.owner_ref,
                "--json",
            ],
            {**payload, **expected},
        )
        if not (
            result.get("ok") is True
            and result.get("owner") == workspace.owner_ref
            and result.get("creation_nonce") == workspace.creation_nonce
            and result.get("writer_identity") == self._writer_identity
            and valid_record_writer(result.get("writer_contract"))
        ):
            raise TaskError("executing Worktrees writer is unconfirmed; original relationship retained")
        return result
