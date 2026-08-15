"""agent-ssh :: mesh-status :: render the SSH machine mesh from a repo's config.

``mesh-status`` reads the **calling repo's** ``machines.yaml`` (the repo-scoped
mesh registry: ``machines: <key>: {display_name, role, environment, ssh.ready,
ssh.environments[], dtssh}``) and reports, per machine, its role, environment,
reachability (``ssh.ready``), the per-environment SSH aliases, and dtssh notes.

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
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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


def find_machines_file(start: Path | None = None) -> Path | None:
    """Resolve ``machines.yaml`` for the calling repo.

    Prefer the git top-level of *start* (or cwd); fall back to walking parents so
    the command also works from a subdirectory when git is unavailable.
    """
    start = (start or Path.cwd()).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
        )
        if top.returncode == 0:
            root = Path(top.stdout.strip())
            cand = root / "machines.yaml"
            if cand.is_file():
                return cand
    except FileNotFoundError:
        pass
    for parent in (start, *start.parents):
        cand = parent / "machines.yaml"
        if cand.is_file():
            return cand
    return None


def _load(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        return json.loads(text) or {}


def load_mesh(path: Path) -> Mesh:
    """Project a ``machines.yaml`` document into a :class:`Mesh`."""
    doc = _load(path)
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
            machines.append(
                MeshMachine(
                    key=str(key),
                    display_name=str(entry.get("display_name", "") or ""),
                    role=str(entry.get("role", "") or ""),
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
