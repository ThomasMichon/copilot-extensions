"""agent-ssh :: mesh-status :: render the SSH machine mesh from a repo's config.

``mesh-status`` reads the **calling repo's** ``machines.yaml`` (the repo-scoped
mesh registry: ``machines: <key>: {display_name, role, environment, ssh.ready,
description, capabilities, ssh.environments[], dtssh}``) and reports, per
machine, its role, purpose, broad capabilities, environment, reachability
(``ssh.ready``), per-environment SSH aliases, and dtssh notes.

This is the on-demand renderer behind the succinct ``sessionStart`` pointer
(``scripts/emit-mesh-pointer.*``): the pointer tells an agent the mesh exists and
to run ``agent-ssh mesh-status``; this module produces the actual table. It is
**config-driven and repo-specific** -- it renders whatever ``machines.yaml`` the
current repo ships (or ``--path``), and says nothing when the repo has none, so a
globally-loaded plugin never leaks one repo's mesh into another.

Read-only: it parses config and prints; it never probes or mutates. (For a live
reachability probe of a single target, use ``agent-ssh verify``/``explore``.)
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


@dataclass
class MeshEnv:
    """One SSH environment on a machine (windows/wsl/...)."""

    name: str
    alias: str = ""
    shell: str = ""
    user: str = ""


@dataclass
class MeshMachine:
    """One machine in the mesh, projected from a ``machines.yaml`` entry."""

    key: str
    display_name: str = ""
    role: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    environment: str = ""
    hostname: str = ""
    ssh_ready: bool = False
    environments: list[MeshEnv] = field(default_factory=list)
    dtssh_alias: str = ""
    dtssh_best_effort: bool = False


@dataclass
class Mesh:
    """The mesh projected from one repo's ``machines.yaml``."""

    project: str = ""
    source: str = ""
    machines: list[MeshMachine] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _machines_file_at(root: Path) -> Path | None:
    for relative in (
        Path("machines.yaml"),
        Path(".agent-worktrees/machines.yaml"),  # marketplace-isolation: allow registry
        Path("config/machines.yaml"),
        Path(".github/machines.yaml"),
    ):
        candidate = root / relative
        try:
            mode = candidate.stat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            if candidate.is_symlink():
                raise ValueError(f"machines.yaml source is a broken symlink: {candidate}")
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"machines.yaml source is not a regular file: {candidate}")
        return candidate
    return None


def find_machines_file(start: Path | None = None) -> Path | None:
    """Resolve ``machines.yaml`` for the calling repo.

    Use Bridge's existing conventional location priority within the calling Git
    repository. Without Git, walk parents but never cross a ``.git`` boundary.
    """
    start = (start or Path.cwd()).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
            creationflags=no_window_flags(),
        )
        if top.returncode == 0:
            root = Path(top.stdout.strip())
            return _machines_file_at(root)
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired as exc:
        raise ValueError("timed out resolving the calling Git repository") from exc
    for parent in (start, *start.parents):
        cand = _machines_file_at(parent)
        if cand is not None:
            return cand
        if (parent / ".git").exists():
            break
    return None


def _load(path: Path, *, strict: bool = False) -> dict[str, Any]:
    """Load a machines.yaml document, fail-safe.

    Legacy context consumers remain tolerant. CLI and mutating consumers use
    strict loading so an unavailable or malformed source cannot look empty.
    """
    if strict and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"machines.yaml source is not a regular file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        if strict:
            raise
        return {}
    try:
        import yaml
    except ModuleNotFoundError:
        try:
            doc = json.loads(text)
        except (ValueError, TypeError) as exc:
            if strict:
                raise ValueError(f"cannot parse machines.yaml at {path}: {exc}") from exc
            return {}
    else:
        try:
            doc = yaml.safe_load(text)
        except (yaml.YAMLError, ValueError) as exc:
            if strict:
                raise ValueError(f"cannot parse machines.yaml at {path}: {exc}") from exc
            return {}
    if strict and (not isinstance(doc, dict) or not isinstance(doc.get("machines"), dict)):
        raise ValueError(f"machines.yaml at {path} must contain a 'machines' mapping")
    if strict:
        for key, entry in doc["machines"].items():
            if not isinstance(entry, dict):
                raise ValueError(f"machine '{key}' at {path} must be a mapping")
            for block in ("ssh", "dtssh"):
                if entry.get(block) is not None and not isinstance(entry[block], dict):
                    raise ValueError(f"machine '{key}' {block} at {path} must be a mapping")
            if type((entry.get("ssh") or {}).get("ready", False)) is not bool:
                raise ValueError(f"machine '{key}' SSH readiness at {path} must be a boolean")
            dtssh_alias = (entry.get("dtssh") or {}).get("alias")
            if dtssh_alias is not None and not isinstance(dtssh_alias, str):
                raise ValueError(f"machine '{key}' dtssh alias at {path} must be a string")
            environments = (entry.get("ssh") or {}).get("environments", [])
            if environments is None:
                environments = []
            if not isinstance(environments, list) or any(
                not isinstance(environment, dict) for environment in environments
            ):
                raise ValueError(f"machine '{key}' SSH environments at {path} must be mappings in a list")
            for environment in environments:
                alias = environment.get("alias")
                if alias is not None and not isinstance(alias, str):
                    raise ValueError(f"machine '{key}' SSH alias at {path} must be a string")
    return doc or {}


def load_mesh(path: Path, *, strict: bool = False) -> Mesh:
    """Project topology; mutating callers must request strict input validation."""
    doc = _load(path, strict=strict)
    if not isinstance(doc, dict):
        return Mesh(source=str(path))
    project = ""
    cp = doc.get("control_plane")
    if isinstance(cp, dict):
        project = str(cp.get("project", "") or "")

    machines: list[MeshMachine] = []
    raw_machines = doc.get("machines")
    if isinstance(raw_machines, dict):
        for key, entry in raw_machines.items():
            if not isinstance(entry, dict):
                continue
            ssh = entry.get("ssh") if isinstance(entry.get("ssh"), dict) else {}
            dtssh = entry.get("dtssh") if isinstance(entry.get("dtssh"), dict) else {}
            envs: list[MeshEnv] = []
            for env in ssh.get("environments", []) or []:
                if isinstance(env, dict):
                    envs.append(
                        MeshEnv(
                            name=str(env.get("name", "") or ""),
                            alias=str(env.get("alias", "") or ""),
                            shell=str(env.get("shell", "") or ""),
                            user=str(env.get("user", "") or ""),
                        )
                    )
            description_raw = entry.get("description", "")
            description = (
                description_raw.strip()
                if isinstance(description_raw, str)
                else ""
            )
            capabilities: list[str] = []
            capabilities_raw = entry.get("capabilities", [])
            if isinstance(capabilities_raw, list):
                for capability_raw in capabilities_raw:
                    if not isinstance(capability_raw, str):
                        continue
                    capability = capability_raw.strip()
                    if capability and capability not in capabilities:
                        capabilities.append(capability)
            machines.append(
                MeshMachine(
                    key=str(key),
                    display_name=str(entry.get("display_name", "") or ""),
                    role=str(entry.get("role", "") or ""),
                    description=description,
                    capabilities=capabilities,
                    environment=str(entry.get("environment", "") or ""),
                    hostname=str(entry.get("hostname", "") or ""),
                    ssh_ready=bool(ssh.get("ready", False)),
                    environments=envs,
                    dtssh_alias=str(dtssh.get("alias", "") or ""),
                    dtssh_best_effort=bool(dtssh.get("best_effort", False)),
                )
            )
    return Mesh(project=project, source=str(path), machines=machines)


def summary_line(mesh: Mesh) -> str:
    """A one-line summary used by the sessionStart pointer / ``--summary``."""
    n = len(mesh.machines)
    ready = sum(1 for m in mesh.machines if m.ssh_ready)
    label = mesh.project or "this repo"
    return f"{label}: {n} machine(s) in machines.yaml, {ready} SSH-ready."


def format_report(mesh: Mesh) -> str:
    """Human-readable mesh table."""
    lines: list[str] = []
    header = f"agent-ssh mesh-status: {mesh.project or '(unnamed)'}"
    lines.append(header)
    lines.append(f"  source: {mesh.source}")
    if not mesh.machines:
        lines.append("  (no machines declared)")
        return "\n".join(lines)
    lines.append(f"  machines ({len(mesh.machines)}):")
    for m in mesh.machines:
        reach = "ready" if m.ssh_ready else "not-ready"
        name = m.display_name or m.key
        host = f" [{m.hostname}]" if m.hostname and m.hostname != m.key else ""
        lines.append(f"    - {name}{host}  role={m.role or '?'}  ssh={reach}")
        if m.environment:
            lines.append(f"        env: {m.environment}")
        if m.description:
            lines.append(f"        description: {m.description}")
        if m.capabilities:
            lines.append(f"        capabilities: {', '.join(m.capabilities)}")
        for env in m.environments:
            u = f" user={env.user}" if env.user else ""
            sh = f" ({env.shell})" if env.shell else ""
            lines.append(f"        ssh {env.name}: {env.alias}{sh}{u}".rstrip())
        if m.dtssh_alias:
            be = " [best-effort: up only while logged in]" if m.dtssh_best_effort else ""
            lines.append(f"        dtssh: ssh {m.dtssh_alias}{be}")
    lines.append("")
    lines.append(
        "  Reach a host interactively with `ssh <alias>` (canonical machines.yaml "
        "aliases). Reachability is dtssh: live only while the target is powered on "
        "and logged in. `ssh.ready` reflects the operator's declared state, not a "
        "live probe -- use `agent-ssh verify <alias>` to probe now."
    )
    return "\n".join(lines)
