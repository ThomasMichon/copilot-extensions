"""Retained global connector assets and native activation ownership."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_remote_driver_app import connector
from plugin_activation import state
from plugin_activation.preservation import activation_custody, activation_preservation_pending


@pytest.fixture
def installation(tmp_path: Path) -> tuple[SimpleNamespace, Path, Path]:
    root = tmp_path / "installed"
    slot = root / "versions" / "first"
    assets = slot / "_runtime_assets"
    extension = assets / "extensions" / "agent-remote-driver"
    extension.mkdir(parents=True)
    (assets / "plugin.json").write_text(json.dumps({
        "name": "agent-remote-driver", "version": "0.1.0",
    }), encoding="utf-8")
    for name in ("extension.mjs", "sdk-compat.mjs"):
        (extension / name).write_bytes(b"// fixture\n")
    return SimpleNamespace(root=root, slot=slot), assets, tmp_path / "home"


@pytest.mark.parametrize("windows", [False, True])
def test_startup_binding_uses_canonical_retention_and_physical_windows_gui_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool,
) -> None:
    root = tmp_path / "installed"
    slot = root / "versions" / "generation-one"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    canonical = Path(__file__).resolve().parents[3] / "libs/installer-engine/runtime_launcher.py"
    shutil.copyfile(canonical, bootstrap / "runtime_launcher.py")
    (bootstrap / "runtime-generation").write_text("a" * 32, encoding="ascii")
    completion = b'{"fixture":true}\n'
    (slot / ".install-complete.json").write_bytes(completion)
    python = slot / ".venv" / ("Scripts/python.exe" if windows else "bin/python")
    physical = tmp_path / "base-python" / "python.exe"
    physical.parent.mkdir()
    (physical.parent / "pythonw.exe").touch()
    python.parent.mkdir(parents=True)
    (slot / ".venv" / "pyvenv.cfg").write_text(f"home = {physical.parent}\n", encoding="utf-8")
    monkeypatch.setattr(connector, "os", SimpleNamespace(name="nt" if windows else "posix"))
    monkeypatch.setattr(connector, "physical_executable", lambda selected: selected)
    marketplace = root / "connector-marketplace"
    marketplace.mkdir()
    connector.publish_startup(SimpleNamespace(root=root, slot=slot, python=python), marketplace)
    observed = json.loads((marketplace / "service-binding.json").read_text())
    assert observed["root"] == str(root) and observed["generation"] == slot.name
    expected = [
        str(physical.parent / "pythonw.exe") if windows else str(python),
        "-I", "-S", "-B", str(bootstrap / "runtime_launcher.py"),
        *(["--gui-runtime"] if windows else []),
        "--runtime-identity", "a" * 32 + ":" + hashlib.sha256(completion).hexdigest(),
        "-m", "agent_remote_driver_app", "service", "ensure",
    ]
    assert observed["argv"] == expected


def _read(home: Path, name: str) -> dict[str, Any]:
    return state.read_json_object(home / name)[1]


@pytest.mark.parametrize("activation", [None, False, True, "inventory-disabled"])
def test_native_install_preserves_explicit_activation_and_unrelated_settings(
    installation: tuple[SimpleNamespace, Path, Path], monkeypatch: pytest.MonkeyPatch,
    activation: bool | str | None,
) -> None:
    binding, assets, home = installation
    settings: dict[str, Any] = {
        "enabledPlugins": {"unrelated": False}, "unrelated-setting": [1],
    }
    if isinstance(activation, bool):
        settings["enabledPlugins"][connector.IDENTITY] = activation
    state.write_json_object_atomic(home / "settings.json", settings)
    if activation == "inventory-disabled":
        state.write_json_object_atomic(home / "config.json", {"installedPlugins": [{
            "name": "agent-remote-driver", "marketplace": connector.MARKETPLACE, "enabled": False,
        }]})
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        if argv[2] == "list":
            selected = {
                "name": "agent-remote-driver", "marketplace": connector.MARKETPLACE,
                "source": "live", "installedFrom": str(binding.root / "connector-marketplace"),
                "enabled": activation not in (False, "inventory-disabled"), "version": "0.1.0",
            }
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([selected]))
        current = _read(home, "settings.json")
        current["enabledPlugins"][connector.IDENTITY] = True
        if argv[2] == "marketplace":
            current["extraKnownMarketplaces"] = {connector.MARKETPLACE: {
                "source": {"source": "directory", "path": argv[-1]},
            }}
        else:
            state.write_json_object_atomic(home / "config.json", {"installedPlugins": [{
                "name": "agent-remote-driver", "marketplace": connector.MARKETPLACE,
                "enabled": True,
            }]})
        state.write_json_object_atomic(home / "settings.json", current)
        if kwargs["on_settled"] is not None:
            kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(connector, "run_caller_owned", run)
    observed = connector.install(
        binding, assets=assets, home=home, executable="fixture-cli",
        deadline=time.monotonic() + 5,
    )
    enabled = activation not in (False, "inventory-disabled")
    assert observed["installed"] and observed["inventoryEnabled"] is enabled
    assert [call[0][2] for call in calls][:-1] == (
        ["marketplace", "install", "enable"] if enabled else ["marketplace", "install"]
    )
    current = _read(home, "settings.json")
    assert current["enabledPlugins"]["unrelated"] is False
    assert current["unrelated-setting"] == [1]
    assert not activation_preservation_pending(home)
    assert all(call[1]["timeout"] > 0 for call in calls)
    assert calls[-1][1]["timeout"] <= calls[0][1]["timeout"]


def test_retention_outlives_original_slot_and_preserves_old_generation(
    installation: tuple[SimpleNamespace, Path, Path],
) -> None:
    binding, source, _ = installation
    first = connector.retain(binding, source)
    original = connector._assets(first / "generations" / "first")
    connector.retain(binding, source)
    second_slot = binding.slot.parent / "second"
    shutil.copytree(binding.slot, second_slot)
    second_source = second_slot / "_runtime_assets"
    (second_source / "extensions" / "agent-remote-driver" / "extension.mjs").write_bytes(b"// new\n")
    connector.retain(SimpleNamespace(root=binding.root, slot=second_slot), second_source)
    shutil.rmtree(binding.slot)
    shutil.rmtree(second_slot)
    assert connector._assets(first / "generations" / "first") == original
    assert connector._assets(first / "generations" / "second") != original
    catalog = _read(first / ".github" / "plugin", "marketplace.json")
    assert catalog["plugins"][0]["source"] == "generations/second"


def test_retention_refuses_changed_generation_without_replacing_live_assets(
    installation: tuple[SimpleNamespace, Path, Path],
) -> None:
    binding, source, _ = installation
    marketplace = connector.retain(binding, source)
    (source / "extensions" / "agent-remote-driver" / "extension.mjs").write_bytes(b"changed")
    with pytest.raises(ValueError, match="generation differs"):
        connector.retain(binding, source)
    assert (marketplace / "generations/first/extensions/agent-remote-driver/extension.mjs"
            ).read_bytes() == b"// fixture\n"


def test_foreign_marketplace_refuses_before_assets_or_cli(
    installation: tuple[SimpleNamespace, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding, source, home = installation
    state.write_json_object_atomic(home / "settings.json", {
        "extraKnownMarketplaces": {connector.MARKETPLACE: {
            "source": {"source": "github", "repo": "fixture/foreign"},
        }},
    })
    monkeypatch.setattr(connector, "retain", lambda *_: pytest.fail("foreign retention"))
    monkeypatch.setattr(connector, "run_caller_owned", lambda *_a, **_kw: pytest.fail("foreign CLI"))
    with pytest.raises(ValueError, match="different installation"):
        connector.install(
            binding, assets=source, home=home, executable="fixture-cli",
            deadline=time.monotonic() + 5,
        )


@pytest.mark.parametrize("settled", [False, True])
def test_failed_native_command_retains_original_activation_until_actual_settlement(
    installation: tuple[SimpleNamespace, Path, Path], monkeypatch: pytest.MonkeyPatch, settled: bool,
) -> None:
    binding, source, home = installation
    state.write_json_object_atomic(home / "settings.json", {
        "enabledPlugins": {connector.IDENTITY: False},
    })

    def run(argv: list[str], **kwargs: Any) -> None:
        state.write_json_object_atomic(home / "settings.json", {
            "enabledPlugins": {connector.IDENTITY: True},
        })
        if settled:
            kwargs["on_settled"]()
        raise TimeoutError("fixture command outcome")

    monkeypatch.setattr(connector, "run_caller_owned", run)
    with pytest.raises(TimeoutError, match="fixture command"):
        connector.install(
            binding, assets=source, home=home, executable="fixture-cli",
            deadline=time.monotonic() + 5,
        )
    assert activation_preservation_pending(home) is not settled
    assert _read(home, "settings.json")["enabledPlugins"][connector.IDENTITY] is not settled


def test_expired_setup_refuses_before_any_state_write(
    installation: tuple[SimpleNamespace, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding, source, home = installation
    monkeypatch.setattr(connector, "retain", lambda *_: pytest.fail("late retention"))
    with pytest.raises(TimeoutError, match="before launch"):
        connector.install(
            binding, assets=source, home=home, executable="fixture-cli",
            deadline=time.monotonic() - 1,
        )
    assert not home.exists() and not (binding.root / "connector-marketplace").exists()


def test_connector_failure_prevents_service_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_remote_driver_app as app
    from agent_remote_driver_app import service_lifecycle

    monkeypatch.setattr(service_lifecycle, "selected_source", lambda deadline: object())
    monkeypatch.setattr(service_lifecycle, "start", lambda *_a, **_kw: pytest.fail("unready start"))

    def failed(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("connector unavailable")

    monkeypatch.setattr(connector, "install", failed)
    with pytest.raises(RuntimeError, match="connector unavailable"):
        app.main(["__source-start"])


@pytest.mark.parametrize("damage", [
    "none", "custody", "physical-alias", "foreign", "other-plugin", "duplicate", "expired",
    "startup-owned", "startup-foreign", "startup-replaced", "startup-byte-bound",
    "startup-alias", "native-timeout", "uninstall-still-enabled",
])
def test_connector_withdrawal_uses_native_owner_and_preserves_retained_assets(
    installation: tuple[SimpleNamespace, Path, Path], monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    binding, assets, home = installation
    if damage in {"physical-alias", "startup-alias"} and os.name != "nt":
        pytest.skip("Windows extended path aliases")
    marketplace = connector.retain(binding, assets)
    original = connector._assets(marketplace / "generations" / binding.slot.name)
    registration = {
        "source": {"source": "directory", "path": str(marketplace)},
    }
    if damage == "foreign":
        registration["source"]["path"] = str(binding.root / "different-owner")
    elif damage == "physical-alias":
        registration["source"]["path"] = "\\\\?\\" + str(marketplace)
    state.write_json_object_atomic(home / "settings.json", {
        "enabledPlugins": {connector.IDENTITY: True, "unrelated": False},
        "extraKnownMarketplaces": {connector.MARKETPLACE: registration},
        "unrelated-setting": [1],
    })
    inventory = [{
        "name": "agent-remote-driver", "marketplace": connector.MARKETPLACE,
        "source": "live", "installedFrom": str(marketplace), "enabled": True,
    }]
    if damage == "physical-alias":
        inventory[0]["installedFrom"] = "\\\\?\\" + str(marketplace)
    if damage == "other-plugin":
        inventory.append({**inventory[0], "name": "another-plugin"})
    elif damage == "duplicate":
        inventory.append(dict(inventory[0]))
    startup = marketplace / "service-binding.json"
    if damage.startswith("startup-"):
        expected = {"root": str(binding.root), "generation": binding.slot.name, "argv": ["fixture"]}
        if damage == "startup-alias":
            bootstrap = binding.slot / ".runtime-bootstrap" / "runtime_launcher.py"
            bootstrap.parent.mkdir()
            bootstrap.write_bytes(b"# owned fixture\n")
            expected["argv"] = [sys.executable, "-I", "-S", "-B", str(bootstrap)]
        published = expected
        if damage == "startup-alias":
            published = {
                **expected, "root": "\\\\?\\" + str(binding.root),
                "argv": [
                    "\\\\?\\" + sys.executable, "-I", "-S", "-B", "\\\\?\\" + str(bootstrap),
                ],
            }
        state.write_json_object_atomic(startup, (
            {**expected, "generation": "different-owner"} if damage == "startup-foreign"
            else {**expected, "padding": "x" * 16384} if damage == "startup-byte-bound"
            else published
        ))

        def startup_record(_binding: Any) -> dict[str, Any]:
            if damage == "startup-replaced":
                startup.rename(binding.root / "original-startup.json")
                state.write_json_object_atomic(startup, expected)
            return expected

        monkeypatch.setattr(connector, "_startup_record", startup_record)
    calls = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv[2:])
        if argv[2] == "list":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(inventory))
        if argv[2] == "uninstall" and damage == "native-timeout":
            raise TimeoutError("fixture native outcome is unknown")
        if argv[2] == "uninstall" and damage == "uninstall-still-enabled":
            return subprocess.CompletedProcess(argv, 0, stdout="")
        current = _read(home, "settings.json")
        if argv[2:] == ["uninstall", connector.IDENTITY]:
            inventory[0]["enabled"] = False
            current["enabledPlugins"][connector.IDENTITY] = False
        else:
            assert argv[2:] == ["marketplace", "remove", connector.MARKETPLACE]
            assert inventory[0]["enabled"] is False
            inventory.clear()
            current["extraKnownMarketplaces"].pop(connector.MARKETPLACE)
        state.write_json_object_atomic(home / "settings.json", current)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(connector, "run_caller_owned", run)
    deadline = time.monotonic() + (-1 if damage == "expired" else 10)

    def withdraw() -> dict[str, bool]:
        if damage == "custody":
            with connector.withdrawal_custody(
                binding, home=home, executable="fixture-cli", deadline=deadline,
            ) as result:
                with pytest.raises(state.PluginStateError, match="custody refused"):
                    with activation_custody(home):
                        pytest.fail("another acquisition entered removal custody")
                return result
        return connector.withdraw(
            binding, home=home, executable="fixture-cli", deadline=deadline,
        )

    if damage not in ("none", "custody", "physical-alias", "startup-owned", "startup-alias"):
        before = (home / "settings.json").read_bytes()
        with pytest.raises((ValueError, RuntimeError, TimeoutError)):
            withdraw()
        assert (home / "settings.json").read_bytes() == before
        if damage in ("native-timeout", "uninstall-still-enabled"):
            assert calls.count(["uninstall", connector.IDENTITY]) == 1
            assert not [arguments for arguments in calls if arguments[0] == "marketplace"]
        else:
            assert all(arguments == ["list", "--json"] for arguments in calls)
    else:
        assert withdraw() == {
            "withdrawn": True, "assets_retained": True,
        }
        assert [arguments for arguments in calls if arguments[0] != "list"] == [
            ["uninstall", connector.IDENTITY],
            ["marketplace", "remove", connector.MARKETPLACE],
        ]
        current = _read(home, "settings.json")
        assert current["enabledPlugins"]["unrelated"] is False
        assert current["unrelated-setting"] == [1]
        assert connector.MARKETPLACE not in current["extraKnownMarketplaces"]
        assert not startup.exists()
        if damage == "custody":
            calls.clear()
            with connector.withdrawal_custody(
                binding, home=home, executable="fixture-cli", deadline=deadline, withdraw=False,
            ) as result:
                assert result == {"withdrawn": True, "assets_retained": True}
            assert calls == [["list", "--json"]]
            startup.write_bytes(b"new owner")
            with pytest.raises(RuntimeError, match="connector"):
                with connector.withdrawal_custody(
                    binding, home=home, executable="fixture-cli", deadline=deadline, withdraw=False,
                ):
                    pytest.fail("resumed removal accepted another startup owner")
    assert connector._assets(marketplace / "generations" / binding.slot.name) == original
