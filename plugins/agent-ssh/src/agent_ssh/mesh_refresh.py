"""Unattended dtssh mesh convergence.

Re-discovers live dtssh tunnel ids, re-emits the managed SSH ``config.d``
fragment from that live state, and verifies reachability to every
``machines.yaml`` dtssh alias.

This closes the automation gap described in copilot-extensions#2684: nothing
previously re-ran ``dtssh discover`` + ``emit-profile`` on a schedule, so a
machine's locally cached tunnel id for a peer could go stale (e.g. after the
peer's dtssh host restarted) and inbound SSH would silently break until an
operator noticed and re-ran the client refresh by hand.

Every declared alias -- including the one for the machine actually running the
refresh -- used to be probed with an outbound ``ssh <alias>`` dial. That meant
a machine would route its own dtssh alias through the Dev Tunnel relay just to
verify reachability to itself, which is both unnecessary and, if that
machine's own relay/tunnel hasn't fully (re)registered yet, a source of
spurious "unreachable" reports (copilot-extensions#5375). Each machine entry's
declared raw hostname -- falling back to its ``machines.yaml`` key when
``hostname`` is omitted, per the shared ``machines.yaml`` contract (see
``agent_bridge.topology.MachineConfig.hostname``: "Empty means ``key`` is the
hostname") -- is now compared against the current machine's own hostname; a
match bypasses the SSH probe entirely rather than dialing out.
"""

from __future__ import annotations

import socket
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


def _normalize_hostname(hostname: str) -> str:
    """Normalize a raw hostname for comparison: trimmed and casefolded.

    Casefolds for case-insensitive comparison only -- it must NOT also strip
    a domain suffix here, or two genuinely distinct FQDNs that happen to
    share a leading label (``host-a.corp.example`` vs ``host-a.lab.example``)
    would compare equal.
    """
    return hostname.strip().casefold()


def default_local_hostname() -> str:
    """Resolve the current machine's own raw hostname.

    Stdlib-only (no dependency on any other plugin's machine-identity
    resolution): ``socket.gethostname()`` returns the short computer name on
    every platform this transport targets, including Windows.
    """
    return socket.gethostname()


def _is_local_machine(declared_hostname: str, local_hostname: str) -> bool:
    """Whether a ``machines.yaml`` entry's declared hostname is this machine.

    Both sides must be non-empty: an entry with no declared ``hostname``
    (and no ``machines.yaml`` key to fall back to) is never assumed to be
    local, and a failure to resolve the local hostname must never make every
    alias match by accident.

    An exact (casefolded) match always counts. A bare short name (no domain
    suffix) on exactly one side is also accepted as a match against the
    leading label of an FQDN on the other side -- that is the common case of
    a short local ``hostname``/``COMPUTERNAME`` compared against a declared
    FQDN, or vice versa. Two FQDNs (a dot on *both* sides) are never equated
    by truncating to their leading label: that would conflate genuinely
    distinct hosts that happen to share a short name in different domains.
    """
    declared = _normalize_hostname(declared_hostname)
    local = _normalize_hostname(local_hostname)
    if not declared or not local:
        return False
    if declared == local:
        return True
    declared_qualified = "." in declared
    local_qualified = "." in local
    if declared_qualified == local_qualified:
        # Either both bare (and already compared unequal above) or both
        # FQDNs -- never conflate two qualified names by their leading label.
        return False
    short, fqdn = (declared, local) if local_qualified else (local, declared)
    return fqdn.split(".", 1)[0] == short


@dataclass
class AliasRefreshResult:
    alias: str
    reachable: bool
    detail: str = ""
    local: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "reachable": self.reachable,
            "detail": self.detail,
            "local": self.local,
        }


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
    local_hostname: str | None = None,
) -> MeshRefreshResult:
    """Reconcile this machine's outbound dtssh reach into the mesh.

    Steps (best-effort, each failure reported rather than raised):

    1. Resolve ``machines.yaml`` (explicit *machines_yaml* or the calling
       repo's own file).
    2. Collect every declared ``dtssh`` alias, along with its machine's
       declared raw ``hostname`` -- or, when omitted, its ``machines.yaml``
       key -- for the self-detection in step 5.
    3. Re-run ``dtssh discover`` + ``dtssh list`` via the transport's
       ``emit-registry`` script to capture the live tunnel ids (never trust a
       cached tunnel id: dtssh tunnel ids rotate).
    4. Re-render this machine's managed SSH ``config.d`` fragment from that
       live registry.
    5. Probe reachability of every known alias with the refreshed profile --
       except an alias whose declared ``hostname`` matches this machine's own
       raw hostname, which is never dialed over SSH (see module docstring).
    """
    path = machines_yaml or mesh_mod.find_machines_file()
    if path is None or not path.is_file():
        return MeshRefreshResult(
            ok=True,
            machines_yaml=None,
            detail="no machines.yaml found for this repo",
        )
    mesh = mesh_mod.load_mesh(path)
    alias_hostnames: dict[str, str] = {}
    for m in mesh.machines:
        if m.dtssh_alias and m.dtssh_alias not in alias_hostnames:
            # ``hostname`` is optional in the shared machines.yaml contract:
            # an empty value means the machine's own key IS its hostname
            # (agent_bridge.topology.MachineConfig.hostname), so fall back to
            # the key rather than leaving self-detection unable to recognize
            # the common key-only declaration.
            alias_hostnames[m.dtssh_alias] = m.hostname or m.key
    aliases = sorted(alias_hostnames)
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

    local_host = local_hostname if local_hostname is not None else default_local_hostname()
    results: list[AliasRefreshResult] = []
    for alias in aliases:
        if _is_local_machine(alias_hostnames.get(alias, ""), local_host):
            results.append(
                AliasRefreshResult(
                    alias=alias,
                    reachable=True,
                    detail="local machine (hostname matches); skipped outbound SSH probe",
                    local=True,
                )
            )
            continue
        results.append(
            AliasRefreshResult(alias=alias, reachable=probe_alias(alias, verify_timeout))
        )
    for result in results:
        if result.local:
            continue
        result.detail = "reachable" if result.reachable else "unreachable after refresh"
    unreachable = [result.alias for result in results if not result.reachable]
    ok = not unreachable
    detail = (
        f"refreshed the dtssh mesh; all {len(results)} known alias(es) reachable"
        if ok
        else f"refreshed the dtssh mesh; unreachable: {', '.join(unreachable)}"
    )
    return MeshRefreshResult(ok=ok, machines_yaml=str(path), aliases=results, detail=detail)
