"""Keep mutable fixtures out of source retained by concurrent installer checks."""

import json
from pathlib import Path
import shutil

import pytest

from update_driver.foundation_inputs import PACKAGES, SUPPORT
from update_driver import first_use as provision


@pytest.fixture
def area(tmp_path_factory):
    return tmp_path_factory.mktemp("ud")


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
def owning_payload(area, monkeypatch):
    library = Path(__file__).resolve().parents[2]
    profile = area / "profile"
    profile.mkdir()
    project = area / "project"
    project.mkdir()
    marketplace = area / "market"
    payload = marketplace / "plugins" / "agent-vault"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    catalog = marketplace / ".github" / "plugin" / "marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "name": "fixture-market", "plugins": [
            {"name": "agent-vault", "source": "./plugins/agent-vault"},
        ],
    }), encoding="utf-8")
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
        "param([string]$Action,[string]$InstallDir)\n"
        "if ($Action -cne 'provision') { exit 90 }\n"
        "if ($env:UPDATE_DRIVER_WITNESS -or $env:COPILOT_EXTENSIONS_COMMAND_LIFECYCLE) { exit 91 }\n"
        "[IO.Directory]::CreateDirectory($InstallDir) | Out-Null\n"
        "[IO.File]::AppendAllText((Join-Path $InstallDir 'executed'), 'provision')\n"
        "[IO.File]::WriteAllText((Join-Path $InstallDir 'source-path'), $PSScriptRoot)\n"
        "[IO.File]::AppendAllText((Join-Path $InstallDir 'downgrade-history'), "
        "('[' + [string]$env:AGENT_VAULT_ALLOW_DOWNGRADE + ']' + [Environment]::NewLine))\n"
        "exit 7\n", encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '#!/usr/bin/env bash\nset -eu\n[[ "$1" == provision && "$2" == --install-dir ]]\n'
        '[[ -z "${UPDATE_DRIVER_WITNESS:-}" && -z "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}" ]]\n'
        'mkdir -p -- "$3"\nprintf provision >>"$3/executed"\n'
        'dirname -- "${BASH_SOURCE[0]}" >"$3/source-path"\n'
        'printf "[%s]\\n" "${AGENT_VAULT_ALLOW_DOWNGRADE:-}" >>"$3/downgrade-history"\n'
        'exit 7\n',
        encoding="utf-8",
    )
    for key in (*provision.CONTEXT_KEYS, "AGENT_VAULT_VALIDATED_CONTEXT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    return payload, profile / ".agent-vault", project
