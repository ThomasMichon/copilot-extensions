"""Unattended dtssh mesh convergence.

Re-discovers live dtssh tunnel ids, re-emits the managed SSH ``config.d``
fragment from that live state, and verifies reachability to every
``machines.yaml`` dtssh alias.

This closes the automation gap described in copilot-extensions#2684: nothing
previously re-ran ``dtssh discover`` + ``emit-profile`` on a schedule, so a
machine's locally cached tunnel id for a peer could go stale (e.g. after the
peer's dtssh host restarted) and inbound SSH would silently break until an
operator noticed and re-ran the client refresh by hand.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from . import fragment_registry, ssh_profile
from . import mesh as mesh_mod
from .host_restore import payload_root as _default_payload_root
from .probe import probe_alias


@dataclass
class AliasRefreshResult:
    alias: str
    reachable: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"alias": self.alias, "reachable": self.reachable, "detail": self.detail}


@dataclass
class MeshRefreshResult:
    ok: bool
    machines_yaml: str | None
    aliases: list[AliasRefreshResult] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "machines_yaml": self.machines_yaml,
            "detail": self.detail,
            "aliases": [alias.to_dict() for alias in self.aliases],
        }


def _dtssh_transport_paths(root: Path) -> tuple[Path, Path]:
    script = root / "transports" / "dtssh" / "deploy" / "emit-registry.py"
    module = root / "transports" / "dtssh" / "module.yaml"
    return script, module


def refresh_mesh(
    *,
    machines_yaml: Path | None = None,
    config_d: Path | None = None,
    verify_timeout: int = 8,
    resolve_payload_root: Any = None,
) -> MeshRefreshResult:
    """Reconcile this machine's outbound dtssh reach into the mesh.

    Steps (best-effort, each failure reported rather than raised):

    1. Resolve ``machines.yaml`` (explicit *machines_yaml* or the calling
       repo's own file).
    2. Collect every declared ``dtssh`` alias.
    3. Re-run ``dtssh discover`` + ``dtssh list`` via the transport's
       ``emit-registry`` script to capture the live tunnel ids (never trust a
       cached tunnel id: dtssh tunnel ids rotate).
    4. Re-render this machine's managed SSH ``config.d`` fragment from that
       live registry.
    5. Probe reachability of every known alias with the refreshed profile.
    """
    path = machines_yaml or mesh_mod.find_machines_file()
    if path is None or not path.is_file():
        return MeshRefreshResult(
            ok=True,
            machines_yaml=None,
            detail="no machines.yaml found for this repo",
        )
    mesh = mesh_mod.load_mesh(path)
    aliases = sorted({m.dtssh_alias for m in mesh.machines if m.dtssh_alias})
    if not aliases:
        return MeshRefreshResult(
            ok=True,
            machines_yaml=str(path),
            detail="no dtssh-transport machines declared",
        )

    resolver = resolve_payload_root or _default_payload_root
    try:
        root = resolver()
    except RuntimeError as exc:
        return MeshRefreshResult(ok=False, machines_yaml=str(path), detail=str(exc))
    emit_registry_script, module_path = _dtssh_transport_paths(root)
    if not emit_registry_script.is_file() or not module_path.is_file():
        return MeshRefreshResult(
            ok=False,
            machines_yaml=str(path),
            detail=f"dtssh transport deploy assets are unavailable under {root}",
        )

    with tempfile.TemporaryDirectory(prefix="agent-ssh-mesh-refresh-") as tmp:
        registry_path = Path(tmp) / "dtssh-registry.yaml"
        emitted = subprocess.run(  # noqa: S603 - argv list, no shell
            [
                sys.executable,
                str(emit_registry_script),
                "--machines",
                str(path),
                "--out",
                str(registry_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            **no_window_kwargs(),
        )
        if emitted.returncode != 0:
            detail = (emitted.stderr or emitted.stdout or "emit-registry failed").strip()
            return MeshRefreshResult(ok=False, machines_yaml=str(path), detail=detail)

        try:
            cfg = ssh_profile.load_file(registry_path)
            module = ssh_profile.load_file(module_path)
            ssh_profile.write_fragment(
                cfg,
                module,
                config_d=config_d,
                registry_path=registry_path.resolve(),
                module_path=module_path.resolve(),
            )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return MeshRefreshResult(
                ok=False,
                machines_yaml=str(path),
                detail=f"cannot render managed SSH fragment: {exc}",
            )

    fragment_registry.FragmentRegistry(config_d).refresh()

    results = [
        AliasRefreshResult(alias=alias, reachable=probe_alias(alias, verify_timeout))
        for alias in aliases
    ]
    for result in results:
        result.detail = "reachable" if result.reachable else "unreachable after refresh"
    unreachable = [result.alias for result in results if not result.reachable]
    ok = not unreachable
    detail = (
        f"refreshed the dtssh mesh; all {len(results)} known alias(es) reachable"
        if ok
        else f"refreshed the dtssh mesh; unreachable: {', '.join(unreachable)}"
    )
    return MeshRefreshResult(ok=ok, machines_yaml=str(path), aliases=results, detail=detail)
