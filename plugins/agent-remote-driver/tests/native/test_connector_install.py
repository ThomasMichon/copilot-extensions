"""Native plugin acquisition must retain the connector independently of its source."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil.owned_process import run_caller_owned
from agent_remote_driver_app import connector


ROOT = Path(__file__).resolve().parents[4]
IDENTITY = connector.IDENTITY


@pytest.mark.skipif(os.name != "nt", reason="physical Windows GUI interpreter contract")
def test_startup_uses_physical_venv_home_before_any_application_install(tmp_path: Path) -> None:
    root = tmp_path / "installed"
    slot = root / "versions" / "generation-one"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "libs/installer-engine/runtime_launcher.py", bootstrap / "runtime_launcher.py",
    )
    (bootstrap / "runtime-generation").write_text("a" * 32, encoding="ascii")
    (slot / ".install-complete.json").write_text("{}", encoding="ascii")
    marketplace = root / "connector-marketplace"
    marketplace.mkdir()
    connector.publish_startup(
        SimpleNamespace(root=root, slot=slot, python=Path(sys.executable)), marketplace,
    )
    binding = json.loads((marketplace / "service-binding.json").read_text())
    python = Path(binding["argv"][0])
    assert python.parent.samefile(sys.base_prefix)
    assert not python.samefile(Path(sys.executable).with_name("pythonw.exe"))
    result = run_caller_owned(
        [str(python), "-I", "-S", "-c",
         "import json,sys; print(json.dumps({'executable':sys.executable,'base':sys.base_prefix}))"],
        cwd=tmp_path, env=dict(os.environ), timeout=10,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed["executable"]).samefile(python)
    assert Path(observed["base"]).samefile(sys.base_prefix)


def test_native_connector_install_retains_payload_without_application_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select an existing native Copilot executable for plugin acquisition")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    home, workspace = (tmp_path / name for name in ("home", "workspace"))
    for path in (home, workspace):
        path.mkdir()
    root = tmp_path / "installed"
    slot = root / "versions" / "generation-one"
    source = slot / "_runtime_assets"
    source.mkdir(parents=True)
    binding = SimpleNamespace(root=root, slot=slot)
    settings = {"autoUpdate": False, "enabledPlugins": {"computer-use": False}}
    (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    resources = runpy.run_path(str(ROOT / "libs/versioned-runtime/standalone_build.py"))[
        "application_resources"
    ](ROOT / "plugins/agent-remote-driver", "agent_remote_driver_app")
    expected = {}
    for name, origin in resources.items():
        relative = name.removeprefix("_runtime_assets/")
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(origin.read_bytes())
        expected[relative] = destination.read_bytes()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "WORKTREE_", "OTEL_"))
        and key not in {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN"}
    }
    environment.update(
        COPILOT_HOME=str(home), COPILOT_CACHE_HOME=str(tmp_path / "cache"),
        COPILOT_OFFLINE="true",
    )
    deadline = time.monotonic() + 90
    monkeypatch.setattr(os, "environ", environment)

    def invoke(*arguments: str) -> str:
        result = run_caller_owned(
            [executable, "plugin", *arguments], cwd=workspace, env=environment,
            timeout=min(30, max(0.01, deadline - time.monotonic())),
            max_stdout_bytes=128 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert isinstance(result.stdout, str)
        return result.stdout

    observed = connector.install(
        binding, executable=executable, home=home, assets=source, deadline=deadline,
    )
    assert observed["installed"] and observed["userActivation"] == "true"
    retained = root / "connector-marketplace"
    inventory = json.loads(invoke("list", "--json"))
    selected = [item for item in inventory if item["name"] == "agent-remote-driver"]
    assert len(selected) == 1 and selected[0]["source"] == "live"
    assert Path(selected[0]["installedFrom"]).samefile(retained)
    persisted = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert persisted["extraKnownMarketplaces"][connector.MARKETPLACE] == {
        "source": {"source": "directory", "path": str(retained)},
    }
    shutil.rmtree(slot)
    inventory = json.loads(invoke("list", "--json"))
    selected = [item for item in inventory if item["name"] == "agent-remote-driver"]
    assert len(selected) == 1 and selected[0]["enabled"] is True
    payload = retained / "generations" / slot.name
    assert {name: (payload / name).read_bytes() for name in expected} == expected
    current = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert current["enabledPlugins"][IDENTITY] is True
    assert current["enabledPlugins"]["computer-use"] is False
    assert current["autoUpdate"] is False
    assert connector.withdraw(
        binding, executable=executable, home=home, deadline=deadline,
    ) == {"withdrawn": True, "assets_retained": True}
    inventory = json.loads(invoke("list", "--json"))
    assert not [item for item in inventory if item.get("marketplace") == connector.MARKETPLACE]
    current = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert connector.MARKETPLACE not in current.get("extraKnownMarketplaces", {})
    assert current["enabledPlugins"]["computer-use"] is False
    assert current["autoUpdate"] is False
    assert {name: (payload / name).read_bytes() for name in expected} == expected
    assert connector.withdraw(
        binding, executable=executable, home=home, deadline=deadline,
    ) == {"withdrawn": True, "assets_retained": True}
    assert {name: (payload / name).read_bytes() for name in expected} == expected
