"""Keep mutable fixtures out of source retained by concurrent installer checks."""

import json
import os
from pathlib import Path
import shutil
import time

import pytest

from update_driver.foundation_inputs import PACKAGES, SUPPORT
from update_driver import first_use as provision


@pytest.fixture
def area(tmp_path_factory):
    return tmp_path_factory.mktemp("ud")


@pytest.fixture
def rename_source():
    def rename(source, destination):
        for attempt in range(5):
            try:
                return source.rename(destination)
            except OSError as error:
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                    raise
                time.sleep(.05 * (attempt + 1))

    return rename


@pytest.fixture
def native_window_observation():
    if os.name != "nt":
        yield
        return
    from test_foundation_genesis_process import window_observer

    with window_observer(Path(__file__).resolve().parents[2]) as observation:
        yield
        assert observation.surfaced == {}
        assert observation.focus_changes == {}


@pytest.fixture
def native_source(area):
    common = area / "source"
    for directory, (name, dependencies) in PACKAGES.items():
        package = common / directory
        package.mkdir(parents=True)
        lines = [
            "[project]", f"name = {json.dumps(name)}", 'version = "1.0.0"',
            "dependencies = " + json.dumps(sorted(dependencies)), "[tool.uv.sources]",
        ]
        for dependency in sorted(dependencies):
            target = next(key for key, value in PACKAGES.items() if value[0] == dependency)
            lines.append(f'{dependency} = {{ path = "../{target}", editable = true }}')
        (package / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")
        (package / "source.py").write_text("pass\n", encoding="utf-8")
    plugin = common / "update-driver"
    (plugin / "scripts").mkdir()
    for name in SUPPORT:
        (plugin / "scripts" / name).write_text("# fixture\n", encoding="utf-8")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
    (plugin / "Z.py").write_text("Z\n", encoding="utf-8")
    (plugin / "a.py").write_text("a\n", encoding="utf-8")
    (plugin / "\ue000.py").write_text("bmp\n", encoding="utf-8")
    (plugin / "\U00010000.py").write_text("astral\n", encoding="utf-8")
    return plugin


@pytest.fixture
def owning_context(area, monkeypatch):
    profile = area / "profile"
    profile.mkdir()
    project = area / "project"
    project.mkdir()
    marketplace = area / "market"
    payload = marketplace / "plugins" / "agent-vault"
    payload.mkdir(parents=True)
    catalog = marketplace / ".github" / "plugin" / "marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "name": "fixture-market", "plugins": [
            {"name": "agent-vault", "source": "./plugins/agent-vault"},
        ],
    }), encoding="utf-8")
    for key in (
        *provision.CONTEXT_KEYS, *provision.PULL_REQUESTS_CONTEXT_KEYS,
        *provision.SSH_CONTEXT_KEYS,
        *provision.CODESPACES_CONTEXT_KEYS,
        *provision.LOGGER_CONTEXT_KEYS,
        *provision.BRIDGE_CONTEXT_KEYS,
        "AGENT_VAULT_VALIDATED_CONTEXT", "AGENT_PULL_REQUESTS_VALIDATED_CONTEXT",
        "AGENT_PULL_REQUESTS_ACTION", "AGENT_PULL_REQUESTS_FORCE",
        "AGENT_PULL_REQUESTS_INSTALL_DIR", "UPDATE_DRIVER_WITNESS",
        "AGENT_SSH_VALIDATED_CONTEXT", "AGENT_SSH_PAYLOAD_ROOT",
        "AGENT_SSH_ACTION", "AGENT_SSH_DRY_RUN",
        "AGENT_CODESPACES_VALIDATED_CONTEXT", "AGENT_CODESPACES_PAYLOAD_ROOT",
        "AGENT_CODESPACES_ACTION", "AGENT_CODESPACES_FORCE", "AGENT_CODESPACES_DRY_RUN",
        "AGENT_LOGGER_VALIDATED_CONTEXT", "AGENT_LOGGER_PAYLOAD_ROOT",
        "AGENT_LOGGER_ACTION", "AGENT_LOGGER_DRY_RUN",
        "AGENT_BRIDGE_VALIDATED_CONTEXT", "AGENT_BRIDGE_PAYLOAD_ROOT",
        "AGENT_BRIDGE_ACTION", "AGENT_BRIDGE_DRY_RUN", "AGENT_BRIDGE_FORCE",
        "AGENT_BRIDGE_CONFIG_DIR", "AGENT_BRIDGE_INSTALL_DIR",
        "COPILOT_PROJECT_DIR", "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("PSModuleAnalysisCachePath", str(profile / "module-analysis.cache"))
    return payload, profile / ".agent-vault", project


@pytest.fixture
def owning_payload(owning_context):
    library = Path(__file__).resolve().parents[2]
    payload, installation, project = owning_context
    scripts = payload / "scripts"
    scripts.mkdir()
    (payload / "plugin.json").write_text(
        '{"name":"agent-vault","version":"1.0.0"}', encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-vault"\nversion="1.0.0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    (payload / "payload-invocation.json").write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "version": 2,
        "command": "agent-vault", "installationContext": "required",
        "provisionMode": "direct", "installer": "install",
        "noSelfProvisionEnv": "AGENT_VAULT_NO_SELFPROVISION",
    }), encoding="utf-8")
    for source in (library / "update-driver" / "scripts").glob("*.py"):
        shutil.copyfile(source, scripts / source.name)
    shutil.copytree(
        library / "installation-context", scripts / "installation-context",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    (scripts / "install.ps1").write_text(
        "param([string]$Action,[string]$InstallDir,[switch]$NoService)\n"
        "if ($Action -cnotin @('provision', 'stamp')) { exit 90 }\n"
        "if ($env:UPDATE_DRIVER_WITNESS -or $env:COPILOT_EXTENSIONS_COMMAND_LIFECYCLE) { exit 91 }\n"
        "if ($env:COPILOT_PROJECT_DIR -or $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) { exit 93 }\n"
        "[IO.Directory]::CreateDirectory($InstallDir) | Out-Null\n"
        "[IO.File]::AppendAllText((Join-Path $InstallDir 'executed'), $Action)\n"
        "[IO.File]::WriteAllText((Join-Path $InstallDir 'source-path'), $PSScriptRoot)\n"
        "[IO.File]::WriteAllText((Join-Path $InstallDir 'project-path'), $PWD.Path)\n"
        "[IO.File]::AppendAllText((Join-Path $InstallDir 'client-only'), "
        "([string][int][bool]$NoService + [Environment]::NewLine))\n"
        "[IO.File]::AppendAllText((Join-Path $InstallDir 'downgrade-history'), "
        "('[' + [string]$env:AGENT_VAULT_ALLOW_DOWNGRADE + ']' + [Environment]::NewLine))\n"
        "exit 7\n", encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '#!/usr/bin/env bash\nset -eu\n[[ ( "$1" == provision || "$1" == stamp ) && "$2" == --install-dir ]]\n'
        '[[ -z "${UPDATE_DRIVER_WITNESS:-}" && -z "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}" ]]\n'
        '[[ -z "${COPILOT_PROJECT_DIR:-}" && -z "${COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT:-}" ]]\n'
        'mkdir -p -- "$3"\nprintf "%s" "$1" >>"$3/executed"\n'
        'dirname -- "${BASH_SOURCE[0]}" >"$3/source-path"\n'
        'printf "%s" "$PWD" >"$3/project-path"\n'
        'if [[ "${4:-}" == --no-service ]]; then echo 1; else echo 0; fi >>"$3/client-only"\n'
        'printf "[%s]\\n" "${AGENT_VAULT_ALLOW_DOWNGRADE:-}" >>"$3/downgrade-history"\n'
        'exit 7\n',
        encoding="utf-8",
    )
    return payload, installation, project


@pytest.fixture
def owning_pull_requests(owning_payload):
    payload, installation, project = owning_payload
    # Keep fixture inputs until framework cleanup; no directory-rename custody is needed.
    payload = Path(shutil.copytree(payload, payload.with_name("agent-pull-requests")))
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml"):
        path = payload / name
        path.write_text(
            path.read_text().replace("agent-vault", "agent-pull-requests").replace(
                "AGENT_VAULT", "AGENT_PULL_REQUESTS",
            ),
            encoding="utf-8",
        )
    catalog = payload.parents[1] / ".github" / "plugin" / "marketplace.json"
    catalog.write_text(
        catalog.read_text().replace("agent-vault", "agent-pull-requests"), encoding="utf-8",
    )
    scripts = payload / "scripts"
    windows = scripts / "install.ps1"
    windows.write_text(windows.read_text().replace(
        "[switch]$NoService", "[switch]$Unsupported"
    ).replace(
        "if ($Action", "if ($env:AGENT_PULL_REQUESTS_FORCE -or $env:AGENT_PULL_REQUESTS_ACTION "
        "-or $env:AGENT_PULL_REQUESTS_INSTALL_DIR) { exit 92 }\nif ($Action", 1,
    ).replace("[bool]$NoService", "[bool]$Unsupported"), encoding="utf-8")
    posix = scripts / "install.sh"
    posix.write_text(posix.read_text().replace(
        "mkdir -p", '[[ -z "${AGENT_PULL_REQUESTS_FORCE:-}" && '
        '-z "${AGENT_PULL_REQUESTS_ACTION:-}" && -z "${AGENT_PULL_REQUESTS_INSTALL_DIR:-}" ]]\n'
        '[[ "$#" == 3 ]]\nmkdir -p', 1,
    ), encoding="utf-8")
    installed = installation.parent / ".copilot" / "installed-plugins" / "fixture-market" / payload.name
    shutil.copytree(payload, installed)
    (installation.parent / ".copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "fixture-market": {"source": {"source": "github", "repo": "example/fixture-market"}},
        },
    }), encoding="utf-8")
    return installed, installation.with_name(".agent-pull-requests"), project


@pytest.fixture
def owning_ssh(owning_pull_requests):
    payload, installation, project = owning_pull_requests
    payload = Path(shutil.copytree(payload, payload.with_name("agent-ssh")))
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml"):
        path = payload / name
        path.write_text(
            path.read_text().replace("agent-pull-requests", "agent-ssh").replace(
                "AGENT_PULL_REQUESTS", "AGENT_SSH",
            ), encoding="utf-8",
        )
    path = payload / "payload-invocation.json"
    manifest = json.loads(path.read_text())
    del manifest["provisionMode"]
    manifest.update(
        module="agent_ssh", legacyRuntimeRoot=".agent-ssh",
        payloadRootEnv="AGENT_SSH_PAYLOAD_ROOT",
        payloadDispatcher={
            "posix": "scripts/runtime-gate.sh", "windows": "scripts/runtime-gate.ps1",
        },
    )
    path.write_text(json.dumps(manifest), encoding="utf-8")
    for name in ("install.ps1", "install.sh"):
        path = payload / "scripts" / name
        path.write_text(path.read_text().replace(
            "AGENT_PULL_REQUESTS", "AGENT_SSH",
        ).replace("AGENT_SSH_FORCE", "AGENT_SSH_DRY_RUN").replace(
            "AGENT_SSH_INSTALL_DIR", "AGENT_SSH_PAYLOAD_ROOT",
        ), encoding="utf-8")
    return payload, installation.with_name(".agent-ssh"), project


@pytest.fixture
def owning_codespaces(owning_ssh):
    payload, installation, project = owning_ssh
    payload = Path(shutil.copytree(payload, payload.with_name("agent-codespaces")))
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml",
                 "scripts/install.ps1", "scripts/install.sh"):
        path = payload / name
        path.write_text(
            path.read_text().replace("agent-ssh", "agent-codespaces").replace(
                "agent_ssh", "agent_codespaces",
            ).replace("AGENT_SSH", "AGENT_CODESPACES"),
            encoding="utf-8",
        )
    return payload, installation.with_name(".agent-codespaces"), project


@pytest.fixture
def owning_bridge(owning_codespaces):
    payload, installation, project = owning_codespaces
    payload = Path(shutil.copytree(payload, payload.with_name("agent-bridge")))
    for name in ("plugin.json", "pyproject.toml", "scripts/install.ps1", "scripts/install.sh"):
        path = payload / name
        path.write_text(path.read_text().replace(
            "agent-codespaces", "agent-bridge",
        ).replace("agent_codespaces", "agent_bridge").replace(
            "AGENT_CODESPACES", "AGENT_BRIDGE",
        ), encoding="utf-8")
    repository = Path(__file__).resolve().parents[3]
    shutil.copyfile(
        repository / "plugins/agent-bridge/payload-invocation.json",
        payload / "payload-invocation.json",
    )
    for name in provision.BRIDGE_HELPERS:
        path = payload / "scripts" / name
        if not path.exists():
            source = repository / (
                "libs/installer-engine" if name in ("installer-engine.ps1", "installer-engine.sh")
                else "plugins/agent-bridge/scripts"
            ) / name
            shutil.copyfile(source, path)
    return payload, installation.with_name(".agent-bridge"), project


@pytest.fixture
def owning_logger(owning_codespaces):
    payload, installation, project = owning_codespaces
    payload = Path(shutil.copytree(payload, payload.with_name("agent-logger")))
    for name in ("plugin.json", "pyproject.toml", "scripts/install.ps1", "scripts/install.sh"):
        path = payload / name
        path.write_text(path.read_text().replace(
            "agent-codespaces", "agent-logger",
        ).replace("agent_codespaces", "agent_logger").replace(
            "AGENT_CODESPACES", "AGENT_LOGGER",
        ), encoding="utf-8")
    windows = payload / "scripts/install.ps1"
    windows.write_text(windows.read_text().replace(
        "@('provision', 'stamp')", "@('provision', 'stamp', 'install')",
    ).replace(
        "$env:UPDATE_DRIVER_WITNESS -or $env:COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "$env:UPDATE_DRIVER_WITNESS -or -not $env:COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
    ), encoding="utf-8")
    posix = payload / "scripts/install.sh"
    posix.write_text(posix.read_text().replace(
        '"$1" == provision || "$1" == stamp', '"$1" == provision || "$1" == stamp || "$1" == install',
    ).replace(
        '-z "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}"',
        '-n "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}"',
    ), encoding="utf-8")
    repository = Path(__file__).resolve().parents[3]
    for name in ("command_binding.py", "command_lifecycle.py"):
        shutil.copyfile(repository / "libs/payload-invocation" / name, payload / "scripts" / name)
    shutil.copyfile(
        repository / "plugins/agent-logger/payload-invocation.json",
        payload / "payload-invocation.json",
    )
    return payload, installation.with_name(".agent-logger"), project


@pytest.fixture
def selected_logger_snapshot(owning_logger):
    from test_first_use import primitive

    payload, installation, project = owning_logger
    captured = primitive(payload).resolve_context(
        payload_root=payload, plugin_id="agent-logger", project_root=project,
    )
    snapshot = installation / "snapshots/1.0.0-retained"
    shutil.copytree(payload, snapshot)
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 2,
        "snapshotId": snapshot.name, "payloadVersion": "1.0.0", "resolution": captured,
    }), encoding="utf-8")
    (installation / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (installation / "stamped-version").write_text("1.0.0", encoding="utf-8")
    payload.rename(payload.with_name("unavailable-installed-payload"))
    return snapshot, installation, project
