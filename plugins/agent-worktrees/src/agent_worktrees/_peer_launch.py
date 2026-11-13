"""Read-only, same-cell native child boundary, vendored beside the validator.

Executed by absolute filename under the caller's isolated Python. The caller's
list[str] prefix remains composable: no user argument enters a shell, including
on Windows PowerShell 5.1. Resolution never provisions or activates anything.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any, TypedDict, cast

CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"
PEERS = {
    "agent-worktrees": "agent_worktrees",
    "agent-bridge": "agent_bridge",
    "agent-codespaces": "agent_codespaces",
    "agent-containers": "agent_containers",
    "agent-dispatch": "agent_dispatch",
    "agent-ssh": "agent_ssh",
    "agent-pull-requests": "agent_pull_requests",
    "agent-machines": "agent_machines",
    "agent-vault": "agent_vault",
}
OWNERS = {
    "agent-bridge", "agent-dispatch", "agent-codespaces", "agent-containers",
    "agent-logger", "agent-index", "agent-machines", "agent-worktrees", "agent-pull-requests",
    "agent-ssh", "agent-mcp", "agent-vault",
}
_packaged_primitives: dict[Path, ModuleType] = {}
_peer_runtimes: dict[Path, tuple[tuple[tuple[str, int, int, int, int], ...], ModuleType | None]] = {}
_primitive_lock = threading.Lock()


def _reset_primitive_lock() -> None:
    global _primitive_lock
    _primitive_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_primitive_lock)


class _NoWindowKwargs(TypedDict, total=False):
    creationflags: int


def no_window_kwargs() -> _NoWindowKwargs:
    """Keep the standalone bootstrap dependency-free and its children windowless."""
    if os.name == "nt":
        return {"creationflags": cast(int, getattr(subprocess, "CREATE_NO_WINDOW"))}  # headless-guard: allow dependency-free canonical bootstrap
    return {}


class ContextRefused(RuntimeError):
    """An explicit installation boundary was rejected; never an optional miss."""


def launch_prefix(owner: str, own_root: Path, raw_context: str, peer: str) -> list[str]:
    """Build a native prefix that revalidates receipts at execution time."""
    bootstrap = Path(sys.executable)
    if bootstrap.name.lower() == "pythonw.exe":
        bootstrap = bootstrap.with_name("python.exe")
    return [
        str(bootstrap), "-I", "-B", "-X", "utf8", str(Path(__file__).resolve()),
        owner, str(own_root), raw_context, peer,
    ]


def _retention_launcher(root: Path, python: Path, module: str) -> Path:
    python = _checked_python({"pluginId": module, "versionsRoot": str(root / "versions")}, python)
    environment = python.parent.parent
    slot = environment.parent if environment.name == ".venv" else environment
    bootstrap = slot / ".runtime-bootstrap"
    launcher = bootstrap / "runtime_launcher.py"
    if (
        slot.name == "dev" or slot.is_symlink() or bootstrap.is_symlink() or launcher.is_symlink()
        or bootstrap.resolve() != slot.resolve() / bootstrap.name
        or not launcher.is_file()
    ):
        raise ValueError(f"{module}: selected runtime has no immutable retention bootstrap")
    primitive = _load_primitive(launcher, "_selected_runtime_launcher")
    if not callable(getattr(primitive, "runtime_identity", None)):
        raise ValueError(f"{module}: selected retention bootstrap has no build identity support")
    return launcher


def retained_runtime_identity(root: Path, python: Path, module: str) -> str:
    launcher = _retention_launcher(root, python, module)
    primitive = _load_primitive(launcher, "_selected_runtime_identity")
    return cast(str, primitive.runtime_identity(launcher.parent.parent))


def retained_runtime_prefix(
    root: Path, python: Path, module: str, *, runtime_identity: str,
) -> list[str]:
    """Carry the original identity to the selected bootstrap, never recapture it."""
    launcher = _retention_launcher(root, python, module)
    return [
        str(python), "-I", "-S", "-B", "-X", "utf8", str(launcher),
        "--runtime-identity", runtime_identity, "-m", module,
    ]


def pinned_launch_prefix(
    owner: str, own_root: Path, raw_context: str, peer: str,
) -> tuple[list[str], Path]:
    """Select once; callers retain this prefix for qualification and later calls."""
    context, _environment, python = _resolve_peer(
        owner, own_root, raw_context, peer, allow_native=False,
    )
    if python is None:
        raise ValueError(f"{peer}: pinning requires the peer-owned Python resolver")
    identity = retained_runtime_identity(Path(context["pluginRoot"]), python, PEERS[peer])
    prefix = launch_prefix(owner, own_root, raw_context, peer)
    prefix[6:6] = ["--pinned-runtime", str(python), "--runtime-identity", identity]
    return prefix, python


def _load_primitive(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Installation-context primitive is unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    except Exception as error:
        raise ValueError(f"Installation-context primitive initialization failed: {path}") from error
    return module


def _packaged_primitive() -> ModuleType:
    path = Path(__file__).with_name("_installation_context.py")
    # Preserve bootstrap-held GLOBALROOT paths rather than resolving the DOS map.
    key = path.absolute() if path.drive.casefold() == "\\\\?\\globalroot" else path.resolve()
    with _primitive_lock:
        module = _packaged_primitives.get(key)
        if module is None:
            module = _load_primitive(key, "_owner_installation_context")
            _packaged_primitives[key] = module
        return module


def _active_context(
    primitive: ModuleType, receipt: Path, durable: Path, plugin: str,
    cell: Path, environment: dict[str, str],
) -> dict[str, Any]:
    context = cast(dict[str, Any], primitive.validate_context_receipt(
        receipt, durable, expected_plugin_id=plugin,
        expected_cell_root=cell, environment=environment,
    ))
    namespace = primitive.validate_namespace_receipt(
        context["namespaceReceipt"], durable,
    )
    if context["state"] != "active" or namespace["receipt"]["state"] != "active":
        raise ValueError(f"{plugin}: installation and namespace must be active")
    return context


def legacy_peer_environment(inherited: dict[str, str]) -> dict[str, str]:
    """Remove caller routing and credentials before a peer selects its own roots."""
    environment = {}
    for key, value in inherited.items():
        upper = key.upper()
        if upper in {
            CONTEXT_ENV, "COPILOT_PLUGIN_ROOT", "PYTHONPATH", "PYTHONHOME",
            "AGENT_HOME",
            "GH_TOKEN", "GITHUB_TOKEN",
        }:
            continue
        if upper.startswith((
            "AGENT_RT_", "AGENT_DISPATCH_", "AGENT_CODESPACES_", "AGENT_CONTAINERS_",
            "AGENT_LOGGER_", "AGENT_WORKTREES_", "AGENT_BRIDGE_", "AGENT_INDEX_",
            "AGENT_MACHINES_", "AGENT_SSH_", "AGENT_PULL_REQUESTS_",
            "AGENT_MCP_", "AGENT_VAULT_",
        )) or (
            upper.startswith("AGENT_")
            and upper.endswith(("_ROOT", "_DIR", "_HOME", "_INSTALLATION_ID"))
        ):
            continue
        environment[key] = value
    return environment


def _vault_service_suffix(root: str) -> str:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        # Match the native gate's invariant casing, not Python's contextual lowercasing.
        mapping = ctypes.WinDLL("kernel32", use_last_error=True).LCMapStringEx
        mapping.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_int,
            wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM,
        )
        mapping.restype = ctypes.c_int
        size = mapping("", 0x100, root, -1, None, 0, None, None, 0)
        if not size:
            raise ValueError("Vault native installation suffix is unavailable")
        mapped = ctypes.create_unicode_buffer(size)
        if not mapping("", 0x100, root, -1, mapped, size, None, None, 0):
            raise ValueError("Vault native installation suffix is unavailable")
        normalized = mapped.value
    else:
        if not sys.platform.startswith("linux"):
            raise ValueError("Vault peer invocation supports native Windows and Linux only")
        if not (shutil.which("sha256sum") or shutil.which("shasum")):
            raise ValueError("Vault peer requires the native owner's SHA-256 suffix support")
        normalized = root.replace("\\", "/").translate(
            str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"),
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def peer_environment(context: dict[str, Any], inherited: dict[str, str]) -> dict[str, str]:
    """Rebind installation routing, retaining non-routing caller/session data."""
    environment = legacy_peer_environment(inherited)
    plugin = context["pluginId"]
    prefix = plugin.upper().replace("-", "_")
    environment.update({
        CONTEXT_ENV: context["installReceipt"],
        "COPILOT_PLUGIN_ROOT": context["payloadRoot"],
        f"{prefix}_PAYLOAD_ROOT": context["payloadRoot"],
        "AGENT_RT_ROOT": context["pluginRoot"],
        "PYTHONUTF8": "1",
    })
    if plugin == "agent-bridge":
        environment.update({
            "AGENT_BRIDGE_INSTALL_DIR": context["pluginRoot"],
            "AGENT_BRIDGE_CONFIG_DIR": context["pluginRoot"],
            "AGENT_BRIDGE_INSTALLATION_ID": f"{context['marketplaceId']}/{plugin}",
            "AGENT_BRIDGE_CONNECT_LOG": str(Path(context["logsRoot"]) / "connect.log"),
        })
    elif plugin == "agent-codespaces":
        environment["AGENT_CODESPACES_HOME"] = context["pluginRoot"]
    elif plugin == "agent-containers":
        environment.update({
            "AGENT_CONTAINERS_HOME": context["pluginRoot"],
            "AGENT_CONTAINERS_STATE_DIR": context["pluginRoot"],
        })
    elif plugin == "agent-dispatch":
        environment.update({
            "AGENT_DISPATCH_INSTALL_DIR": context["pluginRoot"],
            "AGENT_DISPATCH_ROUTING_DIR": context["pluginRoot"],
            "AGENT_DISPATCH_RUN_DIR": str(Path(context["pluginRoot"]) / "run"),
        })
        for key in (
            "AGENT_DISPATCH_TASK_AUTHORITY",
            "AGENT_DISPATCH_URL",
            "AGENT_DISPATCH_TOKEN",
            "AGENT_DISPATCH_CONTROL_TOKEN",
            "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND",
            "AGENT_DISPATCH_SHARED_URL",
            "AGENT_DISPATCH_SHARED_TOKEN",
            "AGENT_DISPATCH_SHARED_CONTROL_TOKEN",
            "AGENT_DISPATCH_SHARED_TOKEN_COMMAND",
            "AGENT_DISPATCH_SHARED_CONTROL_TOKEN_COMMAND",
            "AGENT_DISPATCH_ENDPOINT",
            "AGENT_DISPATCH_SUPERVISE_MACHINE",
            "AGENT_DISPATCH_FAILOVER_MACHINE",
            "AGENT_DISPATCH_WSL_WINDOWS_CLIENT",
            "AGENT_DISPATCH_NO_AUTOSTART",
        ):
            value = inherited.get(key)
            if value:
                environment[key] = value
    elif plugin == "agent-pull-requests":
        environment["AGENT_PULL_REQUESTS_HOME"] = context["stateRoot"]
    elif plugin == "agent-vault":
        root = str(Path(context["pluginRoot"]).absolute())
        suffix = _vault_service_suffix(root)
        for key in ("KPDB", "VAULT_GROUP", "AGENT_VAULT", "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD"):
            environment.pop(key, None)
        environment.update({
            "AGENT_VAULT_HOME": root,
            "AGENT_VAULT_VALIDATED_CONTEXT": context["installReceipt"],
            "AGENT_VAULT_INSTALLATION_ID": f"{context['marketplaceId']}/{plugin}",
            "AGENT_VAULT_RUN_DIR": str(Path(root) / "run"),
            "AGENT_VAULT_CORE_RUN_DIR": str(Path(root) / "core"),
            "AGENT_VAULT_CACHE_DIR": str(Path(root) / "cache"),
            "AGENT_VAULT_SOCKET": str(Path(root) / "run" / "agent-vault.sock"),
            "AGENT_VAULT_PIPE": rf"\\.\pipe\agent-vault-{suffix}",
            "AGENT_VAULT_PID": str(Path(root) / "run" / "agent-vault-service.pid"),
            "AGENT_VAULT_LOG": str(Path(root) / "logs" / "agent-vault-service.log"),
            "AGENT_VAULT_PORT": "0",
            "AGENT_VAULT_SYSTEMD_UNIT": f"agent-vault-{suffix}.service",
            "AGENT_VAULT_TASK_NAME": f"AgentVault-{suffix}",
            "AGENT_VAULT_NO_SELFPROVISION": "1",
        })
    return environment


def _governance(
    primitive: ModuleType, context: dict[str, Any], durable: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    plugin = context["pluginId"]
    resolution = cast(dict[str, Any], primitive.resolve_installation_mode(
        legacy_root=Path.home() / f".{plugin}",
        payload_root=context["payloadRoot"], plugin_id=plugin,
        context=context["installReceipt"], durable_home=durable,
        expected_cell_root=context["cellRoot"],
        environment=environment,
    ))
    allowed = (
        resolution["status"] == "ready"
        and resolution["reason"] == "namespaced-active"
    ) or resolution["status"] == "deactivation-required"
    if (
        not allowed
        or resolution["actualMode"] != "namespaced"
        or resolution["runtimeRoot"] != context["pluginRoot"]
        or resolution["context"] != context["installReceipt"]
        or resolution["installGeneration"] != context["generation"]
    ):
        raise ValueError(
            f"{plugin}: installation governance blocks invocation: "
            f"{resolution['status']} ({resolution['reason']})"
        )
    return resolution


def _peer_python_inprocess(context: dict[str, Any]) -> Path | None:
    """Retain peer code, not its selection; replaced code requires a fresh caller."""
    scripts = Path(context["payloadRoot"]) / "scripts"
    resolver = scripts / "versioned_runtime.py"
    if not resolver.is_file():
        return None
    key = resolver.resolve()
    sources = tuple(sorted(scripts.glob("versioned_runtime*.py")))
    signature = tuple(
        (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        for path in sources for info in (path.stat(),)
    )
    with _primitive_lock:
        retained = _peer_runtimes.get(key)
        if retained is None:
            name = "_peer_runtime_" + str(len(_peer_runtimes))
            module = ModuleType(name)
            module.__file__ = str(key)
            sys.modules[name] = module
            _peer_runtimes[key] = (signature, None)
            try:
                exec(compile(key.read_bytes(), str(key), "exec"), module.__dict__)
            except Exception as error:
                raise ValueError("Peer runtime resolver load failed; restart the observing caller") from error
            _peer_runtimes[key] = (signature, module)
        else:
            original, retained_module = retained
            if original != signature:
                raise ValueError("Peer runtime resolver changed; restart the observing caller")
            if retained_module is None:
                raise ValueError("Peer runtime resolver load failed; restart the observing caller")
            module = retained_module
    resolve = getattr(module, "resolve_python", None)
    if not callable(resolve):
        return None
    try:
        python = resolve(Path(context["pluginRoot"]))
    except Exception as error:
        raise ValueError("Peer runtime resolver selection failed") from error
    if python is None:
        raise ValueError(f"{context['pluginId']}: no completed runtime resolved")
    return _checked_python(context, Path(python))


def _peer_python(context: dict[str, Any], environment: dict[str, str]) -> Path:
    """Use the peer's Python resolver; older peers retain their native boundary."""
    python = _peer_python_inprocess(context)
    if python is not None:
        return python
    scripts = Path(context["payloadRoot"]) / "scripts"
    if os.name == "nt":
        resolver = scripts / "resolve-runtime.ps1"
        host = (
            Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell"
            / "v1.0" / "powershell.exe"
        )
        # The command is constant. Paths travel in the rebound environment;
        # arbitrary caller argv never crosses PS5's native serialization.
        command = [
            str(host), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
            ". (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts\\resolve-runtime.ps1'); "
            "if ($AgentRtPy) { [Console]::Out.Write($AgentRtPy) }",
        ]
    else:
        resolver = scripts / "resolve-runtime.sh"
        command = [
            "/bin/sh", "-c",
            '. "$COPILOT_PLUGIN_ROOT/scripts/resolve-runtime.sh"; '
            'printf %s "${AGENT_RT_PY:-}"',
        ]
    if not resolver.is_file():
        raise ValueError(f"Peer canonical runtime resolver is missing: {resolver}")
    result = subprocess.run(
        command, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, encoding="utf-8", timeout=15, check=True,
        **no_window_kwargs(),
    )
    if not result.stdout:
        raise ValueError(f"{context['pluginId']}: no completed runtime resolved")
    return _checked_python(context, Path(result.stdout))


def _checked_python(context: dict[str, Any], python: Path) -> Path:
    versions = Path(context["versionsRoot"])
    environment_root = python.parent.parent
    slot = environment_root.parent if environment_root.name == ".venv" else environment_root
    expected_environment = versions.resolve() / slot.name
    if environment_root.name == ".venv":
        expected_environment /= ".venv"
    # Constrain the resolver's answer to this peer, but do NOT dereference the
    # interpreter leaf: ordinary POSIX venvs symlink it to their base Python.
    if (
        not python.is_absolute() or not python.is_file()
        or python.parent.name not in {"bin", "Scripts"}
        or python.name not in {"python", "python.exe"}
        or slot.parent != versions
        or python.parent.resolve() != expected_environment / python.parent.name
    ):
        raise ValueError(f"{context['pluginId']}: resolver escaped its version slots")
    return python


def validate_owner(owner: str, own_root: Path, raw_context: str) -> dict[str, Any]:
    """Validate the caller using packaged bytes, never an unvalidated payload."""
    if owner not in OWNERS:
        raise ValueError(f"Unsupported peer-launch owner: {owner}")
    if (
        not own_root.is_absolute() or own_root.name != owner
        or own_root.parent.name != "plugins"
        or own_root.parent.parent.parent.name != "marketplaces"
    ):
        raise ValueError("Explicit context requires a canonical cell install root")
    cell = own_root.parent.parent
    durable = cell.parent.parent
    primitive = _packaged_primitive()
    declared = None
    pointer = raw_context
    if raw_context.lstrip().startswith("{"):
        declared = json.loads(raw_context)
        pointer = declared.get("installReceipt")
    if not isinstance(pointer, str) or not pointer:
        raise ValueError("Explicit context must name an installation receipt")
    own = _active_context(
        primitive, Path(pointer), durable, owner, cell, dict(os.environ),
    )
    if not primitive.paths_equal(own["pluginRoot"], own_root):
        raise ValueError("Explicit context does not belong to this owner installation")
    if declared is not None and any(
        value != own.get(key) for key, value in declared.items() if key != "action"
    ):
        raise ValueError("Explicit inline context conflicts with its canonical receipt")
    own["invocationGovernance"] = _governance(
        primitive, own, durable, dict(os.environ),
    )
    return own


def _resolve_peer(
    owner: str, own_root: Path, raw_context: str, plugin: str, *, allow_native: bool,
    pinned_python: Path | None = None,
) -> tuple[dict[str, Any], dict[str, str], Path | None]:
    if plugin not in PEERS:
        raise ValueError(f"Unsupported peer: {plugin}")
    own = validate_owner(owner, own_root, raw_context)
    cell = own_root.parent.parent
    durable = cell.parent.parent
    primitive = _packaged_primitive()
    peer_receipt = Path(own["cellRoot"]) / "plugins" / plugin / "install.json"
    peer = _active_context(primitive, peer_receipt, durable, plugin, cell, {})
    environment = peer_environment(peer, dict(os.environ))
    peer_primitive = _load_primitive(
        Path(peer["payloadRoot"]) / "scripts" / "installation-context"
        / "installation_context.py", "_peer_installation_context",
    )
    # Use the peer's shipped contract too: a peer may update independently.
    validated = _active_context(
        peer_primitive, peer_receipt, durable, plugin, cell, environment,
    )
    if validated != peer:
        raise ValueError("Peer installation changed during validation")
    before = _governance(peer_primitive, peer, durable, environment)
    python = _checked_python(peer, pinned_python) if pinned_python is not None else (
        _peer_python(peer, environment) if allow_native else _peer_python_inprocess(peer)
    )
    if (
        validate_owner(owner, own_root, raw_context) != own
        or _active_context(peer_primitive, peer_receipt, durable, plugin,
                           cell, environment) != peer
        or _governance(peer_primitive, peer, durable, environment) != before
    ):
        raise ValueError("Installation governance changed during peer resolution")
    if pinned_python is None and not allow_native and _peer_python_inprocess(peer) != python:
        raise ValueError("Peer runtime selection changed during observation")
    return peer, environment, python


def observe_peer_python(
    owner: str, own_root: Path, raw_context: str, plugin: str,
) -> Path | None:
    """Observe the actual selection child-free; native-only peers remain unknown."""
    return _resolve_peer(owner, own_root, raw_context, plugin, allow_native=False)[2]


def launch(
    owner: str, own_root: Path, raw_context: str, plugin: str, argv: list[str],
    *, pinned_python: Path | None = None, runtime_identity: str | None = None,
) -> int:
    """Validate both owners, delegate peer governance/resolution, then launch."""
    peer, environment, python = _resolve_peer(
        owner, own_root, raw_context, plugin, allow_native=True, pinned_python=pinned_python,
    )
    if python is None:
        raise ValueError(f"{plugin}: no completed runtime resolved")
    prefix = (
        retained_runtime_prefix(
            Path(peer["pluginRoot"]), python, PEERS[plugin],
            runtime_identity=runtime_identity or "",
        )
        if pinned_python is not None
        else [str(python), "-I", "-B", "-X", "utf8", "-m", PEERS[plugin]]
    )
    command = [*prefix, *argv]
    if os.name != "nt":
        os.execve(python, command, environment)
    # Keep inherited stdio and process-tree ownership, not a detached child.
    executable = str(python)
    if len(executable) >= 260 and not executable.startswith("\\\\?\\"):
        executable = (
            "\\\\?\\UNC\\" + executable[2:] if executable.startswith("\\\\")
            else "\\\\?\\" + executable
        )
    return subprocess.call(
        command, executable=executable, env=environment,
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        **no_window_kwargs(),
    )


def main() -> int:
    try:
        arguments = sys.argv[1:]
        pinned_python = None
        runtime_identity = None
        if arguments[:1] == ["--pinned-runtime"]:
            if len(arguments) < 8 or arguments[2] != "--runtime-identity":
                raise ValueError("Expected pinned runtime, original build identity, owner, context, and peer")
            pinned_python = Path(arguments[1])
            runtime_identity = arguments[3]
            arguments = arguments[4:]
        if len(arguments) < 4:
            raise ValueError("Expected owner, owner root, explicit context, and peer")
        return launch(
            arguments[0], Path(arguments[1]), arguments[2], arguments[3], arguments[4:],
            pinned_python=pinned_python, runtime_identity=runtime_identity,
        )
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        print(f"peer launch refused: {error}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
