"""Finite runtime removal under the payload owner's existing lifecycle/custody.

Native callers own their whole-install transaction and service STOP/registration.
This module never stops a service/process, provisions code, or deletes a data root.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

sys.dont_write_bytecode = True
SELECTORS = ("current-version", "last-known-good", "payload-dir", "payload-origin",
             "stamped-version", "running-version.json", "deploy-manifest.json")
BASE = (".venv", "venv", "versions", "snapshots", *SELECTORS)
RESOLVERS = ("resolve-runtime.ps1", "resolve-runtime.sh")
RUNTIME_MODULES = tuple(f"versioned_runtime{suffix}.py" for suffix in (
    "", "_leases", "_construction", "_source", "_publication",
))
CONTEXT = ("installation-context.ps1", "installation-context.sh",
           "guest-environment.ps1", "guest-environment.sh", "json-query.awk")
RESERVED = frozenset((
    ".install.lock", ".install.lock.pid", ".install.lock.pid.reap", ".install-stage",
    ".construction-leases", ".retention-leases", ".publication-leases",
    "config", "config.json", "config.yaml", "data", "auth", "sessions", "logs",
    "run", "core", "cache", "models", "model-cache", "supervisors",
    "service.env", "supervisor.env", "active.json", "ssh", "providers.d",
    "install.json", "runtime-deployment.json", "namespace.json",
))


class UninstallRefused(RuntimeError):
    """Ownership, custody, process inspection or finite cleanup is unverified."""


@dataclass(frozen=True)
class Profile:
    """Closed payload-owned declarations; directory contents are never inferred."""

    root_entries: tuple[str, ...]
    bin_entries: tuple[str, ...]
    script_entries: tuple[str, ...]
    local_bin_entries: tuple[str, ...]
    module: str
    guard_module: str | None = None
    guard_function: str = "check_under_custody"
    nested_engine: bool = False


@dataclass(frozen=True)
class Target:
    root: Path
    versions: tuple[str, ...]
    snapshots: tuple[str, ...]


def _leaf(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+", value)
            or value in {".", ".."} or value.endswith(".") or value in RESERVED
            or value.endswith((".db", ".sqlite", ".sqlite3", ".pid", ".log", ".env"))):
        raise UninstallRefused(f"Not a finite reconstructible entry: {value!r}")
    return value


def _helper_entry(value: str) -> str:
    if not isinstance(value, str):
        raise UninstallRefused(f"Not a finite reconstructible helper: {value!r}")
    return "/".join(_leaf(part) for part in value.split("/"))


def _plain(path: Path, *, file: bool = False) -> None:
    for candidate in (path, *path.parents):
        try:
            value = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise UninstallRefused(f"Redirected uninstall path: {candidate}")
    if file and path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise UninstallRefused(f"Uninstall requires an ordinary unshared file: {path}")


def _load(path: Path) -> ModuleType:
    _plain(path, file=True)
    name = "_finite_uninstall_" + hashlib.sha256(str(path).encode()).hexdigest()
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise UninstallRefused(f"Required payload helper unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _dependency(payload: Path, filename: str) -> Path:
    local = payload / "scripts" / filename
    if local.is_file():
        _plain(local, file=True)
        return local
    canonical = Path(os.path.abspath(__file__)).parent
    if canonical == payload / "scripts" and payload.parent.name == "plugins":
        source = payload.parent.parent / "libs" / "installer-engine"
        owner = source / Path(__file__).name
        _plain(owner, file=True)
        if owner.is_file() and owner.read_bytes() == Path(__file__).read_bytes():
            canonical = source
    if (canonical.name == "installer-engine" and canonical.parent.name == "libs"
            and payload.parent == canonical.parent.parent / "plugins"):
        path = canonical / filename
        _plain(path, file=True)
        if path.is_file():
            return path
    raise UninstallRefused(f"Packaged uninstall dependency missing: {filename}")


def profile_for(payload: Path, plugin: str, path: Path | None = None) -> Profile:
    """Read only the declared ordinary profile in this exact trusted payload."""
    if path is not None:
        expected = payload / "scripts" / "uninstall-profile.json"
        if path.absolute() != expected:
            raise UninstallRefused("The profile must be this payload's scripts/uninstall-profile.json")
        _plain(path, file=True)
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        required = {"version", "root_entries", "bin_entries", "script_entries",
                    "local_bin_entries", "module"}
        if (not isinstance(record, dict) or not required <= record.keys()
                or record.keys() - required - {"guard_module", "guard_function", "nested_engine"}
                or type(record["version"]) is not int or record["version"] != 1):
            raise UninstallRefused("Invalid finite uninstall profile")
        arrays = {}
        for key in ("root_entries", "bin_entries", "script_entries", "local_bin_entries"):
            if not isinstance(record[key], list):
                raise UninstallRefused(f"Profile {key} must be a finite list")
            validate = _helper_entry if key in {"bin_entries", "script_entries"} else _leaf
            arrays[key] = tuple(validate(value) for value in record[key])
            if len(set(arrays[key])) != len(arrays[key]):
                raise UninstallRefused("Duplicate finite uninstall entry")
        profile = Profile(**arrays, **{key: record[key] for key in (
            "module", "guard_module", "guard_function", "nested_engine",
        ) if key in record})
    else:
        extras = {
            "agent-bridge": ("start-agent-bridge.ps1",),
            "agent-dispatch": ("serve-service.ps1", "supervise-service.ps1", "supervise-service.sh"),
            "agent-vault": ("service.ps1",),
            "budget-guidance": (),
            "agent-index": ("service.ps1", "service.sh"),
            "agent-mcp": (".provision-status",),
            "agent-logger": (),
        }
        if plugin not in extras:
            raise UninstallRefused("This plugin requires an explicit trusted uninstall profile")
        helpers = RESOLVERS
        if plugin == "agent-dispatch":
            helpers += ("runtime-gate.ps1", "runtime-gate.sh",
                        "delegated-waiter-callback.ps1", "delegated-waiter-callback.sh")
        if plugin == "budget-guidance":
            helpers += ("bootstrap-check.ps1", "bootstrap-check.sh",
                        "bootstrap-killswitch-guard.ps1", "bootstrap-killswitch-guard.sh",
                        "emit-mesh-pointer.ps1", "emit-mesh-pointer.sh")
        commands = (plugin, plugin + ".ps1", plugin + ".cmd")
        if plugin == "agent-dispatch":
            commands += ("agent-dispatch-board", "agent-dispatch-board.ps1", "agent-dispatch-board.cmd")
        if plugin == "agent-vault":
            commands += ("vault-askpass",)
        if plugin == "agent-logger":
            commands += tuple(name + suffix for name in (
                "session-sync", "collate-session", "read-session-digest",
                "prepare-session-log", "ramp-up-session",
            ) for suffix in ("", ".ps1", ".cmd"))
        profile = Profile(BASE + extras[plugin], helpers,
                          RUNTIME_MODULES if plugin == "agent-bridge" else (),
                          commands, plugin.replace("-", "_"), nested_engine=plugin == "agent-index")
    if (not isinstance(profile.module, str)
            or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", profile.module)
            or type(profile.nested_engine) is not bool
            or (profile.nested_engine and plugin != "agent-index")):
        raise UninstallRefused("Invalid module identity or non-Index nested runtime")
    if profile.guard_module is not None:
        _leaf(profile.guard_module)
        if not profile.guard_module.endswith(".py"):
            raise UninstallRefused("A policy guard must be a trusted Python source file")
    if (not isinstance(profile.guard_function, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", profile.guard_function)):
        raise UninstallRefused("Invalid finite policy guard function")
    return profile


def _root(payload: Path, root: Path) -> None:
    if not payload.is_absolute() or ".." in payload.parts or not root.is_absolute() or ".." in root.parts:
        raise UninstallRefused("Uninstall requires an absolute root without parent traversal")
    _plain(payload)
    _plain(root)
    homes = {Path.home().absolute(), *(Path(os.environ[key]).absolute() for key in
              ("HOME", "USERPROFILE") if os.environ.get(key))}
    if root == Path(root.anchor) or any(root == home or root in home.parents for home in homes):
        raise UninstallRefused("Uninstall refuses filesystem roots and home ancestors")
    stage = root / ".install-stage"
    for candidate in (payload, Path(sys.executable).absolute(), Path(sys.prefix),
                      Path(sys.base_prefix), Path(__file__).absolute()):
        for value in (candidate, candidate.resolve()):
            if value.is_relative_to(root) and not value.is_relative_to(stage):
                raise UninstallRefused("Uninstall code/interpreter must be outside removable runtime content")
    if root.exists() and not root.is_dir():
        raise UninstallRefused("Uninstall requires an ordinary runtime directory")
    for name in ("install.json", "runtime-deployment.json"):
        if os.path.lexists(root / name):
            raise UninstallRefused("Legacy uninstall refuses installation-cell or managed-deployment ownership")
    _plain(root / ".install.lock", file=True)


def _project_name(snapshot: Path, plugin: str) -> None:
    project = snapshot / "pyproject.toml"
    _plain(project, file=True)
    section = re.search(r"(?ms)^\[project\][ \t]*$\s*(.*?)(?=^\[|\Z)",
                        project.read_text(encoding="utf-8-sig"))
    if section is None or re.search(
        r'''(?m)^name[ \t]*=[ \t]*(["'])''' + re.escape(plugin) + r'''\1[ \t]*(?:\#.*)?$''',
        section[1],
    ) is None:
        raise UninstallRefused("Stamped ownership belongs to another product")


def _authority_payload(payload: Path) -> Path:
    """Keep staged source identity at its origin; custody validates the ticket."""
    source = os.environ.get("COPILOT_PLUGIN_STAGED_FROM")
    if not source or not os.environ.get("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"):
        return payload
    authority = Path(source)
    if not authority.is_absolute() or ".." in authority.parts:
        raise UninstallRefused("The staged lifecycle source must be an absolute ordinary payload")
    _plain(authority)
    return authority


def _ownership(payload: Path, plugin: str, root: Path) -> tuple[Any, Any, Any, dict[str, Any]]:
    manifest = root / "deploy-manifest.json"
    _plain(manifest, file=True)
    record: dict[str, Any] = {}
    if manifest.exists():
        record = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if (not isinstance(record, dict) or record.get("schema_version") != 3
                or record.get("service") != plugin):
            raise UninstallRefused("Foreign or invalid deployment manifest")
        source = record.get("source", {})
        if not isinstance(source, dict) or source.get("plugin", plugin) != plugin:
            raise UninstallRefused("Foreign deployment source")
    else:
        marker = root / "payload-dir"
        _plain(marker, file=True)
        snapshot = Path(marker.read_text(encoding="utf-8-sig").strip())
        if (not snapshot.is_absolute() or ".." in snapshot.parts
                or snapshot.parent != root / "snapshots" or not _generation(snapshot.name)):
            raise UninstallRefused("Stamped ownership is outside this root's snapshots")
        _plain(snapshot)
        _project_name(snapshot, plugin)
        record = {"schema_version": 3, "service": plugin,
                  "source": {"plugin": plugin, "kind": "local", "path": str(payload)}}
    binding = _load(payload / "scripts" / "command_binding.py")
    primitive = binding._load_primitive(payload)  # noqa: SLF001 - payload-owned binding API
    authority = _authority_payload(payload)
    arguments = dict(payload_root=authority, plugin_id=plugin, legacy_root=root,
                     project_root=Path(os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT") or Path.cwd()),
                     environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(authority)})
    decision = primitive.probe_legacy_entrypoint(**{
        key: arguments[key] for key in ("payload_root", "plugin_id", "legacy_root", "project_root")
    })
    if decision["allowMutation"] is not True:
        raise UninstallRefused(f"Typed installation ownership refused: {decision['probeReason']}")
    receipt = None
    try:
        receipt = binding.publish_legacy_binding(primitive, **arguments)
    except binding.SourceProvenanceUnavailable:
        if record.get("commandBinding") is not None or record.get("commandLifecycle") is not None:
            raise UninstallRefused("Existing typed/lifecycle ownership cannot be revalidated") from None
        if record.get("source", {}).get("plugin") != plugin:
            raise UninstallRefused("Untyped fallback lacks product ownership") from None
    lifecycle = record.get("commandLifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict) or lifecycle.get("state") != "complete":
            raise UninstallRefused("Incomplete command lifecycle cannot authorize runtime removal")
    return binding, primitive, receipt, record


def _generation(name: str) -> bool:
    return bool(re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", name)) and not name.endswith(".")


def _inventory(root: Path, runtime: Any) -> Target:
    versions = root / "versions"
    snapshots = root / "snapshots"
    for directory in (versions, snapshots):
        _plain(directory)
        if directory.exists() and not directory.is_dir():
            raise UninstallRefused("Invalid runtime inventory directory")
    names = runtime.list_versions(root)
    if "dev" in (name.lower() for name in names) or os.path.lexists(root / "dev-claim.json"):
        raise UninstallRefused("Mutable dev ownership requires explicit retirement")
    selected = tuple(name for name in names if _generation(name))
    stamps = tuple(sorted(path.name for path in snapshots.iterdir() if _generation(path.name)
                          and path.is_dir())) if snapshots.exists() else ()
    for directory, generations in ((versions, selected), (snapshots, stamps)):
        for name in generations:
            _plain(directory / name)
    return Target(root, selected, stamps)


def _guard(payload: Path, plugin: str, root: Path, profile: Profile) -> None:
    if profile.guard_module is not None:
        module = _load(payload / "scripts" / profile.guard_module)
        callback = getattr(module, profile.guard_function)
        if callback(root=root, payload_root=payload, plugin_id=plugin) is not None:
            raise UninstallRefused("A read-only policy guard must return None or raise")


def _targets(payload: Path, plugin: str, root: Path, profile: Profile, runtime: Any) -> tuple[Target, ...]:
    targets = [_inventory(root, runtime)]
    if profile.nested_engine:
        engine = root / "engine"
        override = os.environ.get("AGENT_INDEX_ENGINE_HOME")
        if override and Path(os.path.abspath(override)) != engine:
            raise UninstallRefused("An external Index engine requires separate authenticated teardown")
        _plain(engine)
        if engine.exists():
            _root(payload, engine)
            manifest = engine / "deploy-manifest.json"
            _plain(manifest, file=True)
            if manifest.exists():
                record = json.loads(manifest.read_text(encoding="utf-8-sig"))
                if (not isinstance(record, dict) or record.get("schema_version") != 3
                        or record.get("service") != "agent-index-engine"):
                    raise UninstallRefused("Foreign or invalid nested Index engine ownership")
                source = record.get("source", {})
                if (not isinstance(source, dict)
                        or source.get("plugin", "agent-index-engine")
                        not in {"agent-index", "agent-index-engine"}):
                    raise UninstallRefused("Foreign nested Index engine source")
                lifecycle = record.get("commandLifecycle")
                if lifecycle is not None and (
                    not isinstance(lifecycle, dict) or lifecycle.get("state") != "complete"
                ):
                    raise UninstallRefused("Incomplete nested Index engine lifecycle ownership")
            targets.append(_inventory(engine, runtime))
    return tuple(targets)


def _helper_names(directory: Path, names: tuple[str, ...]) -> tuple[str, ...]:
    _plain(directory)
    if directory.exists() and not directory.is_dir():
        raise UninstallRefused("Generated helper directory is not ordinary")
    for name in names:
        _plain(directory / name, file=True)
    return tuple(name for name in names if (directory / name).exists())


def _index_launchers(payload: Path, stamped_payload: Path | None = None) -> dict[str, tuple[str, ...]]:
    repair = _load(payload / "scripts" / "stamp_repair.py")
    script = payload / "scripts" / "install.ps1"
    _plain(script, file=True)
    source = script.read_text(encoding="utf-8-sig")
    installed = {}
    for variable, name in (("ps1Content", "agent-index.ps1"), ("cmdContent", "agent-index.cmd")):
        matches = re.findall(r"(?m)^    \$" + variable + r" = @'\n(.*?)\n'@$", source, re.S)
        if len(matches) != 1:
            raise UninstallRefused("Canonical Index launcher template is unavailable or ambiguous")
        installed[name] = matches[0]
    gate_root = stamped_payload or payload
    gate_ps = str(gate_root / "scripts" / "runtime-gate.ps1").replace("'", "''")
    gate_sh = shlex.quote(str(gate_root / "scripts" / "runtime-gate.sh"))
    return {
        "agent-index": (repair.installed_launcher().decode().rstrip("\n"),
                        f'#!/usr/bin/env bash\nexec bash {gate_sh} "$@"'),
        "agent-index.ps1": (installed["agent-index.ps1"],
                            f"& '{gate_ps}' @args\nexit $LASTEXITCODE"),
        "agent-index.cmd": (installed["agent-index.cmd"], (
            '@echo off\nwhere pwsh >nul 2>&1\n'
            'if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File '
            '"%~dp0agent-index.ps1" %*) else (powershell -NoProfile -ExecutionPolicy '
            'Bypass -File "%~dp0agent-index.ps1" %*)\nexit /b %ERRORLEVEL%'
        )),
    }


def _local_bindings(payload: Path, root: Path, profile: Profile) -> tuple[Path, tuple[str, ...]]:
    directory = Path.home() / ".local" / "bin"
    _plain(directory)
    contents = {}
    for name in profile.local_bin_entries:
        path = directory / name
        if os.path.lexists(path):
            _plain(path, file=True)
            contents[name] = path.read_text(encoding="utf-8-sig")
    if not contents:
        return directory, ()
    if profile.nested_engine:
        if root != Path.home() / ".agent-index":
            return directory, ()
        templates = _index_launchers(payload)
        marker = root / "payload-dir"
        _plain(marker, file=True)
        if marker.is_file():
            stamp = Path(marker.read_text(encoding="utf-8-sig").strip())
            if not stamp.is_absolute() or ".." in stamp.parts:
                raise UninstallRefused("Index stamped command payload is not an absolute owned path")
            _project_name(stamp, "agent-index")
            stamped = _index_launchers(payload, stamp)
            templates = {name: values + stamped[name] for name, values in templates.items()}
        selected = [name for name, text in contents.items()
                    if text.replace("\r\n", "\n").rstrip("\n") in templates.get(name, ())]
        if "agent-index.ps1" not in selected and "agent-index.cmd" in selected:
            selected.remove("agent-index.cmd")
        return directory, tuple(selected)
    commands = _load(_dependency(payload, "installer_engine_commands.py"))
    publisher = None
    if profile.module in {"agent_bridge", "agent_dispatch", "agent_containers"}:
        manifest = root / "deploy-manifest.json"
        _plain(manifest, file=True)
        if manifest.is_file():
            record = json.loads(manifest.read_text(encoding="utf-8-sig"))
            source = record.get("source", {})
            path = source.get("path") if isinstance(source, dict) else None
            if path is not None:
                if not isinstance(path, str) or not path:
                    raise UninstallRefused("Invalid installed command publisher")
                publisher = Path(path)
    return directory, commands.owned_local_names(
        payload, root, profile.module, contents, publisher_root=publisher,
    )


def _plan(payload: Path, root: Path, profile: Profile, targets: tuple[Target, ...]) -> list[tuple[Path, tuple[str, ...]]]:
    operations = []
    for target in targets:
        current = target.root
        operations.extend(((current / "versions", target.versions),
                           (current / "snapshots", target.snapshots)))
        names = (profile.root_entries if current == root else
                 BASE + ("engine.ps1", "engine.sh") + RUNTIME_MODULES)
        leaves = []
        for name in names:
            if name in {"versions", "snapshots", *SELECTORS}:
                continue
            _leaf(name)
            path = current / name
            if not os.path.lexists(path):
                continue
            if name in {".venv", "venv"}:
                if not path.is_symlink() and not (getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                    _plain(path)
                    if not (path / "pyvenv.cfg").is_file():
                        raise UninstallRefused("Unrecognized legacy runtime directory")
            else:
                _plain(path, file=True)
                if path.is_dir():
                    raise UninstallRefused("Undeclared runtime container; finite files only")
            leaves.append(name)
        operations.append((current, tuple(leaves)))
        root_helpers = tuple(name for name in names if name.endswith(".py"))
        for directory, helpers in (("", root_helpers),
                                   ("bin", profile.bin_entries if current == root else RESOLVERS),
                                   ("scripts", profile.script_entries if current == root else RUNTIME_MODULES),
                                   ("tool", ("uv", "uvx", "uv.exe", "uvx.exe"))):
            groups: dict[Path, list[str]] = {}
            for name in helpers:
                relative = Path(name)
                groups.setdefault(current / directory / relative.parent, []).append(relative.name)
            for folder, entries in groups.items():
                selected = _helper_names(folder, tuple(entries)) if directory else ()
                operations.append((folder, selected))
                cache = folder / "__pycache__"
                if cache.exists():
                    _plain(cache)
                    stems = {Path(name).stem for name in entries if name.endswith(".py")}
                    compiled = tuple(path.name for path in cache.iterdir() if any(re.fullmatch(
                        re.escape(stem) + r"\.(?:cpython|pypy)-[0-9]+(?:\.opt-[0-9]+)?\.pyc", path.name,
                    ) for stem in stems))
                    operations.append((cache, _helper_names(cache, compiled)))
        context = current / "bin" / "installation-context"
        if context.exists():
            operations.append((context, _helper_names(context, CONTEXT)))
        candidates = []
        for path in current.glob("stamp-candidate-*"):
            version = path.name.removeprefix("stamp-candidate-")
            if _generation(version):
                _plain(path, file=True)
                if path.read_text(encoding="utf-8-sig").strip() == str(current / "snapshots" / version):
                    candidates.append(path.name)
        operations.append((current, tuple(candidates)))
    operations.append(_local_bindings(payload, root, profile))
    for target in reversed(targets):
        selected = profile.root_entries if target.root == root else BASE
        names = tuple(name for name in SELECTORS if name in selected and name != "deploy-manifest.json")
        for name in names:
            _plain(target.root / name, file=True)
        operations.append((target.root, names))
        if target.root != root and "deploy-manifest.json" in selected:
            _plain(target.root / "deploy-manifest.json", file=True)
            operations.append((target.root, ("deploy-manifest.json",)))
    operations.append((root, ("deploy-manifest.json",)))
    return [(directory, names) for directory, names in operations if names and directory.exists()]


def _host(shell: str) -> str:
    if os.name == "nt":
        if Path(shell).name.lower() not in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            shell = shutil.which("pwsh") or shutil.which("powershell.exe") or ""
        if not shell:
            raise UninstallRefused("Windows finite cleanup requires a native PowerShell host")
    elif Path(shell).name not in {"bash", "sh", "bash.exe"}:
        raise UninstallRefused("POSIX finite cleanup requires a native shell")
    path = Path(shell)
    if not path.is_absolute() or not path.is_file():
        raise UninstallRefused("An absolute native shell executable is required")
    return str(path)


def _consumer_closure(shell: str, operations: list[tuple[Path, tuple[str, ...]]]) -> None:
    for consumer in (Path(shell), Path.cwd()):
        for value in (consumer.absolute(), consumer.resolve()):
            if any(value == directory / name or value.is_relative_to(directory / name)
                   for directory, names in operations for name in names):
                raise UninstallRefused("A native shell or working directory lives in selected runtime code")


def _run(payload: Path, command: list[str], *, timeout: float = 45) -> str:
    if os.name == "nt":
        result = subprocess.run(  # noqa: S603 - finite, validated native argument vector
            command, capture_output=True, text=True, timeout=timeout, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        runner = _load(_dependency(payload, "installer_engine_process.py"))
        result = runner.run_owned(command, cwd=Path.cwd(), env=dict(os.environ), timeout=timeout,
                                  procutil=None, construction_fd=None)
    if result.returncode:
        raise UninstallRefused(f"Native uninstall operation failed: {result.stderr.strip()}")
    return result.stdout


def _argv(command: str, image: str) -> list[str]:
    if os.name == "nt":
        import ctypes
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        shell.CommandLineToArgvW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
        shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        count = ctypes.c_int()
        values = shell.CommandLineToArgvW(command, ctypes.byref(count))
        if not values:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            arguments = [values[index] for index in range(count.value)]
        finally:
            kernel.LocalFree(values)
    else:
        arguments = shlex.split(command)
    if os.name == "nt" and Path(image).stem.lower() in {"bash", "sh"}:
        arguments = [re.sub(r"^/(?:cygdrive/)?([A-Za-z])/", r"\1:/", value)
                     for value in arguments]
    return arguments


def _arguments(row: dict[str, Any]) -> list[str]:
    captured = row.get("argv")
    return _argv(row["command"], row["image"]) if captured is None else captured


def _parent(pid: int, image: str, command: str, payload: Path, root: Path, plugin: str,
            expected_parent: int | None = None, *, arguments: list[str] | None = None) -> bool:
    if (pid != (os.getppid() if expected_parent is None else expected_parent)
            or Path(image).stem.lower() not in {"bash", "sh", "pwsh", "powershell"}):
        return False
    try:
        arguments = _argv(command, image) if arguments is None else arguments
    except ValueError:
        return False
    entries = {payload / "scripts" / name for name in ("install.ps1", "init.ps1", "install.sh", "init.sh")}
    if plugin in {"agent-machines", "agent-containers"}:
        entries.add(payload / "scripts" / "legacy-uninstall.sh")
    matched = [index for index, value in enumerate(arguments) if Path(os.path.abspath(value)) in entries]
    if len(matched) != 1:
        return False
    index = matched[0]
    if (Path(image).stem.lower() in {"pwsh", "powershell"}
            and (not index or arguments[index - 1].lower() != "-file")):
        return False
    if (Path(image).stem.lower() in {"bash", "sh"}
            and any(value not in {"--noprofile", "--norc", "-e", "-u", "-eu", "-ue"}
                    for value in arguments[1:index])):
        return False
    tail = arguments[index + 1:]
    if Path(arguments[index]).name == "legacy-uninstall.sh":
        return len(tail) == 1 and Path(os.path.abspath(tail[0])) == root
    default = Path.home() / ("." + plugin)
    if "uninstall" not in (value.lower() for value in tail):
        return False
    lower = [value.lower() for value in tail]
    for flag in ("-installdir", "--install-dir"):
        if flag in lower:
            position = lower.index(flag)
            return position + 1 < len(tail) and Path(os.path.abspath(tail[position + 1])) == root
    return root == default


def _transaction_helper(row: dict[str, Any], payload: Path, root: Path,
                        parents: set[int] | None = None) -> bool:
    if row.get("ppid") not in (parents if parents is not None else {os.getppid()}) or Path(row["image"]).is_relative_to(root):
        return False
    try:
        argv = _arguments(row)
        if (len(argv) != 6 or argv[1:3] != ["-I", "-B"]
                or Path(argv[3]) != payload / "scripts" / "install-transaction.py"
                or Path(os.path.abspath(argv[4])) != root):
            return False
        timeout = float(argv[5])
        _plain(Path(argv[3]), file=True)
        return Path(argv[3]).is_file() and math.isfinite(timeout) and timeout > 0
    except (OSError, ValueError):
        return False


def _lifecycle_controllers(rows: list[dict[str, Any]], payload: Path, root: Path,
                           plugin: str) -> tuple[list[Path], set[int]]:
    helper = _load(_dependency(payload, "installer_engine_uninstall_controllers.py"))
    return helper.lifecycle_controllers(
        rows, payload, root, plugin, load=_load, authority_payload=_authority_payload,
        arguments=_arguments, parent=_parent, dependency=_dependency,
    )


def _controller_proofs(payload: Path):
    helper = _load(_dependency(payload, "installer_engine_uninstall_controllers.py"))
    return helper.bind(plain=_plain, run=_run, error=UninstallRefused)


def _controllers(rows: list[dict[str, Any]], payload: Path, root: Path,
                 plugin: str) -> tuple[set[int], set[int]]:
    """Recognize exact Python redirectors and the native installer's coproc shell."""
    origins, admitted = _lifecycle_controllers(rows, payload, root, plugin)
    controllers = {os.getppid(), *admitted}
    redirectors: set[int] = set()
    for _ in range(4):
        for row in rows:
            if row["pid"] not in controllers:
                continue
            if any(_parent(row["pid"], row["image"], row["command"], origin, root, plugin, row["pid"],
                           arguments=row.get("argv"))
                   for origin in origins):
                controllers.add(row["ppid"])
            try:
                argv = _arguments(row)
                source = Path(os.path.abspath(sys.argv[0]))
                trusted = (source == Path(os.path.abspath(__file__))
                           or source == payload / "scripts" / "uninstall-state.py")
                if (trusted and len(argv) >= len(sys.argv) + 1
                        and argv[-len(sys.argv):] == sys.argv
                        and re.fullmatch(r"(?:python|pythonw|pypy)[0-9.]*", Path(row["image"]).stem.lower())
                        and not Path(row["image"]).is_relative_to(root)):
                    redirectors.add(row["pid"])
                    controllers.add(row["ppid"])
            except ValueError:
                continue
    verified = admitted | {row["pid"] for row in rows if row["pid"] in controllers and any(_parent(
        row["pid"], row["image"], row["command"], origin, root, plugin, row["pid"],
        arguments=row.get("argv"),
    ) for origin in origins)}
    for _ in range(4):
        verified.update(row["pid"] for row in rows if row.get("ppid") in verified
                        and Path(row["image"]).stem.lower() in {"bash", "sh"}
                        and _parent(row["pid"], row["image"], row["command"],
                                    payload, root, plugin, row["pid"], arguments=row.get("argv")))
    return verified, redirectors


def refuse_consumers(payload: Path, plugin: str, root: Path, shell: str, profile: Profile, *,
                     controller_custody: Any = None, lifecycle_lease: Any = None) -> None:
    """Require inspectable current-user processes; never signal any process."""
    prefix = str(root).replace("\\", "/").casefold()
    if os.name == "nt":
        quote = lambda value: "'" + value.replace("'", "''") + "'"  # noqa: E731
        script = f"$prefix={quote(prefix)};$module={quote(profile.module)};$inspector={os.getpid()}\n"
        script += r"""
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
$ancestors=@($inspector)
$current=$inspector
foreach ($level in 1..4) {
    $parent=@($raw | Where-Object ProcessId -eq $current)
    if ($parent.Count -ne 1) { break }
    $current=$parent[0].ParentProcessId
    $ancestors+=@($current)
}
$rows=@($raw | ForEach-Object {
    $image=([string]$_.ExecutablePath).Replace('\','/').ToLowerInvariant()
    $command=([string]$_.CommandLine).Replace('\','/').ToLowerInvariant()
    $candidate=($_.ProcessId -in $ancestors -or
        $_.Name -match '^(bash|sh)(\.exe)?$' -or
        ($_.ParentProcessId -in $ancestors -and $_.Name -match '^(bash|sh)(\.exe)?$') -or
        $image.StartsWith($prefix+'/') -or
        $command.Contains($prefix) -or $command.Contains($module) -or
        ($_.Name -match '^(python|pythonw|pypy)[0-9.]*\.exe$' -and (-not $image -or -not $command)))
    if (-not $candidate) { return }
    $row=$_
    try { $owner=Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid -ErrorAction Stop } catch {
        if (-not (Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue)) { return }; throw
    }
    if ($owner.ReturnValue -ne 0) { throw 'Cannot attribute candidate process ownership' }
    if ($owner.Sid -eq $sid) {
        if (-not $image -or -not $command) { throw 'Cannot inspect a current-user candidate process' }
        [pscustomobject]@{pid=$row.ProcessId;ppid=$row.ParentProcessId;image=$row.ExecutablePath;
            command=$row.CommandLine;inspectionHost=($row.ProcessId -eq $PID)}
    }
})
ConvertTo-Json -InputObject $rows -Compress
"""
        rows = json.loads(_run(payload, [shell, "-NoProfile", "-NonInteractive", "-Command", script]))
    elif sys.platform.startswith("linux") and Path("/proc").is_dir():
        rows = []
        for directory in Path("/proc").iterdir():
            if not directory.name.isdigit():
                continue
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                argv = [os.fsdecode(value) for value in (directory / "cmdline").read_bytes().split(b"\0") if value]
                command = " ".join(argv)
                if not command:
                    continue
                image = os.readlink(directory / "exe")
                status = (directory / "stat").read_text()
                parent = int(status[status.rfind(")") + 2:].split()[1])
                rows.append({"pid": int(directory.name), "ppid": parent, "image": image,
                             "command": command, "argv": argv})
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as error:
                raise UninstallRefused("Cannot inspect a current-user process") from error
    elif sys.platform == "darwin":
        rows = _load(_dependency(payload, "installer_engine_uninstall_darwin.py")).inspect_processes()
    else:
        raise UninstallRefused("Runtime removal requires native Windows, Linux /proc or Darwin process inspection")
    if not any(row["pid"] == os.getpid() for row in rows):
        raise UninstallRefused("Process inventory did not include its inspector")
    identities = [profile.module]
    if profile.nested_engine:
        identities.append("agent_index_engine")
    module = re.compile(r"\b(?:" + "|".join(re.escape(value) for value in identities) + r")\b")
    controllers, redirectors = _controllers(rows, payload, root, plugin)
    if controller_custody is not None:
        if plugin != "agent-worktrees":
            raise UninstallRefused("Driver controller qualification is Worktrees-only")
        controllers.update(_controller_proofs(payload).worktrees_controllers(
            rows, payload, root, shell, controller_custody, lifecycle_lease,
        ))
    gates: set[int] = set()
    for _ in range(2):
        gates.update(row["pid"] for row in rows
                     if _transaction_helper(row, payload, root, controllers | gates))
    for row in rows:
        if (row["pid"] == os.getpid() or row["pid"] in controllers | redirectors | gates
                or (row.get("inspectionHost") is True and row.get("ppid") == os.getpid()
                    and Path(row["image"]).stem.lower() in {"pwsh", "powershell"})
                ):
            continue
        image = row["image"].replace("\\", "/").casefold()
        command = row["command"].replace("\\", "/").casefold()
        if os.name == "nt" and Path(row["image"]).stem.lower() in {"bash", "sh"}:
            command = " ".join(_argv(row["command"], row["image"])).replace("\\", "/").casefold()
        if image.startswith(prefix + "/") or re.search(re.escape(prefix) + r'(?:[/\s"\']|$)', command):
            raise UninstallRefused(f"Active rooted runtime consumer (pid={row['pid']})")
        if module.search(command):
            raise UninstallRefused(f"Unattributed module consumer (pid={row['pid']})")


def _native_remove(payload: Path, shell: str, root: Path, names: tuple[str, ...]) -> None:
    engine = _dependency(payload, "installer-engine.ps1" if os.name == "nt" else "installer-engine.sh")
    if os.name == "nt":
        def quote(value: object) -> str:
            return "'" + str(value).replace("'", "''") + "'"
        script = (
            f"$ErrorActionPreference='Stop'; . {quote(engine)}; "
            "$cleanup=Get-Command Remove-InstallRuntimeContent -CommandType Function;"
            "if (-not $cleanup.Parameters.ContainsKey('EntryNames')) {"
            "throw 'Selective cleanup EntryNames API is unavailable'};"
            f"Remove-InstallRuntimeContent -InstallRoot {quote(root)} "
            f"-EntryNames @({','.join(quote(name) for name in names)})"
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        script = (
            '. "$1"; shift; impl="$(declare -f remove_install_runtime_content)" || exit 1; '
            '[[ "$impl" == *\'${@:2}\'* || '
            '( "$impl" == *\'shift\'* && "$impl" == *\'$@\'* ) ]] || '
            '{ echo "Selective cleanup API unavailable" >&2; exit 1; }; '
            'remove_install_runtime_content "$@"'
        )
        command = [shell, "-c", script, "finite-runtime-uninstall", str(engine), str(root), *names]
    _run(payload, command, timeout=90)


def _proof(path: Path, content: bytes) -> None:
    """Publish exact retry proof only if its owned name has not been replaced."""
    _plain(path, file=True)
    if path.exists():
        if path.read_bytes() != content:
            raise UninstallRefused("Retry ownership changed during cleanup")
        return
    temporary = path.with_name(".uninstall-proof-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            raise UninstallRefused("Retry ownership appeared during cleanup")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def _custody(payload: Path, plugin: str, root: Path, owner: tuple[Any, ...],
             targets: tuple[Target, ...], runtime: Any) -> Iterator[Any]:
    binding, primitive = owner[:2]
    authority = _authority_payload(payload)
    arguments = dict(payload_root=authority, plugin_id=plugin, legacy_root=root,
                     project_root=Path(os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT") or Path.cwd()),
                     environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(authority)})
    lifecycle = binding._load_lifecycle()  # noqa: SLF001 - exact payload lifecycle
    leases = runtime._leases  # noqa: SLF001 - co-located canonical runtime custody
    with ExitStack() as held:
        lease = held.enter_context(lifecycle.legacy_lifecycle(
            binding._rules(), primitive, reserve=False, teardown=True, lock_timeout=5, **arguments,  # noqa: SLF001
        ))
        for target in targets:
            if target.snapshots:
                held.enter_context(leases.batch_removal_custody(
                    target.root / "snapshots", list(target.snapshots),
                ))
            if target.versions:
                held.enter_context(leases.batch_removal_custody(target.root, list(target.versions)))
            else:
                held.enter_context(leases._publication_lease(target.root, timeout=0))  # noqa: SLF001
            held.enter_context(leases._kernel_lease(  # noqa: SLF001
                target.root, "redirects", kind="publication", shared=False, timeout=0,
            ))
        yield lease


def cleanup(payload_root: Path, plugin_id: str, root: Path, shell: str, *,
            profile: Path | None = None, remove_runtime: bool = False,
            check_consumers: bool = False,
            controller_custody: Any = None,
            remover: Callable[[Path, str, Path, tuple[str, ...]], None] | None = None) -> dict[str, Any]:
    """Validate or remove a profile under its native whole-install transaction.

    The caller retains its product's .install.lock or command lifecycle; this
    owner revalidates the latter and holds generation/publication removal custody.
    Explicit consumer admission uses that same custody without deleting code.
    """
    payload = payload_root
    _root(payload, root)
    selected = profile_for(payload, plugin_id, profile)
    if not root.exists():
        return {"status": "absent", "root": str(root)}
    runtime = _load(payload / "scripts" / "versioned_runtime.py")
    targets = _targets(payload, plugin_id, root, selected, runtime)
    named = tuple(name for name in selected.root_entries if name not in {"versions", "snapshots"})
    if (not any(target.versions or target.snapshots for target in targets)
            and not any(os.path.lexists(root / name) for name in named)
            and not any(os.path.lexists(root / directory / name) for directory, names in (
                ("bin", selected.bin_entries), ("scripts", selected.script_entries),
            ) for name in names)):
        return {"status": "absent", "root": str(root)}
    owner = _ownership(payload, plugin_id, root)
    host = _host(shell)
    _consumer_closure(host, _plan(payload, root, selected, targets))
    _guard(payload, plugin_id, root, selected)
    if not remove_runtime and not check_consumers:
        return {"status": "ready", "root": str(root)}
    manifest = root / "deploy-manifest.json"
    existing = manifest.read_bytes() if manifest.exists() else None
    record = dict(owner[3])
    if owner[2] is not None:
        record["commandBinding"] = owner[2]
    proof = existing if existing is not None else json.dumps(record, sort_keys=True).encode() + b"\n"
    with _custody(payload, plugin_id, root, owner, targets, runtime) as lifecycle_lease:
        _root(payload, root)
        if _targets(payload, plugin_id, root, selected, runtime) != targets:
            raise UninstallRefused("Runtime inventory changed during custody admission")
        _ownership(payload, plugin_id, root)
        if (manifest.read_bytes() if manifest.exists() else None) != existing:
            raise UninstallRefused("Deployment ownership changed during custody admission")
        operations = _plan(payload, root, selected, targets)
        _consumer_closure(host, operations)
        _guard(payload, plugin_id, root, selected)
        if controller_custody is None:
            refuse_consumers(payload, plugin_id, root, host, selected)
        else:
            refuse_consumers(payload, plugin_id, root, host, selected,
                             controller_custody=controller_custody, lifecycle_lease=lifecycle_lease)
        if selected.nested_engine and len(targets) > 1:
            refuse_consumers(payload, plugin_id, targets[1].root, host, selected)
        if not remove_runtime:
            return {"status": "ready", "root": str(root)}
        if any(os.path.lexists(root / name) for name in (
            "windows-launch-broker.json", "windows-launch-broker.pending.json",
        )):
            raise UninstallRefused("Owning installer must remove its Windows broker before runtime removal")
        _proof(manifest, proof)
        try:
            _guard(payload, plugin_id, root, selected)
            for directory, names in operations:
                (remover or _native_remove)(payload, host, directory, names)
        except BaseException:
            _proof(manifest, proof)
            raise
    return {"status": "removed", "root": str(root)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shell", required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--check-consumers", action="store_true",
                        help="Also acquire removal custody and refuse consumers during admission")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--remove-runtime", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = cleanup(arguments.payload_root, arguments.plugin_id, arguments.root, arguments.shell,
                         profile=arguments.profile, remove_runtime=arguments.remove_runtime,
                         check_consumers=arguments.check_consumers)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Runtime uninstall refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
