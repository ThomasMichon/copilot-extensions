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
import shlex
import shutil
import subprocess
from dataclasses import dataclass

#: The engine binstub name (the self-provisioning agent-worktrees tool CLI).
ENGINE_BIN = "agent-worktrees"

#: Override the base engine command (everything before ``[--project …] <verb>``).
#: Set to a shell-quoted command to point the client at a *fake* engine binstub
#: instead of the real one -- the seam that makes the Manager (and its Picker)
#: buildable, testable, and demo-able without a live agent-worktrees, faithfully
#: through the same subprocess + JSON-parse path. The ``--demo`` Picker mode sets
#: this to the bundled Aperture Labs fake engine.
ENGINE_CMD_ENV = "WORKTREE_MANAGER_ENGINE_CMD"

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


def _engine_override() -> list[str] | None:
    """The overriding base engine command from the environment, if set."""
    raw = os.environ.get(ENGINE_CMD_ENV)
    if not raw or not raw.strip():
        return None
    return shlex.split(raw, posix=os.name != "nt")


#: In-process base-command override (wins over the env). Set by the Picker's
#: ``--demo`` mode to the bundled fake engine, avoiding any shell-quoting round
#: trip through the environment.
_ENGINE_CMD_OVERRIDE: list[str] | None = None


def set_engine_command(cmd: list[str] | None) -> None:
    """Force the base engine command in-process (e.g. a fake/demo engine)."""
    global _ENGINE_CMD_OVERRIDE
    _ENGINE_CMD_OVERRIDE = list(cmd) if cmd else None


def engine_base_command() -> list[str] | None:
    """The base command to run the engine (before ``[--project …] <verb>``).

    Resolution order: the in-process override (demo/tests) → the
    ``WORKTREE_MANAGER_ENGINE_CMD`` env override → the resolved ``agent-worktrees``
    binstub. None when none is available.
    """
    if _ENGINE_CMD_OVERRIDE:
        return list(_ENGINE_CMD_OVERRIDE)
    override = _engine_override()
    if override:
        return override
    exe = engine_path()
    return [exe] if exe else None


def engine_path() -> str | None:
    """Resolve the ``agent-worktrees`` binstub on PATH, or None if not installed."""
    return shutil.which(ENGINE_BIN)


def engine_available() -> bool:
    return engine_base_command() is not None


def _run(project: str | None, args: list[str], *, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Run ``agent-worktrees [--project <p>] <args>`` and return stdout.

    Raises :class:`EngineError` when the binstub is missing (``install_hint``),
    the process fails, or times out. A non-zero exit whose stdout is a JSON error
    envelope surfaces the engine's own ``error`` message.
    """
    base = engine_base_command()
    if base is None:
        raise EngineError(
            f"the {ENGINE_BIN} engine is not installed", install_hint=True)
    cmd = [*base]
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


def run_engine_passthrough(project: str | None, args: list[str], *,
                           timeout: int | None = None) -> int:
    """Run an engine verb with **inherited stdio**, returning its exit code.

    Unlike :func:`run_json` (which captures + parses), this streams the engine's
    live output straight to the user's terminal -- for interactive, non-``--json``
    verbs the Manager *orchestrates* rather than reads, notably
    ``agent-worktrees update --no-manager`` (the seam bypass the Manager re-enters
    through). Raises :class:`EngineError` with ``install_hint`` when the engine
    binstub is absent.
    """
    base = engine_base_command()
    if base is None:
        raise EngineError(
            f"the {ENGINE_BIN} engine is not installed", install_hint=True)
    cmd = [*base]
    if project:
        cmd += ["--project", project]
    cmd += args
    try:
        return subprocess.run(
            cmd, timeout=timeout, check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        ).returncode
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"{ENGINE_BIN} {' '.join(args)} timed out") from e
    except OSError as e:
        raise EngineError(f"could not run {ENGINE_BIN}: {e}") from e


def get_value(project: str, key: str, *, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Read a pinned scalar value from ``agent-worktrees get <key>``."""
    return _run(project, ["get", key], timeout=timeout).strip()


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


def _int_field(value: object, *, name: str) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool_field(value: object, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0):
        return False
    if value == 1:
        return True
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in ("true", "yes", "1"):
            return True
        if normalized in ("false", "no", "0", ""):
            return False
    raise ValueError(f"{name} must be a boolean")


def worktree_from_dict(d: dict) -> Worktree:
    """Build the Manager's cross-cutting worktree model from a contract row."""
    return Worktree(
        id=str(d.get("id") or ""),
        repo=str(d.get("repo", "") or ""),
        machine=str(d.get("machine", "") or ""),
        branch=str(d.get("branch", "") or ""),
        title=(d.get("title") if d.get("title") not in (None, "null") else None),
        state=d.get("state"),
        ahead=_int_field(d.get("ahead"), name="ahead"),
        behind=_int_field(d.get("behind"), name="behind"),
        dirty=_bool_field(d.get("dirty"), name="dirty"),
        status=d.get("status"),
        path=d.get("path"),
        raw=d,
    )


@dataclass(frozen=True)
class LaunchPlan:
    """A launch plan emitted by ``agent-worktrees resolve --json`` (contract v1).

    ``resolve`` is the engine's *control-plane* verb: given a worktree id (resume),
    ``--new`` (create-and-launch), or ``--bare-resume``, it returns the JSON plan
    the front-end acts on -- **it does not launch anything itself** (the Python
    process exits before Copilot starts; the caller executes the plan). The Manager
    is that caller now, so :mod:`launcher` composes + runs this plan.

    ``--json`` forces ``no_mux`` on the *engine* side (the engine must never spawn a
    multiplexer in machine-readable mode); muxing is the Manager's own decision
    (DQ9 -- the Manager owns mux), so :func:`launcher.compose_launch` does not gate
    on ``no_mux``. Only the fields the launcher needs are typed; ``raw`` keeps the
    whole plan for forward-compat fields.
    """

    action: str                 # "exec" | "none" | (other engine actions pass through)
    cmd: list[str]
    work_dir: str | None
    status_path: str | None
    env: dict
    worktree_id: str | None
    post_exit: bool
    no_mux: bool                # the *engine's* mux suppression (always set by --json)
    exit_code: int
    raw: dict

    @property
    def is_exec(self) -> bool:
        return self.action == "exec"


def _to_launch_plan(d: dict) -> LaunchPlan:
    cmd = d.get("cmd")
    return LaunchPlan(
        action=str(d.get("action", "none")),
        cmd=[str(c) for c in cmd] if isinstance(cmd, list) else [],
        work_dir=d.get("work_dir"),
        status_path=d.get("status_path") or d.get("work_dir"),
        env=dict(d.get("env") or {}),
        worktree_id=d.get("worktree_id"),
        post_exit=bool(d.get("post_exit")),
        no_mux=bool(d.get("no_mux")),
        exit_code=int(d.get("exit_code") or 0),
        raw=d,
    )


def resolve_launch_plan(
    project: str,
    *,
    worktree_id: str | None = None,
    new: bool = False,
    bare_resume: bool = False,
    timeout: int = _DEFAULT_TIMEOUT,
) -> LaunchPlan:
    """Fetch a launch plan via ``agent-worktrees resolve --json`` (process boundary).

    Exactly one of ``worktree_id`` (resume the worktree) or ``new`` (create + launch
    a fresh worktree) must be given; ``bare_resume`` is the two-step-restore variant
    of a resume. Mirrors the engine's own ``--json requires --worktree-id or --new``
    rule so the Manager fails fast rather than shelling out to be rejected.

    Version-skew tolerant: an older engine that does not know ``--bare-resume`` is
    retried as a plain resume (degrade the feature, don't fail) -- the same contract
    property :func:`list_worktrees` applies to ``--classify``.
    """
    if worktree_id and new:
        raise EngineError("worktree_id and new are mutually exclusive")
    if not worktree_id and not new:
        raise EngineError("resolve requires a worktree_id or new=True")

    args = ["resolve", "--json"]
    if new:
        args.append("--new")
    else:
        args += ["--worktree-id", worktree_id or ""]
    if bare_resume:
        args.append("--bare-resume")

    try:
        obj = run_json(project, args, timeout=timeout)
    except EngineError as e:
        if bare_resume and "--bare-resume" in str(e):
            return resolve_launch_plan(
                project, worktree_id=worktree_id, new=new,
                bare_resume=False, timeout=timeout)
        raise

    # agent-bridge's ACP path nests the plan under ``launch``; the interactive
    # resolve emits it flat. Unwrap defensively so either shape parses (mirrors
    # the shell launcher's own unwrap).
    if isinstance(obj.get("launch"), dict):
        obj = obj["launch"]
    return _to_launch_plan(obj)


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
    return [worktree_from_dict(d) for d in rows if isinstance(d, dict)]
