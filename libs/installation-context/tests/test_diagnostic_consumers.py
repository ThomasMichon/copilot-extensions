"""Execute read-only diagnostics through derived primitives and peer wrappers."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

import test_installation_mode_governance as governance

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.installation_context_smoke


def _load(path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _python_copies() -> list[Path]:
    spec = importlib.util.spec_from_file_location(
        "diagnostic_vendor_inventory", ROOT / "tools" / "sync-installation-context.py"
    )
    assert spec and spec.loader
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    return [
        destination
        for source, destination in inventory.vendor_pairs()
        if source.name == "installation_context.py"
    ]


PYTHON_COPIES = _python_copies()
PEER_COPIES = [
    ROOT / "libs" / "peer-launch" / "peer_launch.py",
    *sorted((ROOT / "plugins").glob("*/src/*/_peer_launch.py")),
    ROOT / "plugins" / "agent-dispatch" / "src" / "agent_dispatch" / "peer_launch.py",
]


def _snapshot(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): (
            path.is_dir(), path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
    }


def _fixture(tmp_path: Path, namespaced: bool, pointer_exists: bool) -> tuple[dict, Path, Path]:
    layout = governance._cell_layout(tmp_path)
    profile = tmp_path / "profile"
    legacy = tmp_path / "legacy"
    profile.mkdir()
    legacy.mkdir()
    environment = governance._profile_environment(profile)
    if namespaced:
        governance._activation(layout, environment=environment)
        governance._write_json(
            profile / ".copilot-extensions" / "installation-mode.json",
            governance._policy(True, str(layout["marketplace_id"])),
        )
    if pointer_exists:
        pointer = Path(layout["durable"]) / governance.PLUGIN_ID / "runtime-root"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("retained-foreign-pointer\n", encoding="utf-8")
    return layout, profile, legacy


def _guard_mutation(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []

    def refuse_mutation(*args, **kwargs):
        attempts.append("mutation")
        raise AssertionError("diagnostic consumer entered lifecycle mutation")

    for name in (
        "_atomic_write_json", "_atomic_write_text", "stamp_context",
        "compare_and_swap_activation", "_DirectoryLock",
    ):
        monkeypatch.setattr(module, name, refuse_mutation)
    return attempts


@pytest.mark.parametrize("copy", PYTHON_COPIES, ids=lambda path: path.relative_to(ROOT).as_posix())
@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("pointer_exists", [False, True])
def test_derived_diagnostic_api_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    copy: Path, namespaced: bool, pointer_exists: bool,
) -> None:
    primitive = _load(copy, "diagnostic_derived_primitive", monkeypatch)
    layout, profile, legacy = _fixture(tmp_path, namespaced, pointer_exists)
    environment = governance._profile_environment(profile)
    arguments = governance._api_arguments(
        layout, profile, legacy,
        platform=environment["platform"], wsl_distro=environment["wslDistro"],
    )
    attempts = _guard_mutation(primitive, monkeypatch)
    before = _snapshot(tmp_path)
    result = primitive.resolve_installation_mode(**arguments)
    decision = primitive.probe_legacy_entrypoint(**arguments)
    assert result["actualMode"] == ("namespaced" if namespaced else "legacy"), result
    assert decision["allowMutation"] is (not namespaced)
    assert attempts == []
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("copy", PEER_COPIES, ids=lambda path: path.relative_to(ROOT).as_posix())
@pytest.mark.parametrize("pointer_exists", [False, True])
@pytest.mark.parametrize("activated", [False, True])
def test_peer_governance_wrapper_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    copy: Path, pointer_exists: bool, activated: bool,
) -> None:
    peer = _load(copy, "diagnostic_peer_wrapper", monkeypatch)
    primitive_path = (
        ROOT / "libs" / "installation-context" / "installation_context.py"
        if copy.parent.name == "peer-launch"
        else copy.with_name("_installation_context.py")
    )
    primitive = _load(primitive_path, "diagnostic_peer_primitive", monkeypatch)
    layout, profile, _legacy = _fixture(tmp_path, activated, pointer_exists)
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    assert peer.Path.home() == profile
    environment = {
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "WSL_DISTRO_NAME": os.environ.get("WSL_DISTRO_NAME", ""),
    }
    current_environment = primitive._current_environment

    def fixture_environment(**arguments):
        return current_environment(**{**arguments, "os_profile": profile})

    monkeypatch.setattr(primitive, "_current_environment", fixture_environment)
    attempts = _guard_mutation(primitive, monkeypatch)
    before = _snapshot(tmp_path)
    context = peer._active_context(
        primitive, Path(layout["install"]), Path(layout["durable"]),
        governance.PLUGIN_ID, Path(layout["cell"]), environment,
    )
    if activated:
        result = peer._governance(primitive, context, Path(layout["durable"]), environment)
        assert result["reason"] == "namespaced-active"
        assert result["runtimeRoot"] == str(layout["plugin_root"])
    else:
        with pytest.raises(ValueError, match="installation governance blocks invocation"):
            peer._governance(primitive, context, Path(layout["durable"]), environment)
    assert attempts == []
    assert _snapshot(tmp_path) == before
