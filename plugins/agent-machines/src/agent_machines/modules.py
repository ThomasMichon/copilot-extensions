"""Repo-local module execution.

The generic engine converges *Copilot settings* directly (the ``surfaces``), but
the sensitive, OS-mutating work -- install a package manager, bootstrap WSL,
configure SSH, change power settings -- must never live in this public plugin.
Instead a requirement package declares **modules**: repo-local commands the
engine invokes on the machines they gate to. The engine is a generic module
*runner*; the actual mutation logic stays in the harness repo (e.g. a multi-machine system's
``tools/restore`` sections), so "engine public, modules repo-local" holds.

A module is declared per package::

    modules:
      - name: ssh
        gate: [box-1]                 # optional; defaults to the package gate
        elevated: false               # informational
        windows:
          command: ["pwsh", "-File", "tools/restore/Restore-MachineState.ps1", "-Section", "SSH"]
          dry_run_args: ["-DryRun"]
        linux:
          command: ["bash", "tools/restore/restore-machine-state.sh", "--section", "ssh"]
          dry_run_args: ["--dry-run"]

Commands are argv lists (never shell strings), run with the package's repo root
as the working directory so relative script paths resolve. **Dry-run safety:** a
module runs during a dry-run only if it declares ``dry_run_args`` (proving it
supports preview); otherwise it is skipped, never executed speculatively.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from .authority import AUTHORITY_MODE_OPAQUE_ADDITIVE, effective_authority
from .manifest import RequirementPackage

#: Per-platform block fallbacks: a wsl module may reuse the linux block.
_PLATFORM_FALLBACK = {"wsl": ("wsl", "linux"), "linux": ("linux",), "windows": ("windows",)}

#: Cap a module's runtime so a hung installer cannot wedge a restore.
DEFAULT_TIMEOUT = 1800


@dataclass
class ModuleResult:
    name: str
    source_repo: str
    ran: bool
    dry_run: bool
    returncode: int | None = None
    command: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    package: str = ""
    authority: int = 0
    authority_mode: str = AUTHORITY_MODE_OPAQUE_ADDITIVE

    @property
    def ok(self) -> bool:
        return self.skipped_reason is not None or self.returncode == 0


def platform_block(module: dict[str, Any], plat: str) -> dict[str, Any] | None:
    """Return the command block for ``plat`` (with wsl->linux fallback)."""
    for key in _PLATFORM_FALLBACK.get(plat, (plat,)):
        block = module.get(key)
        if isinstance(block, dict) and block.get("command"):
            return block
    return None


def invocation_block(module: dict[str, Any], plat: str) -> dict[str, Any] | None:
    """Return a payload-attributable invocation supported on ``plat``."""
    invocation = module.get("invocation")
    if not isinstance(invocation, dict):
        return None
    platforms = invocation.get("platforms")
    if platforms and plat not in platforms:
        return None
    if not invocation.get("plugin") or not invocation.get("command"):
        return None
    return invocation


def module_applies(module: dict[str, Any], pkg: RequirementPackage, machine: str) -> bool:
    """A module applies when its own gate (or the package gate) includes machine."""
    gate = module.get("gate")
    if gate:
        return machine in gate or "*" in gate
    return pkg.applies_to(machine)


def resolve_modules(
    packages: list[RequirementPackage], machine: str, plat: str
) -> list[tuple[RequirementPackage, dict[str, Any]]]:
    """Gather (package, module) pairs applicable to ``machine`` on ``plat``."""
    out: list[tuple[RequirementPackage, dict[str, Any]]] = []
    for pkg in packages:
        for module in pkg.modules:
            if module_applies(module, pkg, machine) and (
                platform_block(module, plat) or invocation_block(module, plat)
            ):
                out.append((pkg, module))
    return sorted(
        out,
        key=lambda item: (
            item[0].source_repo,
            item[0].name,
            str(item[1].get("name")),
        ),
    )


def _tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def _active_plugins():
    from plugin_activation import resolve_active_plugins

    return resolve_active_plugins().active


def _payload_invocation_command(
    invocation: dict[str, Any],
    plat: str,
) -> tuple[list[str], dict[str, str]]:
    source = str(invocation["plugin"])
    command_name = str(invocation["command"])
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", command_name) is None:
        raise RuntimeError(f"invalid payload command id: {command_name!r}")
    active = _active_plugins().get(source)
    if active is None:
        raise RuntimeError(f"required active plugin is unavailable: {source}")
    payload = active.root.resolve()
    descriptor_path = payload / "payload-invocation.json"
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"cannot read payload invocation for {source}: {exc}"
        ) from exc
    if not isinstance(descriptor, dict) or descriptor.get("version") not in (1, 2):
        raise RuntimeError(
            f"plugin {source} has an invalid payload invocation descriptor"
        )
    raw_commands = descriptor.get("commands")
    if raw_commands is None:
        declared = [descriptor]
    elif isinstance(raw_commands, list):
        declared = [item for item in raw_commands if isinstance(item, dict)]
    else:
        declared = []
    if not any(item.get("command") == command_name for item in declared):
        raise RuntimeError(
            f"plugin {source} does not declare payload command {command_name!r}"
        )
    output_dir = descriptor.get("outputDir", "bin")
    output_path = Path(str(output_dir))
    if (
        not isinstance(output_dir, str)
        or not output_dir
        or output_path.is_absolute()
        or re.match(r"^[A-Za-z]:", output_dir) is not None
        or ".." in output_path.parts
    ):
        raise RuntimeError(f"plugin {source} has an invalid payload outputDir")
    if plat == "windows":
        windows_shim = descriptor.get("windowsCatalogShim", "powershell")
        if windows_shim == "cmd":
            shim = payload / output_path / f"{command_name}.cmd"
            host = os.environ.get("COMSPEC") or shutil.which("cmd")
            if host is None:
                raise RuntimeError("cmd.exe is required for a Windows payload command")
            command = [host, "/d", "/c", str(shim)]
        elif windows_shim == "powershell":
            shim = payload / output_path / f"{command_name}.ps1"
            host = shutil.which("pwsh") or shutil.which("powershell")
            if host is None:
                raise RuntimeError(
                    "PowerShell is required for a Windows payload command"
                )
            command = [
                host,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(shim),
            ]
        else:
            raise RuntimeError(
                f"plugin {source} has an invalid windowsCatalogShim"
            )
    else:
        shim = payload / output_path / command_name
        command = [str(shim)]
    try:
        shim.resolve().relative_to(payload)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"payload command escapes plugin root: {shim}") from exc
    if not shim.is_file():
        raise RuntimeError(f"payload command shim is unavailable: {shim}")
    env = dict(os.environ)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    return command, env


def run_module(
    pkg: RequirementPackage,
    module: dict[str, Any],
    plat: str,
    dry_run: bool,
    timeout: int = DEFAULT_TIMEOUT,
) -> ModuleResult:
    """Execute one repo-local module (or skip it) and capture the outcome."""
    name = str(module.get("name"))
    authority = effective_authority(pkg, module)
    metadata = {
        "package": pkg.name,
        "authority": authority,
        "authority_mode": AUTHORITY_MODE_OPAQUE_ADDITIVE,
    }
    repo_root = pkg.repo_root()
    block = platform_block(module, plat)
    invocation = invocation_block(module, plat)
    if block is None and invocation is None:
        return ModuleResult(name, pkg.source_repo, ran=False, dry_run=dry_run,
                            skipped_reason=f"no command for platform '{plat}'",
                            **metadata)
    if repo_root is None:
        return ModuleResult(name, pkg.source_repo, ran=False, dry_run=dry_run,
                            skipped_reason="could not derive repo root from package path",
                            **metadata)

    env = None
    if invocation is not None:
        try:
            command, env = _payload_invocation_command(invocation, plat)
        except RuntimeError as exc:
            return ModuleResult(
                name,
                pkg.source_repo,
                ran=True,
                dry_run=dry_run,
                returncode=127,
                stderr_tail=str(exc),
                **metadata,
            )
        command.extend(str(arg) for arg in invocation.get("arguments", []))
        mode_args = (
            invocation.get("dry_run_arguments")
            if dry_run
            else invocation.get("apply_arguments", [])
        )
        if dry_run and not mode_args:
            return ModuleResult(
                name,
                pkg.source_repo,
                ran=False,
                dry_run=True,
                command=command,
                skipped_reason=(
                    "payload invocation declares no dry_run_arguments "
                    "(skipped in dry-run)"
                ),
                **metadata,
            )
        command.extend(str(arg) for arg in (mode_args or []))
    else:
        assert block is not None
        command = [str(c) for c in block.get("command", [])]
        dry_args = block.get("dry_run_args")
        if not dry_run:
            dry_args = None
        if dry_run and not dry_args:
            return ModuleResult(
                name, pkg.source_repo, ran=False, dry_run=True, command=command,
                skipped_reason="module declares no dry_run_args (skipped in dry-run)",
                **metadata,
            )
        if dry_args:
            command = command + [str(a) for a in dry_args]

    try:
        proc = subprocess.run(  # noqa: S603 - argv list, repo-declared trusted module
            command, cwd=str(repo_root), capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=env,
            **no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        return ModuleResult(name, pkg.source_repo, ran=False, dry_run=dry_run, command=command,
                            skipped_reason=f"interpreter not found: {exc}", **metadata)
    except subprocess.TimeoutExpired:
        return ModuleResult(name, pkg.source_repo, ran=True, dry_run=dry_run, command=command,
                            returncode=124, stderr_tail=f"timed out after {timeout}s",
                            **metadata)
    return ModuleResult(
        name, pkg.source_repo, ran=True, dry_run=dry_run, returncode=proc.returncode,
        command=command, stdout_tail=_tail(proc.stdout), stderr_tail=_tail(proc.stderr),
        **metadata,
    )


def run_modules(
    packages: list[RequirementPackage], machine: str, plat: str, dry_run: bool
) -> list[ModuleResult]:
    """Run every applicable module; a failure does not abort the remainder."""
    results: list[ModuleResult] = []
    for pkg, module in resolve_modules(packages, machine, plat):
        results.append(run_module(pkg, module, plat, dry_run))
    return results
