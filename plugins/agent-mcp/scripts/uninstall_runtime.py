"""Finite legacy MCP uninstall adapter; native init retains the installer lock."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from contextlib import nullcontext
from typing import Any

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
ENTRIES = (
    "versions", ".venv", "snapshots", "current-version", "last-known-good",
    "running-version.json", "deploy-manifest.json", "payload-dir", "stamped-version",
    ".provision-status",
)
RESOLVERS = ("resolve-runtime.ps1", "resolve-runtime.sh")


def _load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Uninstall dependency unavailable: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _ordinary_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            value = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise RuntimeError(f"Uninstall refuses redirected paths: {candidate}")


def check_root(root: Path) -> bool:
    """Validate before native lock creation, staging, or any other mutation."""
    _ordinary_path(root)
    homes = {Path.home(), *(Path(os.environ[key]) for key in ("HOME", "USERPROFILE") if os.environ.get(key))}
    if root == Path(root.anchor) or any(root == home or root in home.parents for home in homes):
        raise RuntimeError("Uninstall refuses a filesystem root, user home, or home ancestor")
    if SCRIPTS.is_relative_to(root):
        raise RuntimeError("Uninstall refuses the source payload or its ancestors")
    if Path(sys.executable).absolute().is_relative_to(root):
        raise RuntimeError("Uninstall requires a Python interpreter outside the runtime")
    if not root.exists():
        return False
    if not root.is_dir():
        raise RuntimeError("Uninstall requires an ordinary install directory")
    for name in (".install.lock", "bin"):
        _ordinary_path(root / name)
    lock = root / ".install.lock"
    if lock.exists() and (not lock.is_file() or lock.stat().st_nlink != 1):
        raise RuntimeError("Uninstall requires an ordinary unshared installer lock")
    for name in ("install.json", "runtime-deployment.json"):
        if os.path.lexists(root / name):
            raise RuntimeError("Legacy uninstall refuses installation-cell or managed-deployment metadata")
    if not any(os.path.lexists(root / name) for name in ENTRIES) and not any(
        os.path.lexists(root / "bin" / name) for name in RESOLVERS
    ):
        return False
    binding = _load("_mcp_uninstall_binding", "command_binding.py")
    primitive = binding._load_primitive(SCRIPTS.parent)
    decision = primitive.probe_legacy_entrypoint(
        payload_root=SCRIPTS.parent, plugin_id="agent-mcp", legacy_root=root,
        project_root=Path.cwd(),
    )
    if decision["allowMutation"] is not True:
        raise RuntimeError(f"Legacy uninstall refused: {decision['probeReason']}")
    manifest = root / "deploy-manifest.json"
    if not os.path.lexists(manifest):
        marker = root / "payload-dir"
        _ordinary_path(marker)
        if not marker.is_file():
            raise RuntimeError("No deployment or stamped MCP ownership; uninstall refused")
        payload = Path(marker.read_text(encoding="utf-8-sig").strip())
        if not payload.is_absolute():
            raise RuntimeError("Stamped MCP ownership must be absolute; uninstall refused")
        _ordinary_path(payload)
        payload = payload.resolve(strict=True)
        if not payload.is_relative_to(root / "snapshots"):
            raise RuntimeError("Stamped MCP payload is outside its snapshots; uninstall refused")
        project = payload / "pyproject.toml"
        _ordinary_path(project)
        section = re.search(
            r"(?ms)^\[project\][ \t]*$\s*(.*?)(?=^\[|\Z)",
            project.read_text(encoding="utf-8-sig"),
        )
        if section is None or re.search(
            r'''(?m)^name[ \t]*=[ \t]*(["'])agent-mcp\1[ \t]*(?:\#.*)?$''',
            section[1],
        ) is None:
            raise RuntimeError("Stamped payload belongs to another product; uninstall refused")
    try:
        binding.publish_legacy_binding(
            primitive, payload_root=SCRIPTS.parent, plugin_id="agent-mcp",
            legacy_root=root, project_root=Path.cwd(),
        )
    except binding.SourceProvenanceUnavailable:
        if manifest.exists() and primitive.read_json(manifest).get("commandLifecycle") is not None:
            raise RuntimeError("Cannot revalidate incumbent lifecycle ownership; uninstall refused")
    return True


def _run(command: list[str]) -> str:
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=60, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(f"Uninstall native helper failed: {result.stderr.strip()}")
    return result.stdout


def refuse_active(root: Path, shell: str, runtime: Any) -> None:
    """Refuse ambiguous inspection, never terminate a process or an upstream."""
    if os.name == "nt":
        script = r"""
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rows = @(Get-CimInstance Win32_Process | ForEach-Object {
    if ($_.Name -match '^(python|pythonw|pypy|pypy3)([0-9.]*)?\.exe$') {
        $candidate = $_
        try { $owner = Invoke-CimMethod -InputObject $candidate -MethodName GetOwnerSid } catch {
            if (-not (Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue)) { return }
            throw
        }
        if ($owner.ReturnValue -ne 0) { throw 'Cannot establish Python process ownership' }
        if ($owner.Sid -eq $sid) {
            if (-not $_.ExecutablePath -or -not $_.CommandLine) {
                throw 'Cannot inspect a current-user Python process'
            }
            [pscustomobject]@{ pid=$_.ProcessId; image=$_.ExecutablePath; command=$_.CommandLine }
        }
    } elseif ($_.ExecutablePath) {
        [pscustomobject]@{ pid=$_.ProcessId; image=$_.ExecutablePath; command='' }
    }
})
ConvertTo-Json -InputObject $rows -Compress
"""
        rows = json.loads(_run([shell, "-NoProfile", "-NonInteractive", "-Command", script]))
        if not any(row["pid"] == os.getpid() for row in rows):
            raise RuntimeError("Native process inventory did not include the inspector; uninstall refused")
    elif sys.platform.startswith("linux") and Path("/proc").is_dir():
        rows = []
        for pid in runtime._iter_all_pids():
            directory = Path("/proc") / str(pid)
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                command = (directory / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", errors="surrogateescape",
                )
                image = runtime._pid_image_path(pid) or ""
                rows.append({"pid": pid, "image": image, "command": command})
            except FileNotFoundError:
                continue
            except PermissionError as error:
                raise RuntimeError("Cannot inspect a current-user process; uninstall refused") from error
        if not rows:
            raise RuntimeError("Cannot enumerate current-user processes; uninstall refused")
    else:
        raise RuntimeError("Uninstall process inspection is supported only on Windows and Linux /proc")
    needle = str(root).replace("\\", "/").casefold()
    ambiguous = []
    for row in rows:
        if row["pid"] == os.getpid():
            continue
        image = row["image"].replace("\\", "/").casefold()
        command = row["command"].replace("\\", "/").casefold()
        # A base interpreter importing MCP without an attributable runtime path
        # cannot prove which installation supplies its code.
        ambiguous_mcp = "agent_mcp" in command and not image.startswith(needle + "/")
        if image.startswith(needle + "/") or needle + "/" in command:
            raise RuntimeError(f"Active MCP runtime process {row['pid']}; uninstall refused")
        if ambiguous_mcp:
            ambiguous.append(row["pid"])
    if ambiguous:
        raise RuntimeError(f"Unattributed MCP runtime process {ambiguous[0]}; uninstall refused")
    if runtime._versions_with_live_process(root):
        raise RuntimeError("Active runtime generation; uninstall refused")


def _engine() -> Path:
    local = SCRIPTS / ("installer-engine.ps1" if os.name == "nt" else "installer-engine.sh")
    if local.is_file():
        return local
    source = SCRIPTS.parents[2] / "libs" / "installer-engine" / local.name
    if source.is_file():
        return source
    raise RuntimeError("Shared installer-engine removal helper is unavailable")


def _remove(shell: str, root: Path, names: tuple[str, ...]) -> None:
    if not names:
        return
    engine = _engine()
    if os.name == "nt":
        def quote(value: object) -> str:
            return "'" + str(value).replace("'", "''") + "'"
        command = (
            f"$ErrorActionPreference='Stop'; . {quote(engine)}; "
            f"Remove-InstallRuntimeContent -InstallRoot {quote(root)} "
            f"-EntryNames @({','.join(quote(name) for name in names)})"
        )
        _run([shell, "-NoProfile", "-NonInteractive", "-Command", command])
    else:
        _run([shell, "-c", '. "$1"; shift; remove_install_runtime_content "$@"',
              "mcp-uninstall", str(engine), str(root), *names])


def uninstall(root: Path, shell: str) -> None:
    if not check_root(root):
        return
    runtime = _load("_mcp_uninstall_runtime", "versioned_runtime.py")
    leases = _load("_mcp_uninstall_leases", "versioned_runtime_leases.py")
    for name in ("versions", "snapshots"):
        directory = root / name
        _ordinary_path(directory)
        if directory.exists():
            if not directory.is_dir():
                raise RuntimeError(f"Unrecognized {name} inventory; uninstall refused")
            for entry in directory.iterdir():
                _ordinary_path(entry)
                if not entry.is_dir() or (
                    entry.name.casefold() != "dev"
                    and not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", entry.name)
                ):
                    raise RuntimeError(f"Unrecognized {name} entry; uninstall refused")
    versions = runtime.list_versions(root)
    if any(version.casefold() == "dev" for version in versions):
        raise RuntimeError("Mutable dev generation requires explicit owner retirement; uninstall refused")
    # Keep lease inodes, configuration, materialized fleets, auth and unknown
    # children. Native init owns .install.lock for this entire subprocess.
    guard = leases.batch_removal_custody(root, versions) if versions else nullcontext()
    with guard:
        refuse_active(root, shell, runtime)
        local_bin = Path.home() / ".local" / "bin"
        commands: tuple[str, ...] = ()
        if root == Path.home() / ".agent-mcp" and local_bin.exists():  # marketplace-isolation: allow legacy teardown
            _ordinary_path(local_bin)
            selected = []
            for name in ("agent-mcp", "agent-mcp.cmd", "agent-mcp.ps1"):
                path = local_bin / name
                if not os.path.lexists(path):
                    continue
                _ordinary_path(path)
                content = path.read_text(encoding="utf-8")
                cmd = (
                    name == "agent-mcp.cmd" and content.startswith("@echo off\nsetlocal\n")
                    and 'set "_ROOT=%USERPROFILE%\\.agent-mcp"' in content
                    and "call :_resolve" in content and "-m agent_mcp" in content
                )
                sh = (
                    name == "agent-mcp" and content.startswith("#!/usr/bin/env bash\n")
                    and (
                        '_root="$HOME/.agent-mcp"' in content
                        or ('_name="agent-mcp"' in content and '_root="$HOME/.$_name"' in content)
                    )
                    and "resolve-runtime.sh" in content and "-m agent_mcp" in content
                )
                if cmd or sh:
                    selected.append(name)
            commands = tuple(selected)
        _engine()
        refuse_active(root, shell, runtime)
        _remove(shell, root, ENTRIES)
        if (root / "bin").exists():
            _remove(shell, root / "bin", RESOLVERS)
        if commands:
            _remove(shell, local_bin, commands)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "remove"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--shell", required=True)
    arguments = parser.parse_args()
    root = Path(os.path.abspath(arguments.root))
    try:
        if arguments.action == "check":
            print("ready" if check_root(root) else "absent")
        else:
            uninstall(root, arguments.shell)
            print("Legacy MCP runtime removed; durable and unknown contents preserved.")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"MCP uninstall refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
