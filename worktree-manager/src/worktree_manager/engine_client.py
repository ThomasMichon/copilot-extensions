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
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The engine binstub name (the self-provisioning agent-worktrees tool CLI).
ENGINE_BIN = "agent-worktrees"

#: Override the base engine command (everything before ``[--project …] <verb>``).
#: Set to a shell-quoted command to point the client at a *fake* engine binstub
#: instead of the real one -- the seam that makes the Manager (and its Picker)
#: buildable, testable, and demo-able without a live agent-worktrees, faithfully
#: through the same subprocess + JSON-parse path. The ``--demo`` Picker mode sets
#: this to the bundled Aperture Labs fake engine.
ENGINE_CMD_ENV = "WORKTREE_MANAGER_ENGINE_CMD"

#: Exact provider argv handed to the Manager by agent-worktrees. JSON avoids
#: shell quoting and preserves an attributable immutable runtime command.
ENGINE_ARGV_ENV = "WORKTREE_MANAGER_ENGINE_ARGV"

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


class EngineFeatureUnavailable(EngineError):
    """The installed engine predates one optional Manager control-plane seam."""


def _engine_error_detail(error: EngineError) -> str:
    """Return the engine's payload/stderr detail without the echoed argv."""
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


def _state_home() -> Path:
    override = os.environ.get("AGENT_HOME")
    if override:
        return Path(override)
    variable = "USERPROFILE" if os.name == "nt" else "HOME"
    return Path(os.environ.get(variable) or Path.home())


def _version_key(version: str):
    supported = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?", version)
    if supported:
        major, minor, patch, dev = supported.groups()
        return (
            1,
            int(major),
            int(minor),
            int(patch),
            1 if dev is None else 0,
            int(dev or 0),
        )
    tokens = re.split(r"(\d+)", version.casefold())
    return (0, tuple((1, int(t)) if t.isdigit() else (0, t) for t in tokens))


def _runtime_candidates(root: Path) -> list[Path]:
    versions = root / "versions"
    candidates: list[Path] = []

    def contained_slot(version: str) -> Path | None:
        if (
            not version
            or version in {".", ".."}
            or Path(version).name != version
        ):
            return None
        try:
            versions_root = versions.resolve()
            candidate = (versions / version).resolve()
        except OSError:
            return None
        if candidate.parent != versions_root:
            return None
        return candidate

    for marker_name in ("current-version", "last-known-good"):
        try:
            version = (root / marker_name).read_text(encoding="utf-8").strip()
        except OSError:
            version = ""
        candidate = contained_slot(version)
        if candidate is not None:
            candidates.append(candidate)
    try:
        fallback = sorted(
            (path for path in versions.iterdir() if path.is_dir()),
            key=lambda path: _version_key(path.name),
            reverse=True,
        )
    except OSError:
        fallback = []
    candidates.extend(
        candidate
        for path in fallback
        if (candidate := contained_slot(path.name)) is not None
    )
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve()))
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def installed_engine_command() -> list[str] | None:
    """Resolve the exact marker-selected agent-worktrees runtime.

    The deployment manifest attests the owning provider; its marker/fallback
    files select the immutable runtime slot. A bare command name or PATH lookup
    is never accepted.
    """
    root = _state_home() / ".agent-worktrees"
    try:
        manifest = json.loads(
            (root / "deploy-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(manifest, dict):
        return None
    source = manifest.get("source")
    if (
        manifest.get("service") != ENGINE_BIN
        or not isinstance(source, dict)
        or source.get("plugin") != ENGINE_BIN
    ):
        return None
    for slot in _runtime_candidates(root):
        if not (slot / ".install-complete.json").is_file():
            continue
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.is_file():
            return [str(python), "-m", "agent_worktrees"]
    return None


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
    except EngineError:
        return False


def _run(
    project: str | None,
    args: list[str],
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    allow_nonzero: bool = False,
) -> str:
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
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": False,
            "env": {
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONSAFEPATH": "1",
            },
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise EngineError(f"{ENGINE_BIN} {' '.join(args)} timed out") from e
    except OSError as e:
        raise EngineError(f"could not run {ENGINE_BIN}: {e}") from e
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
             timeout: int = _DEFAULT_TIMEOUT,
             allow_nonzero: bool = False) -> dict:
    """Run a ``--json`` verb and parse its stdout envelope into a dict."""
    raw = _run(project, args, timeout=timeout, allow_nonzero=allow_nonzero)
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
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONSAFEPATH": "1",
            },
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


def launch_plan_from_dict(d: dict) -> LaunchPlan:
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
        raw=d,
    )


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
    timeout: int = _DEFAULT_TIMEOUT,
) -> LaunchPlan:
    """Fetch a launch plan via ``agent-worktrees resolve --json`` (process boundary).

    Exactly one of ``worktree_id`` (resume the worktree), ``new`` (create +
    launch a fresh worktree), or ``base`` (launch the anchor checkout) must be
    given. ``target_machine`` asks the engine to return an environment-specific
    remote SSH handoff plan for that same selection.

    Version-skew tolerant: an older engine that does not know ``--bare-resume`` is
    retried as a plain resume (degrade the feature, don't fail) -- the same contract
    property :func:`list_worktrees` applies to ``--classify``.
    """
    selectors = sum(bool(value) for value in (worktree_id, new, base))
    if selectors != 1:
        raise EngineError(
            "resolve requires exactly one of worktree_id, new=True, or base=True"
        )

    args = ["resolve", "--json"]
    if base:
        args.append("--base")
    elif new:
        args.append("--new")
    else:
        args += ["--worktree-id", worktree_id or ""]
    if bare_resume:
        args.append("--bare-resume")
    if target_machine:
        args += ["--machine", target_machine]
        if target_environment:
            args += ["--environment", target_environment]
        if target_no_mux:
            args.append("--target-no-mux")

    try:
        obj = run_json(project, args, timeout=timeout)
    except EngineError as e:
        detail = _engine_error_detail(e)
        if bare_resume and "--bare-resume" in detail:
            return resolve_launch_plan(
                project, worktree_id=worktree_id, new=new,
                base=base, target_machine=target_machine,
                target_environment=target_environment,
                target_no_mux=target_no_mux,
                bare_resume=False, timeout=timeout)
        if target_machine and any(
            flag in detail
            for flag in ("--machine", "--environment", "--target-no-mux")
        ):
            raise EngineFeatureUnavailable(
                "the installed engine predates remote Picker launch plans"
            ) from e
        if base and (
            "--base" in detail
            or "--json requires --worktree-id or --new" in detail
        ):
            obj = run_json(
                project,
                ["resolve", "--base", "--no-mux"],
                timeout=timeout,
            )
        else:
            raise

    # agent-bridge's ACP path nests the plan under ``launch``; the interactive
    # resolve emits it flat. Unwrap defensively so either shape parses (mirrors
    # the shell launcher's own unwrap).
    if isinstance(obj.get("launch"), dict):
        obj = obj["launch"]
    return launch_plan_from_dict(obj)


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
