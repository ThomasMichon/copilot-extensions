"""Install the small global connector without depending on an application slot."""

from __future__ import annotations

from contextlib import contextmanager
import json
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Iterator

from agent_procutil.owned_process import run_caller_owned
from agent_procutil._interpreter import physical_executable, venv_home_python
from plugin_activation import state
from plugin_activation.preservation import activation_custody

from .service_lifecycle import _ordinary, _same_directory


MARKETPLACE = "copilot-driver-standalone"
IDENTITY = f"agent-remote-driver@{MARKETPLACE}"


def _startup_record(binding: Any) -> dict[str, Any]:
    bootstrap = binding.slot / ".runtime-bootstrap" / "runtime_launcher.py"
    _ordinary(bootstrap)
    spec = importlib.util.spec_from_file_location("_driver_runtime_launcher", bootstrap)
    if spec is None or spec.loader is None:
        raise RuntimeError("Driver retained runtime launcher is unavailable")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    arguments = []
    python = binding.python
    if os.name == "nt":
        base = venv_home_python(
            str(python), "pythonw.exe", resolve_executable=physical_executable,
        )
        if base is None:
            raise RuntimeError("Driver startup requires its retained runtime's physical GUI interpreter")
        python = Path(base)
        _ordinary(python)
        arguments.append("--gui-runtime")
    arguments.extend([
        "--runtime-identity", launcher.runtime_identity(binding.slot),
        "-m", "agent_remote_driver_app", "service", "ensure",
    ])
    return {
        "schema": "copilot-driver.service.startup", "version": 1,
        "root": str(binding.root), "generation": binding.slot.name,
        "argv": [str(python), "-I", "-S", "-B", str(bootstrap), *arguments],
    }


def publish_startup(binding: Any, marketplace: Path) -> None:
    """Bind the connector to the canonical retained runtime, not a PATH command."""
    state.write_json_object_atomic(marketplace / "service-binding.json", _startup_record(binding))


def _same_startup(observed: dict[str, Any], expected: dict[str, Any]) -> bool:
    if observed == expected:
        return True
    arguments, original = observed.get("argv"), expected.get("argv")
    if (
        not isinstance(observed.get("root"), str)
        or not _same_directory(Path(observed["root"]), Path(expected["root"]))
        or not isinstance(arguments, list) or not isinstance(original, list)
        or len(arguments) != len(original) or len(original) < 5
        or any(not isinstance(value, str) for value in arguments)
        or any(
            not _same_directory(Path(value), Path(original[index])) if index in (0, 4)
            else value != original[index] for index, value in enumerate(arguments)
        )
    ):
        return False
    return {**observed, "root": expected["root"], "argv": original} == expected


def _assets(source: Path) -> dict[str, bytes]:
    _ordinary(source, directory=True)
    assets = {}
    for path in source.rglob("*"):
        if path.is_dir():
            _ordinary(path, directory=True)
            continue
        _ordinary(path)
        name = path.relative_to(source).as_posix()
        if name != "plugin.json" and not (
            name.startswith("extensions/agent-remote-driver/") and path.suffix == ".mjs"
        ):
            raise ValueError("unexpected packaged Driver connector resource")
        assets[name] = path.read_bytes()
    if not {
        "plugin.json", "extensions/agent-remote-driver/extension.mjs",
        "extensions/agent-remote-driver/sdk-compat.mjs",
    }.issubset(assets):
        raise ValueError("packaged Driver connector is incomplete")
    manifest = json.loads(assets["plugin.json"])
    if manifest.get("name") != "agent-remote-driver":
        raise ValueError("packaged Driver connector has a different identity")
    return assets


def retain(binding: Any, source: Path) -> Path:
    """Keep live-reference assets outside slots retired by application updates."""
    assets = _assets(source)
    marketplace = Path(binding.root) / "connector-marketplace"
    generation = marketplace / "generations" / Path(binding.slot).name
    for path in (binding.root, binding.slot):
        _ordinary(path, directory=True)
    for path in (marketplace, generation.parent):
        path.mkdir(exist_ok=True)
        _ordinary(path, directory=True)
    if generation.exists():
        if _assets(generation) != assets:
            raise ValueError("retained Driver connector generation differs from its application")
    else:
        staged = Path(tempfile.mkdtemp(prefix=".incoming-", dir=generation.parent))
        try:
            for name, body in assets.items():
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            staged.rename(generation)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
    catalog = marketplace / ".github" / "plugin"
    for path in (catalog.parent, catalog):
        path.mkdir(exist_ok=True)
        _ordinary(path, directory=True)
    state.write_json_object_atomic(catalog / "marketplace.json", {
        "name": MARKETPLACE, "owner": {"name": "Copilot Driver"},
        "plugins": [{
            "name": "agent-remote-driver",
            "source": generation.relative_to(marketplace).as_posix(),
            "version": json.loads(assets["plugin.json"])["version"],
        }],
    })
    return marketplace


def install(
    binding: Any, *, deadline: float, executable: str | None = None,
    home: Path | None = None, assets: Path | None = None,
) -> dict[str, Any]:
    """Use native CLI acquisition while preserving an existing user's opt-out."""
    if deadline <= time.monotonic():
        raise TimeoutError("Driver connector installation deadline expired before launch")
    home = home or Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")
    executable = executable or shutil.which("copilot")
    if not executable:
        raise RuntimeError("Copilot CLI is required to install the Driver connector")
    environment = dict(os.environ, COPILOT_HOME=str(home))
    with activation_custody(home) as custody:
        _, settings = state.read_json_object(home / "settings.json")
        registrations = settings.get("extraKnownMarketplaces", {})
        if not isinstance(registrations, dict):
            raise ValueError("Copilot marketplace configuration must be an object")
        expected = binding.root / "connector-marketplace"
        existing = registrations.get(MARKETPLACE)
        if existing is not None:
            if (
                not isinstance(existing, dict)
                or not isinstance(existing.get("source"), dict)
                or existing["source"].get("source") != "directory"
                or not isinstance(existing["source"].get("path"), str)
                or not _same_directory(Path(existing["source"]["path"]), expected)
            ):
                raise ValueError("Driver marketplace belongs to a different installation")
        previous = state.capture(IDENTITY, home)
        enabled = (
            previous.user_value if previous.user_value_existed
            else previous.inventory_enabled if previous.inventory_record_existed
            and previous.inventory_enabled is not None else True
        )
        marketplace = retain(binding, assets or Path(__file__).parent / "_runtime_assets")

        def invoke(arguments: list[str], preservation: Any = None) -> str:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if preservation is not None:
                    preservation.confirm_not_launched()
                raise TimeoutError("Driver connector installation deadline expired before launch")
            settled = None if preservation is None else (
                preservation.confirm_quiescent if os.name == "nt"
                else preservation.confirm_posix_quiescent
            )
            result = run_caller_owned(
                [executable, "plugin", *arguments],
                cwd=binding.root, env=environment, timeout=remaining,
                max_stdout_bytes=128 * 1024, max_stderr_bytes=64 * 1024,
                on_settled=settled,
            )
            if result.returncode:
                raise RuntimeError(f"native Driver connector {arguments[0]} failed")
            if not isinstance(result.stdout, str):
                raise RuntimeError("native Driver connector output is not text")
            return result.stdout

        for arguments in (
            ["marketplace", "add", str(marketplace)], ["install", IDENTITY],
        ):
            with custody.preserve(IDENTITY) as preservation:
                invoke(arguments, preservation)
        if enabled:
            invoke(["enable", IDENTITY])
        inventory = json.loads(invoke(["list", "--json"]))
        if not isinstance(inventory, list):
            raise RuntimeError("native Driver plugin inventory must be an array")
        selected = [
            item for item in inventory if isinstance(item, dict)
            and item.get("name") == "agent-remote-driver"
            and item.get("marketplace") == MARKETPLACE
        ]
        observed = state.inspect_plugin_state(IDENTITY, home)
        if (
            len(selected) != 1
            or selected[0].get("enabled") is not enabled
            or selected[0].get("source") != "live"
            or not isinstance(selected[0].get("installedFrom"), str)
            or not _same_directory(Path(selected[0]["installedFrom"]), marketplace)
            or selected[0].get("version") != json.loads(
                (marketplace / "generations" / binding.slot.name / "plugin.json").read_bytes()
            )["version"]
            or enabled and observed["userActivation"] != "true"
        ):
            raise RuntimeError("native Driver connector installation did not reach readiness")
        return {**observed, "installed": True, "enabled": enabled, "source": "live"}


def withdraw(
    binding: Any, *, deadline: float, executable: str | None = None,
    home: Path | None = None,
) -> dict[str, bool]:
    """Withdraw through native ownership, retaining live assets."""
    with withdrawal_custody(
        binding, deadline=deadline, executable=executable, home=home,
    ) as result:
        return result


@contextmanager
def withdrawal_custody(
    binding: Any, *, deadline: float, executable: str | None = None,
    home: Path | None = None, withdraw: bool = True,
) -> Iterator[dict[str, bool]]:
    """Retain activation exclusion through the application's later removal phases.

    The application removal owner must hold service-start custody and retain its
    durable operation evidence. This helper neither stops services nor deletes a
    runtime, retained payload, user workspace or session. A settled retry checks
    absence without repeating a native withdrawal.
    """
    if deadline <= time.monotonic():
        raise TimeoutError("Driver connector withdrawal deadline expired before launch")
    home = home or Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")
    executable = executable or shutil.which("copilot")
    if not executable:
        raise RuntimeError("Copilot CLI is required to withdraw the Driver connector")
    marketplace = binding.root / "connector-marketplace"
    payload = marketplace / "generations" / binding.slot.name
    for path in (binding.root, marketplace, payload.parent):
        _ordinary(path, directory=True)
    original_assets = _assets(payload)
    environment = dict(os.environ, COPILOT_HOME=str(home))

    def registration() -> bool:
        _, settings = state.read_json_object(home / "settings.json")
        known = settings.get("extraKnownMarketplaces", {})
        if not isinstance(known, dict):
            raise ValueError("Copilot marketplace configuration must be an object")
        existing = known.get(MARKETPLACE)
        if existing is None:
            return False
        if (
            not isinstance(existing, dict) or not isinstance(existing.get("source"), dict)
            or existing["source"].get("source") != "directory"
            or not isinstance(existing["source"].get("path"), str)
            or not _same_directory(Path(existing["source"]["path"]), marketplace)
        ):
            raise ValueError("Driver marketplace belongs to a different installation")
        return True

    def invoke(arguments: list[str]) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Driver connector withdrawal deadline expired before launch")
        result = run_caller_owned(
            [executable, "plugin", *arguments], cwd=binding.root,
            env=environment, timeout=remaining,
            max_stdout_bytes=128 * 1024, max_stderr_bytes=64 * 1024,
        )
        if result.returncode:
            raise RuntimeError(f"native Driver connector {arguments[0]} withdrawal failed")
        if not isinstance(result.stdout, str):
            raise RuntimeError("native Driver connector output is not text")
        return result.stdout

    def installed() -> dict[str, Any] | None:
        inventory = json.loads(invoke(["list", "--json"]))
        if not isinstance(inventory, list) or any(not isinstance(item, dict) for item in inventory):
            raise RuntimeError("native Driver plugin inventory must be an array of objects")
        selected = [item for item in inventory if item.get("marketplace") == MARKETPLACE]
        if len(selected) > 1 or any(item.get("name") != "agent-remote-driver" for item in selected):
            raise RuntimeError("Driver marketplace has another or ambiguous plugin owner")
        for item in selected:
            if (
                item.get("source") != "live" or not isinstance(item.get("installedFrom"), str)
                or not _same_directory(Path(item["installedFrom"]), marketplace)
                or type(item.get("enabled")) is not bool
            ):
                raise ValueError("Driver plugin belongs to a different installation")
        return selected[0] if selected else None

    with activation_custody(home):
        registered = registration()
        present = installed()
        startup = marketplace / "service-binding.json"
        if not withdraw:
            if registered or present is not None or os.path.lexists(startup):
                raise RuntimeError("Driver connector withdrawal acquired another owner")
            yield {"withdrawn": True, "assets_retained": True}
            return
        if present is not None and not registered:
            raise RuntimeError("Driver plugin has no attributable marketplace registration")
        startup_state = None
        if os.path.lexists(startup):
            _ordinary(startup)
            info = startup.lstat()
            if info.st_size > 16384:
                raise ValueError("Driver startup binding exceeds its byte bound")
            _, observed = state.read_json_object(startup)
            if not _same_startup(observed, _startup_record(binding)):
                raise ValueError("Driver startup binding belongs to another application")
            startup_state = (info.st_dev, info.st_ino, observed)
        if startup_state is not None:
            _ordinary(startup)
            info = startup.lstat()
            if info.st_size > 16384:
                raise ValueError("Driver startup binding exceeds its byte bound")
            _, observed = state.read_json_object(startup)
            if (info.st_dev, info.st_ino, observed) != startup_state:
                raise RuntimeError("Driver startup binding changed before withdrawal")
            startup.unlink()
        if present is not None and present["enabled"]:
            if not registration():
                raise RuntimeError("Driver marketplace changed before plugin withdrawal")
            invoke(["uninstall", IDENTITY])
            remaining = installed()
            if remaining is not None and remaining["enabled"]:
                raise RuntimeError("native Driver plugin withdrawal did not settle")
        if registration():
            remaining = installed()
            if remaining is not None and remaining["enabled"]:
                raise RuntimeError("Driver marketplace acquired an active plugin before withdrawal")
            invoke(["marketplace", "remove", MARKETPLACE])
        if registration() or installed() is not None:
            raise RuntimeError("native Driver connector withdrawal did not settle")
        if _assets(payload) != original_assets:
            raise RuntimeError("native Driver withdrawal changed retained connector assets")
        yield {"withdrawn": True, "assets_retained": True}
