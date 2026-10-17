"""Derive bridge identity through the owning project's existing authority."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_procutil import no_window_kwargs


class ExecutionIdentityError(ValueError):
    """The daemon cannot establish an unambiguous local execution binding."""


def source_repository(path: Path) -> Path | None:
    """Locate a source checkout, retaining its stable anchor across detach."""
    from .config import _canonical_repo_root

    path = path.expanduser().resolve()
    if not path.is_dir():
        return None
    for root in (path, *path.parents):
        if (root / ".git").exists():
            return _canonical_repo_root(root)
        if (root / "HEAD").is_file() and (root / "objects").is_dir() and (root / "config").is_file():
            return root
    return None


def _query(prefix: list[str], args: list[str], cwd: Path | None) -> subprocess.CompletedProcess:
    from .session_lifecycle_cli import _agent_worktrees_environment

    environment = {
        key: value for key, value in _agent_worktrees_environment().items()
        if not key.upper().startswith("GIT_")
    }
    try:
        return subprocess.run(
            [*prefix, *args], cwd=cwd, env=environment,
            capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", timeout=30,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        target = str(cwd) if cwd is not None else " ".join(args)
        raise ExecutionIdentityError(
            f"Cannot read source-project execution binding {target!r} from agent-worktrees"
        ) from exc


def _read_identity(prefix: list[str], args: list[str], cwd: Path | None) -> tuple[str | None, str]:
    """Only the versioned configured-binding contract can provide authority."""
    from . import agent_registry

    result = _query(prefix, [*args, "get", "execution-identity"], cwd)
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None, "compatible execution-identity reader unavailable"
    if (
        not isinstance(data, dict)
        or data.get("schema") != "agent-worktrees.execution-identity"
        or type(data.get("version")) is not int or data["version"] != 1
    ):
        return None, "compatible execution-identity reader unavailable"
    binding = str(cwd) if cwd is not None else " ".join(args)
    if result.returncode or data.get("status") == "refused":
        raise ExecutionIdentityError(
            f"Source-project machine binding {binding!r} was refused: "
            + str(data.get("reason") or "execution authority unavailable")
        )
    if data.get("status") == "unavailable":
        return None, str(data.get("reason") or "source binding unavailable")
    if (
        data.get("status") != "ready"
        or data.get("configured") is not True
        or not isinstance(data.get("source"), str)
        or data["source"] not in {"project-machine-config", "global-machine-config"}
        or not isinstance(data.get("machine"), str) or not data["machine"]
        or not isinstance(data.get("project"), str) or not data["project"]
        or data.get("platform") != agent_registry._detect_platform()
    ):
        raise ExecutionIdentityError(f"Source-project machine binding {binding!r} returned invalid authority")
    return data["machine"], ""


def resolve_execution_space(cfg, machines: dict) -> str | None:
    """Resolve once per roster build; never persist a derived machine key."""
    from . import agent_registry
    from .session_lifecycle_cli import _agent_worktrees_launch_prefix
    from .topology import load_control_plane_project

    if not machines:
        return None
    legacy = getattr(cfg, "local_execution_space", None)
    if not legacy and not any(
        entry.execution_platform and not entry.legacy_key for entry in machines.values()
    ):
        return None
    prefix = _agent_worktrees_launch_prefix()
    identities: dict[str, set[str]] = {}
    origin = getattr(cfg, "_source_worktree", None)
    root = source_repository(Path(origin)) if origin is not None else None
    unresolved_targets: set[str] = set()
    if prefix:
        readings: dict[tuple[tuple[str, ...], Path | None], tuple[str | None, str]] = {}

        def read(args: list[str], cwd: Path | None) -> tuple[str | None, str]:
            signature = (tuple(args), cwd)
            if signature not in readings:
                readings[signature] = _read_identity(prefix, args, cwd)
            return readings[signature]

        sources: list[tuple[list[str], Path | None]] = []
        if root is not None:
            key, reason = read([], root)
            if key is not None:
                sources.append(([], root))
            else:
                unresolved_targets.add(f"{root}: {reason}")
        if not sources:
            for profile_name, profile in cfg.topologies.items():
                source_repo = getattr(profile, "source_repo", None)
                if source_repo is not None:
                    source = Path(source_repo).expanduser()
                    if not source.is_absolute():
                        raise ExecutionIdentityError(
                            f"Topology profile {profile_name!r} source_repo {source_repo!r} "
                            "must be an absolute configuration locator"
                        )
                    source = source.resolve()
                    if not source.is_dir():
                        raise ExecutionIdentityError(
                            f"Topology profile {profile_name!r} source repository {str(source)!r} "
                            "is unavailable; re-adopt its binding"
                        )
                    sources.append(([], source))
                elif profile.machines_yaml:
                    project = load_control_plane_project(profile.machines_yaml)
                    if project:
                        sources.append((["--project", project], None))
                    else:
                        source = source_repository(Path(profile.machines_yaml).expanduser().resolve().parent)
                        if source is not None:
                            sources.append(([], source))
        seen: set[tuple[tuple[str, ...], Path | None]] = set()
        for args, cwd in sources:
            signature = (tuple(args), cwd)
            if signature in seen:
                continue
            seen.add(signature)
            binding = str(cwd) if cwd is not None else " ".join(args)
            key, reason = read(args, cwd)
            if key is None:
                unresolved_targets.add(f"{binding}: {reason}")
                continue
            try:
                agent_registry._detect_local_machine(machines, key)
            except ValueError as exc:
                raise ExecutionIdentityError(
                    f"Source-project machine binding {binding!r} is not valid for this daemon: " + str(exc)
                ) from exc
            identities.setdefault(key, set()).add(binding)
    if len(identities) > 1:
        details = "; ".join(
            f"{key!r} from {', '.join(repr(origin) for origin in sorted(origins))}"
            for key, origins in sorted(identities.items())
        )
        raise ExecutionIdentityError(
            "Adopted source projects select different execution spaces: "
            + details + "; choose one source worktree"
        )
    derived = next(iter(identities), None)
    if derived is not None and legacy is not None and derived != legacy:
        raise ExecutionIdentityError(
            "Legacy local_execution_space contradicts authoritative source-project machine configuration"
        )
    selected = derived or legacy
    if selected is None:
        reason = (
            "No verified identity reader is available. "
            if not prefix else "No source-project binding resolved. "
        )
        if unresolved_targets:
            reason += "Unresolved sources: " + ", ".join(repr(target) for target in sorted(unresolved_targets)) + ". "
        raise ExecutionIdentityError(
            f"Missing source-project execution binding for independent registered spaces: {reason}"
            "Start or adopt from a registered source worktree with configured machine authority. "
            "Standalone bridges may retain an existing local_execution_space selector."
        )
    if derived is None:
        try:
            agent_registry._detect_local_machine(machines, selected)
        except ValueError as exc:
            raise ExecutionIdentityError("Legacy bridge selector is invalid: " + str(exc)) from exc
    return selected
