"""Execution-leg CLI calls for the agent-worktrees engine client.

Extracted from ``engine_client.py`` purely to control that module's size (the
module-size gate's hard cap) -- no behavior change. These are the execution-leg
inspection/reservation verbs (``get``/``set``/``clear``/``reserve``/``renew``/
``release``), used by the AHP provider and the Picker to track which execution
backend (if any) currently owns a worktree's interactive session. Re-exported
from ``engine_client`` so every existing caller (``engine_client.execution_leg_
*``) is unaffected.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .engine_client import (
    EngineError,
    EngineFeatureUnavailable,
    _engine_error_detail,
    _DEFAULT_TIMEOUT,
    run_json,
)


def execution_leg_get(
    project: str,
    worktree_id: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    try:
        return run_json(
            project,
            ["execution-leg", "get", "--worktree-id", worktree_id, "--json"],
            timeout=timeout,
        )
    except EngineError as error:
        detail = _engine_error_detail(error).casefold()
        unsupported = (
            ("invalid choice" in detail and "execution-leg" in detail)
            or ("unrecognized arguments" in detail and "execution-leg" in detail)
            or ("unknown command" in detail and "execution-leg" in detail)
        )
        if unsupported:
            raise EngineFeatureUnavailable(
                "installed engine does not support execution-leg inspection"
            ) from error
        raise


def execution_leg_set(
    project: str,
    worktree_id: str,
    *,
    provider: str,
    state: str,
    binding_revision: int,
    blob: dict[str, object],
    if_match_revision: int,
    reservation_token: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(blob, handle, separators=(",", ":"))
            path = handle.name
        args = [
            "execution-leg",
            "set",
            "--worktree-id",
            worktree_id,
            "--provider",
            provider,
            "--state",
            state,
            "--binding-revision",
            str(binding_revision),
            "--blob-file",
            path,
            "--if-match-revision",
            str(if_match_revision),
        ]
        if reservation_token:
            args += ["--reservation-token", reservation_token]
        return run_json(
            project,
            args + ["--json"],
            timeout=timeout,
        )
    finally:
        if path:
            try:
                Path(path).unlink()
            except OSError:
                pass


def execution_leg_clear(
    project: str,
    worktree_id: str,
    *,
    if_match_revision: int,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    return run_json(
        project,
        [
            "execution-leg",
            "clear",
            "--worktree-id",
            worktree_id,
            "--if-match-revision",
            str(if_match_revision),
            "--json",
        ],
        timeout=timeout,
    )


def execution_leg_reserve(
    project: str,
    worktree_id: str,
    *,
    provider: str,
    operation: str,
    owner: str,
    owner_pid: int | None = None,
    owner_start_time: str | None = None,
    lease_seconds: int = 300,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    args = [
        "execution-leg",
        "reserve",
        "--worktree-id",
        worktree_id,
        "--provider",
        provider,
        "--operation",
        operation,
        "--reservation-owner",
        owner,
        "--lease-seconds",
        str(lease_seconds),
    ]
    if owner_pid is not None:
        args += ["--reservation-owner-pid", str(owner_pid)]
    if owner_start_time:
        args += ["--reservation-owner-start-time", owner_start_time]
    return run_json(project, args + ["--json"], timeout=timeout)


def execution_leg_renew(
    project: str,
    worktree_id: str,
    *,
    reservation_token: str,
    lease_seconds: int = 300,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    return run_json(
        project,
        [
            "execution-leg",
            "renew",
            "--worktree-id",
            worktree_id,
            "--reservation-token",
            reservation_token,
            "--lease-seconds",
            str(lease_seconds),
            "--json",
        ],
        timeout=timeout,
    )


def execution_leg_release(
    project: str,
    worktree_id: str,
    *,
    reservation_token: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    return run_json(
        project,
        [
            "execution-leg",
            "release",
            "--worktree-id",
            worktree_id,
            "--reservation-token",
            reservation_token,
            "--json",
        ],
        timeout=timeout,
    )
