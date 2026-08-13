"""Process-boundary client for the agent-worktrees engine (Phase 6b).

The Worktree Manager is a **separate process that shells out to the
``agent-worktrees`` CLI** -- it never ``import``s the plugin (the dependency-free
boundary, asserted by ``test_contract_dependency_free``). Every worktree
operation the Manager renders is fetched by running ``agent-worktrees --project
<p> <verb> --json`` and parsing the machine-readable envelope, per the pinned
*engine <-> Picker ``--json`` contract*
(``plugins/agent-worktrees/docs/engine-picker-contract.md``).

This module is that seam. It resolves the engine binstub, runs a ``--json`` verb
with robust error handling, and tolerates **version skew**: when a newer Manager
passes a flag an older engine rejects (e.g. ``--classify``), it degrades the
request rather than failing (the *version-skew-tolerant contract* property). It
imports nothing from the plugin; the only coupling is the CLI's stable verbs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

#: The engine binstub name (the self-provisioning agent-worktrees tool CLI).
ENGINE_BIN = "agent-worktrees"

#: A generous ceiling: a cold engine self-provisions on first use, and a classify
#: pass can enumerate many worktrees. Kept bounded so the Manager never hangs.
_DEFAULT_TIMEOUT = 120


class EngineError(RuntimeError):
    """The agent-worktrees engine is absent, failed, or returned no valid JSON.

    ``install_hint`` is True when the engine binstub could not be found at all --
    the caller should point the user at ``worktree-manager setup`` (which drives
    the core install) rather than treat it as a hard error.
    """

    def __init__(self, message: str, *, install_hint: bool = False) -> None:
        super().__init__(message)
        self.install_hint = install_hint


def engine_path() -> str | None:
    """Resolve the ``agent-worktrees`` binstub on PATH, or None if not installed."""
    return shutil.which(ENGINE_BIN)


def engine_available() -> bool:
    return engine_path() is not None


def _run(project: str | None, args: list[str], *, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Run ``agent-worktrees [--project <p>] <args>`` and return stdout.

    Raises :class:`EngineError` when the binstub is missing (``install_hint``),
    the process fails, or times out. A non-zero exit whose stdout is a JSON error
    envelope surfaces the engine's own ``error`` message.
    """
    exe = engine_path()
    if exe is None:
        raise EngineError(
            f"the {ENGINE_BIN} engine is not installed", install_hint=True)
    cmd = [exe]
    if project:
        cmd += ["--project", project]
    cmd += args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"{ENGINE_BIN} {' '.join(args)} timed out") from e
    except OSError as e:
        raise EngineError(f"could not run {ENGINE_BIN}: {e}") from e
    if proc.returncode != 0:
        detail = _error_from_envelope(proc.stdout) or (proc.stderr or "").strip()
        raise EngineError(
            f"{ENGINE_BIN} {' '.join(args)} failed "
            f"(exit {proc.returncode}): {detail or 'no output'}")
    return proc.stdout


def _error_from_envelope(stdout: str) -> str | None:
    """Pull the ``error`` field out of a JSON error envelope, if stdout is one."""
    try:
        obj = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("error"):
        return str(obj["error"])
    return None


def run_json(project: str | None, args: list[str], *,
             timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """Run a ``--json`` verb and parse its stdout envelope into a dict."""
    raw = _run(project, args, timeout=timeout)
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise EngineError(
            f"{ENGINE_BIN} {' '.join(args)} did not return valid JSON") from e
    if not isinstance(obj, dict):
        raise EngineError(f"{ENGINE_BIN} {' '.join(args)} returned non-object JSON")
    return obj


@dataclass(frozen=True)
class Worktree:
    """A worktree row derived from ``list --json --classify`` (contract v1).

    Only the fields the Manager renders are lifted into typed attributes; the
    raw dict is kept on ``raw`` so a newer contract field is reachable without a
    code change here (additive-only evolution).
    """

    id: str
    repo: str
    machine: str
    branch: str
    title: str | None
    state: str | None          # git-derived (present with --classify)
    ahead: int
    behind: int
    dirty: bool
    status: str | None         # tracking status (active/complete/...)
    path: str | None
    raw: dict

    @property
    def id4(self) -> str:
        """The short 4-char worktree id suffix the Picker shows (``repo:id4``)."""
        return self.id[-4:] if self.id else "----"

    @property
    def sync_tag(self) -> str:
        bits = []
        if self.ahead:
            bits.append(f"\u2191{self.ahead}")
        if self.behind:
            bits.append(f"\u2193{self.behind}")
        return "".join(bits)


def _to_worktree(d: dict) -> Worktree:
    return Worktree(
        id=str(d.get("id", "")),
        repo=str(d.get("repo", "") or ""),
        machine=str(d.get("machine", "") or ""),
        branch=str(d.get("branch", "") or ""),
        title=(d.get("title") if d.get("title") not in (None, "null") else None),
        state=d.get("state"),
        ahead=int(d.get("ahead") or 0),
        behind=int(d.get("behind") or 0),
        dirty=bool(d.get("dirty") or False),
        status=d.get("status"),
        path=d.get("path"),
        raw=d,
    )


def list_worktrees(project: str, *, classify: bool = True) -> list[Worktree]:
    """List a project's worktrees via ``agent-worktrees list --json``.

    Requests ``--classify`` (git state + sync tags) by default; if an **older**
    engine rejects the flag, transparently retries the plain listing so the
    Manager degrades a feature (no state block) instead of failing -- the
    version-skew tolerance the contract calls for.
    """
    args = ["list", "--json"]
    if classify:
        args.append("--classify")
    try:
        obj = run_json(project, args)
    except EngineError as e:
        if classify and "--classify" in str(e):
            return list_worktrees(project, classify=False)
        raise
    rows = obj.get("worktrees")
    if not isinstance(rows, list):
        return []
    return [_to_worktree(d) for d in rows if isinstance(d, dict)]
