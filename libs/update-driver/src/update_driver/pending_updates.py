"""Latest-wins selection inside the existing queue admission transaction."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import RequestSpec, TrustedRunner


def _execution_home(value: str, cwd: str) -> str:
    from .core import InvalidRequestError

    selected = os.path.normpath(os.path.join(cwd, value))
    if not os.path.isabs(selected):
        raise InvalidRequestError("installation home requires an absolute execution directory")
    return selected


def capture_home(spec: RequestSpec, runner: TrustedRunner) -> RequestSpec:
    if spec.backend not in {"manager", "engine", "engine-launch-update"}:
        return spec
    context = dict(spec.context)
    home = context.get("AGENT_HOME", "").strip()
    if not home:
        return spec
    context["AGENT_HOME"] = _execution_home(home, runner.cwd)
    return replace(spec, context=tuple(context.items()))


def _update_scope(spec: RequestSpec, runner: TrustedRunner) -> tuple[str, ...] | None:
    from .core import InvalidRequestError

    if spec.backend not in {"manager", "engine", "engine-launch-update"}:
        return None
    if spec.backend == "engine-launch-update":
        if "--no-update" in spec.argv or spec.argv[:1] in {
            ("bootstrap",), ("reconcile",), ("staged",),
        }:
            return None
    context = dict(spec.context)

    def path(value: str) -> str:
        selected = Path(value).expanduser()
        if not selected.is_absolute():
            if not spec.cwd or not Path(spec.cwd).is_absolute():
                raise InvalidRequestError("update scope requires an absolute request cwd")
            selected = Path(spec.cwd) / selected
        return os.path.normcase(os.path.normpath(str(selected)))

    cell = context.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if cell:
        receipt = Path(path(cell))
        # Receipts from different plugins in one installation share its slot.
        if receipt.name == "install.json" and receipt.parent.parent.name == "plugins":
            cell = str(receipt.parent.parent.parent)
        else:
            cell = str(receipt)
    home = os.path.normcase(_execution_home(
        context.get("AGENT_HOME", "").strip() or str(Path.home()), runner.cwd,
    ))
    manager = path(context.get("WORKTREE_MANAGER_ROOT") or str(Path.home() / ".worktree-manager"))
    project = spec.project
    if spec.backend == "manager" and project and Path(project).is_absolute() and path(project) == manager:
        project = ""
        for index, argument in enumerate(spec.argv):
            if argument == "--project" and index + 1 < len(spec.argv):
                project = spec.argv[index + 1]
            elif argument.startswith("--project="):
                project = argument.partition("=")[2]
    return home, cell, manager, spec.authority, project


def supersede_waiting(
    db: sqlite3.Connection, root: Path, spec: RequestSpec, runner: TrustedRunner, successor: str,
) -> None:
    from .core import StateError, _request

    scope = _update_scope(spec, runner)
    if scope is None:
        return
    waiting = [
        _request(row, root) for row in db.execute(
            """SELECT * FROM requests WHERE state='pending' AND started_at IS NULL
            AND finished_at IS NULL AND boot_id IS NULL AND id<>?""", (successor,),
        )
    ]
    peers = [
        request for request in waiting
        if _update_scope(request.spec, request.runner) == scope
    ]
    if "--auto" in spec.argv and any("--auto" not in request.spec.argv for request in peers):
        raise StateError("explicit update already waiting; automatic request not admitted")
    db.executemany(
        """UPDATE requests SET state='completed',exit_code=125,error=?,finished_at=?
        WHERE id=?""",
        [
            (f"superseded by update request {successor}; nothing ran", time.time(), request.id)
            for request in peers
        ],
    )
