"""Process-boundary client for agent-worktrees; never import its runtime.
Use attributed argv and validated JSON per plugins/agent-worktrees/docs/engine-picker-contract.md.
Optional syntax may downgrade, never authority or terminal resource custody.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .worktree_reporting_contract import row_execution_machine

from . import agent_plugin_runtime, picker_review
from .engine_sessions import list_worktree_session_snapshot as list_worktree_session_snapshot
from .engine_progress import error_from_envelope as _error_from_envelope
from .engine_progress import json_with_failure_detail
from .engine_update_process import run_engine_passthrough as run_engine_passthrough
from .engine_posix_process import EngineCleanupError as EngineCleanupError
from .worktree_rows_contract import (
    _int_field as _int_field,
    _bool_field as _bool_field,
    _dirty_field as _dirty_field,
)
from .engine_environment import (
    _PYTHON_PARENT_ENV as _PYTHON_PARENT_ENV,
    _engine_environment as _engine_environment,
    _is_parent_python_variable as _is_parent_python_variable,
    _terminal_aware_launch_options as _terminal_aware_launch_options,
)

#: The engine binstub name (the self-provisioning agent-worktrees tool CLI).
ENGINE_BIN = "agent-worktrees"

#: Override the base engine command (everything before ``[--project …] <verb>``).
#: Set to a shell-quoted command to point the client at a *fake* engine binstub
#: instead of the real one -- the seam that makes the Manager (and its Picker)
#: buildable, testable, and demo-able without a live agent-worktrees, faithfully
#: through the same subprocess + JSON-parse path. The ``--demo`` Picker mode sets
#: this to the bundled Example Labs fake engine.
ENGINE_CMD_ENV = "WORKTREE_MANAGER_ENGINE_CMD"

#: Exact provider argv handed to the Manager by agent-worktrees. JSON avoids
#: shell quoting and preserves an attributable immutable runtime command.
ENGINE_ARGV_ENV = "WORKTREE_MANAGER_ENGINE_ARGV"

#: A generous ceiling: a cold engine self-provisions on first use, and a classify
#: pass can enumerate many worktrees. Kept bounded so the Manager never hangs.
_DEFAULT_TIMEOUT = 120
class EngineError(RuntimeError):
    """The engine is absent, failed, or returned invalid JSON.
    ``install_hint`` requests ``worktree-manager setup`` for a missing engine.
    """

    def __init__(self, message: str, *, install_hint: bool = False) -> None:
        super().__init__(message)
        self.install_hint = install_hint


class EngineFeatureUnavailable(EngineError):
    """The installed engine predates one optional Manager control-plane seam."""


class EngineRetirementError(EngineError, EngineCleanupError):
    """A terminal engine error that retains owned-resource retirement provenance."""


def _engine_error_detail(error: EngineError) -> str:
    """Return the engine's payload/stderr detail without the echoed argv."""
    if isinstance(error, EngineCleanupError):
        raise error
    text = str(error)
    marker = "): "
    return text.rsplit(marker, 1)[-1] if marker in text else text


def _engine_override() -> list[str] | None:
    """The overriding base engine command from the environment, if set."""
    raw = os.environ.get(ENGINE_CMD_ENV)
    if not raw or not raw.strip():
        return None
    return shlex.split(raw, posix=os.name != "nt")


def _engine_argv_override() -> list[str] | None:
    """Return the provider-owned exact argv inherited from the launch seam."""
    raw = os.environ.get(ENGINE_ARGV_ENV)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise EngineError(f"{ENGINE_ARGV_ENV} is not valid JSON") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(part, str) or not part for part in parsed)
    ):
        raise EngineError(f"{ENGINE_ARGV_ENV} must be a non-empty JSON string array")
    return list(parsed)


_INHERITED_ENGINE_COMMAND: list[str] | None = None


def inherited_engine_command() -> list[str] | None:
    """Return the accepted provider handoff for an explicit queued update."""
    return list(_INHERITED_ENGINE_COMMAND) if _INHERITED_ENGINE_COMMAND else None


def accept_inherited_engine_command() -> str | None:
    """Consume the provider handoff before launching any child processes.

    Returns a warning when the inherited value was malformed. Recovery commands
    remain usable and the bad value is never inherited by descendants.
    """
    global _INHERITED_ENGINE_COMMAND
    raw = os.environ.pop(ENGINE_ARGV_ENV, None)
    if raw is None:
        _INHERITED_ENGINE_COMMAND = None
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        _INHERITED_ENGINE_COMMAND = None
        return f"{ENGINE_ARGV_ENV} is not valid JSON"
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(part, str) or not part for part in parsed)
    ):
        _INHERITED_ENGINE_COMMAND = None
        return f"{ENGINE_ARGV_ENV} must be a non-empty JSON string array"
    _INHERITED_ENGINE_COMMAND = list(parsed)
    return None


def installed_engine_command(environment: dict[str, str] | None = None) -> list[str] | None:
    """Resolve the marker-selected runtime under ordinary or call-scoped ownership.

    The shared receipt resolver never accepts PATH or a bare command name.
    An explicit recovery environment must not reuse an inherited stale command.
    """
    return agent_plugin_runtime.resolve_installed_plugin_command(ENGINE_BIN, environment=environment)


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

    Resolution order: the in-process override (demo/tests) → the explicit
    command override → the exact provider argv inherited from the
    agent-worktrees front door → the validated marker-selected provider runtime.
    A same-named command found through PATH is never used.
    """
    if _ENGINE_CMD_OVERRIDE:
        return list(_ENGINE_CMD_OVERRIDE)
    override = _engine_override()
    if override:
        return override
    if _INHERITED_ENGINE_COMMAND:
        return list(_INHERITED_ENGINE_COMMAND)
    inherited = _engine_argv_override()
    if inherited:
        return inherited
    return installed_engine_command()


def engine_available() -> bool:
    try:
        return engine_base_command() is not None
    except EngineCleanupError:
        raise
    except EngineError:
        return False


def _run(
    project: str | None,
    args: list[str],
    *,
    timeout: int | None = _DEFAULT_TIMEOUT,
    allow_nonzero: bool = False,
    runner: Any = None,
    cwd: str | None = None,
    stream_progress: bool = False,
    environment: dict[str, str] | None = None,
    engine_command: list[str] | None = None,
    preserve_failure_detail: bool = False,
) -> str:
    """Run ``agent-worktrees [--project <p>] <args>`` and return stdout.

    Raises :class:`EngineError` when the binstub is missing (``install_hint``),
    the process fails, or times out. A non-zero exit whose stdout is a JSON
    error envelope surfaces the engine's own ``error`` message. ``cwd``, when
    given, runs the engine from that directory (cwd-scoped verbs)."""
    resolve = engine_base_command if environment is None else lambda: installed_engine_command(environment)
    base = picker_review.engine_command(
        args, lambda: list(engine_command) if engine_command is not None else resolve(),
    )
    if base is None:
        raise EngineError(
            f"the {ENGINE_BIN} engine is not installed", install_hint=True)
    cmd = [*base]
    if project:
        cmd += ["--project", project]
    cmd += args
    try:
        if runner is not None:
            proc = runner(cmd, timeout)
        else:
            kwargs: dict[str, Any] = {
                "capture_output": True, "text": True, "encoding": "utf-8", "timeout": timeout,
                "check": False, "env": _engine_environment(environment),
                "stdin": subprocess.DEVNULL,
                **({"cwd": cwd} if cwd else {}),
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if stream_progress:
                from .engine_process import run as run_preparation
                from .engine_progress import streamed_stderr

                with streamed_stderr() as (stderr_fd, stderr_path):
                    kwargs.pop("capture_output")
                    kwargs.update(stdout=subprocess.PIPE, stderr=stderr_fd)
                    proc = run_preparation(cmd, **kwargs)
                    detail = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
                    if detail:
                        proc.stderr = detail
            else:
                execute = picker_review.run_read if picker_review.active() else subprocess.run
                proc = execute(cmd, **kwargs)
    except EngineCleanupError as e:
        raise EngineRetirementError(f"owned {ENGINE_BIN} resource retirement failed: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"{ENGINE_BIN} {' '.join(args)} timed out") from e
    except OSError as e:
        raise EngineError(f"could not run {ENGINE_BIN}: {e}") from e
    if args == ["get", "update-controller"]:
        from .project_update_controller import legacy_controller_error
        legacy_error = legacy_controller_error(project, proc)
        if legacy_error is not None:
            raise legacy_error
    if preserve_failure_detail and allow_nonzero:
        return cast(str, cast(Any, json_with_failure_detail)(proc.stdout, proc.stderr, failed=proc.returncode != 0))
    if proc.returncode != 0 and allow_nonzero:
        try:
            json.loads(proc.stdout)
        except (ValueError, TypeError):
            detail = _error_from_envelope(proc.stdout) or (proc.stderr or "").strip()
            raise EngineError(
                f"{ENGINE_BIN} {' '.join(args)} failed "
                f"(exit {proc.returncode}): {detail or 'no output'}")
    elif proc.returncode != 0:
        detail = _error_from_envelope(proc.stdout) or (proc.stderr or "").strip()
        raise EngineError(
            f"{ENGINE_BIN} {' '.join(args)} failed "
            f"(exit {proc.returncode}): {detail or 'no output'}")
    return cast(str, proc.stdout)


def run_json(project: str | None, args: list[str], *,
             timeout: int | None = _DEFAULT_TIMEOUT,
             allow_nonzero: bool = False,
             runner: Any = None,
             stream_progress: bool = False,
             environment: dict[str, str] | None = None,
             engine_command: list[str] | None = None,
             preserve_failure_detail: bool = False,
             max_bytes: int | None = None) -> dict[str, Any]:
    """Run a ``--json`` verb; bound its UTF-8 stdout to ``max_bytes``, then parse it."""
    raw = _run(
        project, args, timeout=timeout, allow_nonzero=allow_nonzero, runner=runner,
        stream_progress=stream_progress, environment=environment, engine_command=engine_command,
        preserve_failure_detail=preserve_failure_detail,
    )
    if max_bytes is not None and len(raw.encode("utf-8", errors="surrogatepass")) > max_bytes:
        raise EngineError(
            f"{ENGINE_BIN} {' '.join(args)} reply exceeded {max_bytes} bytes")
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise EngineError(
            f"{ENGINE_BIN} {' '.join(args)} did not return valid JSON") from e
    if not isinstance(obj, dict):
        raise EngineError(f"{ENGINE_BIN} {' '.join(args)} returned non-object JSON")
    return obj


def project_binstub_command(project: str) -> Path:
    """Return the installer-owned project binstub path, or raise."""
    if os.name == "nt":
        home = os.environ.get("USERPROFILE")
        command = Path(home) / ".local" / "bin" / f"{project}.cmd" if home else (
            Path.home() / ".local" / "bin" / f"{project}.cmd"
        )
    else:
        command = Path.home() / ".local" / "bin" / project
    if not command.is_file():
        raise EngineError(f"project binstub is not installed: {command}")
    return command


def run_project_passthrough(
    project: str,
    args: list[str],
    *,
    timeout: int | None = None,
) -> int:
    """Run one installed project binstub with inherited stdio."""
    picker_review.require_writable()
    command = project_binstub_command(project)
    argv = [str(command), *args]
    if os.name == "nt":
        argv = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(argv),
        ]
    try:
        return subprocess.run(
            argv,
            timeout=timeout,
            check=False,
            env=_engine_environment(),
        ).returncode
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"{project} {' '.join(args)} timed out") from e
    except OSError as e:
        raise EngineError(f"could not run project binstub {project}: {e}") from e


def get_value(project: str | None, key: str, *, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Read a pinned scalar value from ``agent-worktrees get <key>``."""
    return _run(project, ["get", key], timeout=timeout).strip()


def repository_account(project: str, *, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Resolve the project's repository-scoped account through the engine."""
    value = run_json(
        project,
        ["repos", "account-for", "--json"],
        timeout=timeout,
    )
    account = value.get("account")
    return str(account) if account else ""


def repository_token(
    project: str,
    account: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Mint the exact account's token through the engine's scoped ``gh`` seam."""
    if not account:
        raise EngineError("repository token resolution requires an account")
    return _run(
        project,
        ["repos", "gh", "--", "auth", "token", "--user", account],
        timeout=timeout,
    ).strip()


# Execution-leg CLI calls (get/set/clear/reserve/renew/release) moved to
# engine_execution_leg.py purely to control this module's size (module-size
# gate). Re-exported via a lazy module __getattr__ (PEP 562) at the bottom of
# this file -- NOT a top-level import -- so `engine_execution_leg.py`'s own
# `from .engine_client import run_json, ...` can fully resolve this module
# first without a circular-import deadlock (reproducible if anything ever
# imports `engine_execution_leg` directly, before `engine_client`). See
# `__getattr__` below.
_EXECUTION_LEG_NAMES = frozenset({
    "execution_leg_clear",
    "execution_leg_get",
    "execution_leg_release",
    "execution_leg_renew",
    "execution_leg_reserve",
    "execution_leg_set",
})


@dataclass(frozen=True)
class Worktree:
    """A worktree row derived from ``list --json --current`` (contract v1).

    Only the fields the Manager renders are lifted into typed attributes; the
    raw dict is kept on ``raw`` so a newer contract field is reachable without a
    code change here (additive-only evolution).
    ``machine`` uses admitted live reporter naming when supported, otherwise
    the legacy row tuple. Neither naming form establishes transport authority.
    """

    id: str
    repo: str
    machine: str
    branch: str
    title: str | None
    state: str | None          # resident-published git-derived state
    ahead: int
    behind: int
    dirty: bool
    status: str | None         # tracking status (active/complete/...)
    path: str | None
    raw: dict[str, Any]

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


def worktree_from_dict(d: dict[str, Any]) -> Worktree:
    """Build the Manager's cross-cutting worktree model from a contract row."""
    return Worktree(
        id=str(d.get("id") or ""),
        repo=str(d.get("repo", "") or ""),
        machine=row_execution_machine(d),
        branch=str(d.get("branch", "") or ""),
        title=(d.get("title") if d.get("title") not in (None, "null") else None),
        state=d.get("state"),
        ahead=_int_field(d.get("ahead"), name="ahead"),
        behind=_int_field(d.get("behind"), name="behind"),
        dirty=_dirty_field(d.get("dirty")),
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
    env: dict[str, Any]
    worktree_id: str | None
    post_exit: bool
    no_mux: bool                # the *engine's* mux suppression (always set by --json)
    exit_code: int
    seed_claimed: bool          # True only when THIS plan's `cmd` embeds a seed
    raw: dict[str, Any]

    @property
    def is_exec(self) -> bool:
        return self.action == "exec"


def launch_plan_from_dict(d: dict[str, Any]) -> LaunchPlan:
    """Parse one engine launch-plan payload."""
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
        # Compatibility-only metadata. Current plans retain the seed in
        # daemon state and carry seed_pending/seed_id/seed_kind in raw.
        seed_claimed=bool(d.get("seed_claimed")),
        raw=d,
    )


def require_agent_hosting(path: str, *, operation: str = "launch") -> dict[str, Any]:
    """Require fresh engine policy, including on unadopted source repositories."""
    from .hosting_admission import require_agent_hosting as require

    return require(path, operation=operation, request=run_json, failure=EngineError)


def resolve_launch_plan(
    project: str,
    *,
    worktree_id: str | None = None,
    new: bool = False,
    bare_resume: bool = False,
    base: bool = False,
    target_machine: str | None = None,
    target_environment: str | None = None,
    target_no_mux: bool = False,
    seed: str | None = None,
    launch_attempt_id: str | None = None,
    defer_new_seed: bool | None = None,
    defer_projections: bool = False,
    timeout: int | None = None,
) -> LaunchPlan:
    """Fetch a launch plan via ``agent-worktrees resolve --json`` (process boundary).

    Exactly one of ``worktree_id`` (resume the worktree), ``new`` (create +
    launch a fresh worktree), or ``base`` (launch the anchor checkout) must be
    given. ``target_machine`` asks the engine to return an environment-specific
    remote SSH handoff plan for that same selection. ``seed`` is an optional
    prompt queued as the session's first (``new=True``) or next
    (``worktree_id`` resume, resume-prompt-durable-seed-and-mux-fix) interactive
    turn. The returned command carries its staged identity; setup supplies
    full ``--interactive`` only at backend handoff, not at plan construction.
    Meaningful with either ``new=True`` or a plain resume
    (``worktree_id`` set, ``bare_resume=False``); the engine's own CLI already
    rejects it with ``base=True`` or ``bare_resume=True``. With a target machine,
    the engine only packages a TTY SSH handoff. The target's ordinary command
    performs all preparation after the Picker exits.

    Prompted New/Resume requests require typed retry staging. An older engine
    must refuse the capability flag before mutation, rather than silently
    dropping preservation. Every launch also requires hosting-policy admission;
    an engine predating that capability is refused without a weaker fallback.

    Version-skew tolerant: an older engine that does not know ``--bare-resume`` is
    retried as a plain resume (degrade the feature, don't fail) -- the same contract
    property :func:`list_worktrees` applies to ``--classify``.

    Foreground preparation waits for completion by default and streams stderr
    progress. Individual engine operations retain their own failure bounds;
    an explicit timeout remains available to bounded automation.
    Shell-delegated launches can defer publication with a call-scoped environment
    hint; older engines can ignore it without rejecting their resolve arguments.
    """
    selectors = sum(bool(value) for value in (worktree_id, new, base))
    if selectors != 1:
        raise EngineError(
            "resolve requires exactly one of worktree_id, new=True, or base=True"
        )
    from .seed_launch import require_launch_capabilities, require_attempt_plan, require_attempt_target
    require_attempt_target(launch_attempt_id, target_machine=target_machine, seed=seed)
    if target_machine:
        from machine_transport import split_machine_environment

        try:
            selected = split_machine_environment(target_machine, target_environment)
        except ValueError as exc:
            raise EngineError(str(exc)) from exc
        target_machine = selected.machine
        target_environment = target_environment or selected.environment or None
    from .hosting_admission import require_owner_hosting

    def admit_owner() -> None:
        if target_machine:
            return  # The ordinary target command enforces its own policy after SSH.
        require_owner_hosting(
            project, worktree_id,
            request=lambda owner, argv: run_json(owner, argv, allow_nonzero=True),
            failure=EngineFeatureUnavailable, support_only=bool(target_machine),
        )

    admit_owner()

    args = ["resolve", "--json"]
    require_staging = bool(seed) or bool(defer_new_seed)
    retained_seed_plans = bool(worktree_id and not require_staging and not bare_resume and not target_machine)
    if require_staging:
        args += ["--stage-launch-seed", "--require-native-seed"]
    if base:
        args.append("--base")
    elif new:
        args.append("--new")
    else:
        args += ["--worktree-id", worktree_id or ""]
    if bare_resume:
        args.append("--bare-resume")
    if target_machine:
        args += ["--machine", target_machine, "--require-terminal-handoff"]
        if target_environment:
            args += ["--environment", target_environment]
        if target_no_mux:
            args.append("--target-no-mux")
    if seed:
        args += ["--seed", seed]
    if launch_attempt_id is not None:
        args += ["--require-launch-attempt", "--launch-attempt-id", launch_attempt_id]

    try:
        obj = run_json(
            project, args,
            timeout=timeout,
            stream_progress=True,
            environment={
                **os.environ,
                **({"AGENT_WORKTREES_DEFER_PROJECTIONS": "1"} if defer_projections else {}),
                **({"AGENT_WORKTREES_RETAINED_SEED_PLANS": "1"} if retained_seed_plans else {}),
            } if defer_projections or retained_seed_plans else None,
            engine_command=engine_base_command() if defer_projections or retained_seed_plans else None,
        )
    except EngineError as e:
        detail = _engine_error_detail(e)
        if target_machine and "--require-terminal-handoff" in detail and "unrecognized arguments" in detail:
            raise EngineFeatureUnavailable(
                "the selected engine predates SSH-first terminal handoff; update before launching"
            ) from e
        require_launch_capabilities(detail, attempt_id=launch_attempt_id, staged=require_staging)
        if (
            bare_resume
            and "unrecognized arguments" in detail
            and "--bare-resume" in detail
        ):
            return resolve_launch_plan(
                project, worktree_id=worktree_id, new=new,
                base=base, target_machine=target_machine,
                target_environment=target_environment,
                target_no_mux=target_no_mux, seed=seed,
                launch_attempt_id=launch_attempt_id,
                defer_new_seed=defer_new_seed,
                defer_projections=defer_projections,
                bare_resume=False, timeout=timeout)
        if target_machine and any(
            flag in detail
            for flag in ("--machine", "--environment", "--target-no-mux")
        ):
            raise EngineFeatureUnavailable(
                "the installed engine predates remote Picker launch plans"
            ) from e
        if base and launch_attempt_id is None and (
            "--base" in detail
            or "--json requires --worktree-id or --new" in detail
        ):
            admit_owner()
            obj = run_json(
                project,
                ["resolve", "--base", "--no-mux"],
                timeout=timeout,
                stream_progress=True,
            )
        else:
            raise

    # agent-bridge's ACP path nests the plan under ``launch``; the interactive
    # resolve emits it flat. Unwrap defensively so either shape parses (mirrors
    # the shell launcher's own unwrap).
    if isinstance(obj.get("launch"), dict):
        obj = obj["launch"]
    require_attempt_plan(obj, launch_attempt_id)
    if target_machine:
        from .seed_launch import require_terminal_handoff_plan
        require_terminal_handoff_plan(obj, worktree_id=worktree_id)
    if seed and obj.get("action") == "exec" and (
        obj.get("seed_pending") is not True
        or not isinstance(obj.get("seed_id"), str) or not obj["seed_id"]
        or obj.get("seed_kind") != ("new" if new else "resume")
    ):
        raise EngineFeatureUnavailable(
            "the engine omitted typed staged-prompt ownership; update and retry"
        )
    return launch_plan_from_dict(obj)


def list_worktree_rows(
    project: str | None,
    *,
    classify: bool = True,
    mux_details: bool = False,
    cache_only: bool = False,
    fresh: bool = False,
    worktree_id: str | None = None,
    refresh: bool = False,
    current: bool = True,
    runner: Any = None,
) -> list[dict[str, Any]]:
    """Return raw worktree rows from the provider's JSON list contract.

    Current-roster authority is mandatory by default. Optional presentation
    flags may be dropped for compatibility; authority is never downgraded.
    """
    if picker_review.active():
        if not current or refresh:
            raise EngineError(picker_review.MESSAGE)
        return picker_review.worktree_rows(cast(str, project), worktree_id=worktree_id)
    args = ["list", "--json"]
    if current:
        args.append("--current")
    optional: list[tuple[str, bool]] = [
        ("--classify", classify),
        ("--mux-details", mux_details),
        ("--cache-only", cache_only),
        ("--fresh", fresh),
    ]
    args.extend(flag for flag, enabled in optional if enabled)
    if worktree_id:
        args += ["--worktree-id", worktree_id]
    if refresh:
        args.append("--refresh")

    try:
        obj = run_json(project, args, runner=runner)
    except EngineError as error:
        detail = _engine_error_detail(error)
        if current and "unrecognized arguments" in detail and "--current" in detail:
            raise EngineFeatureUnavailable(
                "the selected engine predates resident current-roster reads; update and retry"
            ) from error
        for flag, enabled in optional:
            if enabled and "unrecognized arguments" in detail and flag in detail:
                return list_worktree_rows(
                    project,
                    classify=classify and flag != "--classify",
                    mux_details=mux_details and flag != "--mux-details",
                    cache_only=cache_only and flag != "--cache-only",
                    fresh=fresh and flag != "--fresh",
                    worktree_id=worktree_id,
                    refresh=refresh,
                    current=current,
                    runner=runner,
                )
        if (
            not current
            and
            "unrecognized arguments" in detail
            and worktree_id
            and ("--worktree-id" in detail or "--refresh" in detail)
        ):
            if refresh:
                _run(
                    project,
                    ["backfill-sessions"],
                    timeout=60,
                    runner=runner,
                )
            refreshed_rows = list_worktree_rows(
                project,
                classify=classify,
                mux_details=mux_details,
                cache_only=cache_only,
                fresh=fresh,
                runner=runner,
            )
            exact = [row for row in refreshed_rows if row.get("id") == worktree_id]
            if exact:
                return exact
            matches = [
                row for row in refreshed_rows
                if isinstance(row.get("id"), str)
                and row["id"].endswith(worktree_id)
            ]
            return matches if len(matches) == 1 else []
        raise
    rows = obj.get("worktrees")
    if current:
        from .worktree_rows_contract import parse_current_rows

        try:
            return parse_current_rows(obj)
        except (TypeError, ValueError) as error:
            raise EngineError(str(error)) from error
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def list_worktrees(project: str, *, classify: bool = True) -> list[Worktree]:
    """List a project's worktrees via ``agent-worktrees list --json``.

    Requests the resident current roster. An older engine is an explicit
    upgrade error, never permission to enumerate tracking or compute state.
    """
    return [
        worktree_from_dict(row)
        for row in list_worktree_rows(project, classify=classify)
    ]


def current_worktree_status(
    *,
    path: str | None = None,
    fetch: bool = False,
    project: str | None = None,
    runner: Any = None,
) -> dict[str, Any] | None:
    """Cheap, single-worktree status snapshot via ``status-segment --json``.

    Prefer this over ``list_worktree_rows(..., worktree_id=...)`` for a
    single current-worktree lookup (the Mux Companion's use case): that verb
    still negotiates with the resident classify daemon using project-wide
    filters even when scoped to one id, paying a whole-fleet round trip
    regardless. This reuses the status bar's own non-daemon classify pass.

    Resolves from ``path`` (default: cwd). Returns the envelope payload
    (``id``/``path``/``repo``/``branch``/``state``/``ahead``/``behind``/
    ``dirty``/``turn_count``/``status``/``closure``), with an ``"error"`` key
    when unresolved -- never raises for that case. Raises
    :class:`EngineError` only when the engine itself is unreachable.
    """
    args = ["status-segment", "--json"]
    if path:
        args += ["--path", path]
    if fetch:
        args.append("--fetch")
    return run_json(project, args, runner=runner)


def find_worktree_for_path(
    path: str,
    *,
    project: str | None = None,
    runner: Any = None,
) -> dict[str, Any] | None:
    """Return the raw ``list --json`` row whose worktree contains ``path``.

    Cache-only (no ``--classify``) so this is cheap enough to call before the
    caller even knows a worktree id -- exactly the resolution a hotkey-summoned,
    "what worktree am I in" surface (the Mux Companion) needs on every launch.
    Matches ``path`` itself or, walking upward, its nearest containing
    worktree root, so a caller whose cwd is a subdirectory of the worktree
    still resolves (mirrors ``agent-worktrees``' own path-to-record matching).
    Returns ``None`` when no tracked worktree contains ``path``.
    """
    rows = list_worktree_rows(project, classify=False, cache_only=True, runner=runner)
    by_path: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_path = row.get("path")
        if not isinstance(row_path, str) or not row_path:
            continue
        try:
            by_path[str(Path(row_path).resolve())] = row
        except OSError:
            continue
    try:
        current = Path(path).resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        matching = by_path.get(str(candidate))
        if matching is not None:
            return matching
    return None


def list_worktree_sessions(
    project: str,
    worktree_id: str,
    *,
    runner: Any = None,
) -> list[dict[str, Any]]:
    """Return one worktree's registered sessions through the provider CLI."""
    obj = list_worktree_session_snapshot(project, worktree_id, runner=runner)
    rows = obj.get("sessions")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def recent_worktree_messages(
    project: str,
    worktree_id: str,
    *,
    limit: int = 3,
    runner: Any = None,
) -> dict[str, Any]:
    """Return one worktree's recent conversation messages."""
    return run_json(
        project,
        [
            "recent-messages",
            "--worktree",
            worktree_id,
            "--limit",
            str(limit),
            "--json",
        ],
        runner=runner,
    )


def orphaned_obligations(project: str, *, runner: Any = None) -> list[dict[str, Any]]:
    """Return this machine's durable claims-orphanage for ``project``.

    Mirrors ``agent-worktrees claims orphans --json``: obligations re-homed by
    an ``--abandon`` finalize, awaiting ``claims cleanup`` -- a re-homed claim
    with no worktree row of its own to attach to (worktree-claims-transitive-
    finalization Phase 4 item 2). The orphanage registry is per-machine local
    state (never git-synced), so this never reaches beyond the engine this
    call targets -- there is no cross-machine aggregation to perform here.

    Degrades to an empty list rather than raising on an older engine that
    predates the ``claims orphans`` verb: this is a visibility nicety for the
    Picker, never a required capability.
    """
    try:
        obj = run_json(project, ["claims", "orphans", "--json"], runner=runner)
    except EngineCleanupError:
        raise
    except EngineError:
        return []
    rows = obj.get("orphaned")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def __getattr__(name: str) -> Any:
    """Lazily resolve the re-exported `execution_leg_*` names (PEP 562).

    Deferring the import to first access (rather than a top-level import)
    is what breaks the circular-import deadlock: `engine_execution_leg.py`
    itself does `from .engine_client import run_json, ...`, which needs this
    module fully initialized first. A module-level import here would try to
    import `engine_execution_leg` while THIS module is still mid-init
    whenever something imports `engine_execution_leg` directly before
    `engine_client` -- this function is never even called until something
    does `engine_client.execution_leg_get(...)` etc., well after both
    modules have finished initializing.
    """
    if name in _EXECUTION_LEG_NAMES:
        from . import engine_execution_leg
        return getattr(engine_execution_leg, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
