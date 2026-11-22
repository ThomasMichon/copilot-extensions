"""Retain CodeSpaces' new driver graph when Windows stamps a source checkout."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import agent_procutil
import pytest
from packaging.version import Version

from test_first_use_codespaces import REPOSITORY, isolate_disabled_owner
from test_first_use_vault import cold_environment, native_genesis


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="POSIX legacy stamping intentionally retains the original payload pointer",
)
IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", "tests", "build", "dist", "*.egg-info",
)


@pytest.fixture
def checkout_snapshot(owning_codespaces, area):
    _installed, installation, project = owning_codespaces
    checkout = area / "checkout"
    payload = checkout / "plugins/agent-codespaces"
    canonical = REPOSITORY / "plugins/agent-codespaces"
    shutil.copytree(canonical, payload, ignore=IGNORE)
    shutil.copytree(
        REPOSITORY / "libs/installer-engine", checkout / "libs/installer-engine", ignore=IGNORE,
    )
    pending = [canonical]
    copied = set()
    while pending:
        source = pending.pop()
        if source != canonical:
            assert source.is_relative_to(REPOSITORY / "libs")
            if source in copied:
                continue
            shutil.copytree(source, checkout / source.relative_to(REPOSITORY), ignore=IGNORE)
            copied.add(source)
        declaration = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        for spec in declaration.get("tool", {}).get("uv", {}).get("sources", {}).values():
            if "path" in spec:
                pending.append((source / spec["path"]).resolve())
    catalog = checkout / ".github/plugin/marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "name": "fixture-market",
        "plugins": [{"name": "agent-codespaces", "source": "./plugins/agent-codespaces"}],
    }), encoding="utf-8")
    (installation.parent / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "fixture-market": {"source": {"source": "directory", "path": str(checkout)}},
        },
    }), encoding="utf-8")
    config = project / ".copilot-extensions/agent-codespaces/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("connection_owner:\n  enabled: false\n", encoding="utf-8")
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_CODESPACES_HOME"] = str(installation)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    env["COPILOT_PROJECT_DIR"] = str(project)
    # This is checkout packaging, not another Python-free genesis claim.
    executable = Path(sys._base_executable)
    uv = shutil.which("uv")
    assert uv, "normal native provisioning requires the existing governed UV executable"
    env["PATH"] = os.pathsep.join((str(executable.parent), str(Path(uv).parent), env["PATH"]))
    isolate_disabled_owner(installation, project, env)
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    stamp = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(payload / "scripts/install.ps1"), "stamp", "-InstallDir", str(installation)],
        env=env, cwd=project, capture_output=True, text=True, timeout=90,
        **agent_procutil.no_window_kwargs(),
    )
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    snapshot = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    checkout.rename(area / "unavailable-checkout")
    assert not payload.exists() and not checkout.exists()
    env["COPILOT_PLUGIN_ROOT"] = str(snapshot)
    return snapshot, installation, project, env, shell


def test_checkout_stamp_retains_complete_driver_graph_without_source(checkout_snapshot):
    snapshot, installation, project, env, _shell = checkout_snapshot
    probe = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; from pathlib import Path; "
         "root=Path(sys.argv[1]); sys.path.insert(0,str(root/'scripts')); "
         "import installer_dependency_policy as policy; "
         "assert policy._anchor_roots()==(root.resolve(),); "
         "policy._check_project(root,policy._anchor_roots(),set())",
         str(snapshot)],
        env=env, cwd=project, capture_output=True, text=True, timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    manifest = tomllib.loads((snapshot / "pyproject.toml").read_text())
    assert manifest["tool"]["uv"]["sources"]["agent-update-driver"] == {"path": "libs/update-driver"}
    assert manifest["tool"]["uv"]["sources"]["agent-repository-policy"] == {
        "path": "libs/repository-policy",
    }
    for name in ("update-driver", "agent-procutil", "single-instance-lease"):
        assert (snapshot / "libs" / name / "pyproject.toml").is_file()
    for suffix in ("ps1", "sh"):
        assert (snapshot / "libs/update-driver/scripts" / f"first-use.{suffix}").is_file()
    assert not (installation / "current-version").exists()


@native_genesis
def test_checkout_snapshot_provisions_real_installed_consumer_after_source_removal(
    checkout_snapshot, native_window_observation,
):
    snapshot, installation, project, env, shell = checkout_snapshot
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(snapshot / "scripts/install.ps1"), "provision", "-InstallDir", str(installation)],
        env=env, cwd=project, capture_output=True, text=True, timeout=850,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    assert (slot / ".install-complete.json").is_file()
    python = slot / "Scripts/python.exe"
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json,agent_codespaces,update_driver; from importlib.metadata import version; "
         "print(json.dumps({'codespaces':agent_codespaces.__file__,'driver':update_driver.__file__,"
         "'version':version('agent-codespaces')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    installed = json.loads(identity.stdout)
    assert Path(installed["codespaces"]).is_relative_to(slot)
    assert Path(installed["driver"]).is_relative_to(slot)
    assert installed["version"] == str(Version(selected))
    for args in (["version"], ["owner", "--status"]):
        consumer = subprocess.run(
            [str(python), "-I", "-B", "-m", "agent_codespaces", *args],
            env=env, cwd=project, capture_output=True, text=True, timeout=30,
            **agent_procutil.no_window_kwargs(),
        )
        assert consumer.returncode == 0, consumer.stdout + consumer.stderr
        if args[0] == "owner":
            assert json.loads(consumer.stdout)["enabled"] is False
    assert (installation.parent / "task-probes").read_text() == "agent-codespaces-owner"
