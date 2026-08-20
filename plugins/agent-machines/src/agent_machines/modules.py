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

import subprocess
from dataclasses import dataclass, field
from typing import Any

from agent_procutil import no_window_kwargs

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
            if module_applies(module, pkg, machine) and platform_block(module, plat):
                out.append((pkg, module))
    return out


def _tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def run_module(
    pkg: RequirementPackage,
    module: dict[str, Any],
    plat: str,
    dry_run: bool,
    timeout: int = DEFAULT_TIMEOUT,
) -> ModuleResult:
    """Execute one repo-local module (or skip it) and capture the outcome."""
    name = str(module.get("name"))
    repo_root = pkg.repo_root()
    block = platform_block(module, plat)
    if block is None or repo_root is None:
        return ModuleResult(name, pkg.source_repo, ran=False, dry_run=dry_run,
                            skipped_reason=f"no command for platform '{plat}'")

    command = [str(c) for c in block.get("command", [])]
    if dry_run:
        dry_args = block.get("dry_run_args")
        if not dry_args:
            # Never run a mutating module speculatively during a dry-run.
            return ModuleResult(
                name, pkg.source_repo, ran=False, dry_run=True, command=command,
                skipped_reason="module declares no dry_run_args (skipped in dry-run)",
            )
        command = command + [str(a) for a in dry_args]

    try:
        proc = subprocess.run(  # noqa: S603 - argv list, repo-declared trusted module
            command, cwd=str(repo_root), capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            **no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        return ModuleResult(name, pkg.source_repo, ran=False, dry_run=dry_run, command=command,
                            skipped_reason=f"interpreter not found: {exc}")
    except subprocess.TimeoutExpired:
        return ModuleResult(name, pkg.source_repo, ran=True, dry_run=dry_run, command=command,
                            returncode=124, stderr_tail=f"timed out after {timeout}s")
    return ModuleResult(
        name, pkg.source_repo, ran=True, dry_run=dry_run, returncode=proc.returncode,
        command=command, stdout_tail=_tail(proc.stdout), stderr_tail=_tail(proc.stderr),
    )


def run_modules(
    packages: list[RequirementPackage], machine: str, plat: str, dry_run: bool
) -> list[ModuleResult]:
    """Run every applicable module; a failure does not abort the remainder."""
    results: list[ModuleResult] = []
    for pkg, module in resolve_modules(packages, machine, plat):
        results.append(run_module(pkg, module, plat, dry_run))
    return results
