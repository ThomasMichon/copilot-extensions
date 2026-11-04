"""Use the selected Worktrees writer without adopting legacy workspace names."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable, Sequence

from agent_procutil.owned_process import OwnedCleanupError

from .assignment_workspace import AssignmentWorkspace
from .procutil import agent_worktrees_environment, run_background_capture
from .queue_records import TaskError
from .queue_task_relationships import valid_record_writer

_pending_cleanup: list[OwnedCleanupError] = []
_cleanup_lock = threading.Lock()


class WorktreeRelationshipWriter:
    """Finite owner calls behind an independently qualified writer admission.

    Construction does not activate ordinary allocation. The installing owner
    must supply its real preservation/cohort admission, not a copied receipt.
    """

    def __init__(
        self,
        prefix: Sequence[str],
        *,
        admit_workspace: Callable[[AssignmentWorkspace], None],
        timeout: float = 30,
    ) -> None:
        if not prefix or not callable(admit_workspace):
            raise ValueError(
                "relationship writer requires a selected command and preservation admission"
            )
        self.prefix = tuple(prefix)
        self.admit_workspace = admit_workspace
        self.timeout = timeout
        support = self._run(["resource-relationship", "capabilities", "--json"])
        if not (
            support.get("schema") == "copilot-extensions.resource-relationship"
            and type(support.get("version")) is int
            and support["version"] == 1
            and support.get("expected_creation_nonce") is True
            and support.get("writer_scope") == "executing-process"
            and valid_record_writer(support.get("writer_contract"))
            and support.get("inspect_absence") is True
            and support.get("metadata_version") == 2
            and set(support.get("operations", [])) >= {"reserve", "bind", "inspect", "close"}
        ):
            raise TaskError(
                "selected Worktrees writer cannot protect incarnation-bound task relationships"
            )

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
        self.admit_workspace(workspace)
        if operation not in {"reserve", "bind", "inspect", "close"}:
            raise TaskError("unsupported task relationship operation")
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
        return self._run(
            [
                "--project",
                workspace.project,
                "resource-relationship",
                operation,
                "--owner-ref",
                workspace.owner_ref,
                "--json",
            ],
            payload,
        )
