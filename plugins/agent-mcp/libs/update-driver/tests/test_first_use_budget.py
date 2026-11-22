"""Budget's V1 declaration enters the existing owner without a V2 migration."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, cast

import pytest

from agent_procutil import no_window_kwargs
from update_driver import ParticipantError, Request, drain, status
from update_driver import first_use as provision

from test_first_use import primitive as _primitive
from test_first_use_native import native_command as _native_command


primitive = cast(Callable[[Path], Any], _primitive)
native_command = cast(Callable[..., list[str]], _native_command)


@pytest.fixture
def owning_budget(
    owning_payload: tuple[Path, Path, Path], monkeypatch: Any,
) -> tuple[Path, Path, Path]:
    original, installation, project = owning_payload
    monkeypatch.setenv("COPILOT_HOME", str(installation.parent / ".copilot"))
    for key in (*provision.BUDGET_CONTEXT_KEYS, "BUDGET_GUIDANCE_ACTION",
                "BUDGET_GUIDANCE_FORCE", "BUDGET_GUIDANCE_INSTALL_DIR"):
        if key not in ("HOME", "USERPROFILE"):
            monkeypatch.delenv(key, raising=False)
    payload = installation.parent / ".copilot/installed-plugins/fixture-market/budget-guidance"
    shutil.copytree(original, payload)
    for name in ("plugin.json", "pyproject.toml", "scripts/install.ps1", "scripts/install.sh"):
        path = payload / name
        path.write_text(path.read_text().replace("agent-vault", "budget-guidance").replace(
            "AGENT_VAULT", "BUDGET_GUIDANCE",
        ).replace(
            "@('provision', 'stamp')", "@('provision', 'stamp', 'install')",
        ).replace(
            '"$1" == provision || "$1" == stamp',
            '"$1" == provision || "$1" == stamp || "$1" == install',
        ).replace(
            "$plugin -eq 'budget-guidance'", "$plugin -eq 'agent-vault'",
        ).replace(
            "if ($Action", "if ($env:BUDGET_GUIDANCE_ACTION -or $env:BUDGET_GUIDANCE_FORCE "
            "-or $env:BUDGET_GUIDANCE_INSTALL_DIR) { exit 92 }\nif ($Action", 1,
        ).replace(
            "mkdir -p", '[[ -z "${BUDGET_GUIDANCE_ACTION:-}" && '
            '-z "${BUDGET_GUIDANCE_FORCE:-}" && -z "${BUDGET_GUIDANCE_INSTALL_DIR:-}" ]]\n'
            "mkdir -p", 1,
        ), encoding="utf-8")
    repository = Path(__file__).resolve().parents[3]
    shutil.copyfile(repository / "plugins/budget-guidance/payload-invocation.json",
                    payload / "payload-invocation.json")
    (installation.parent / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "fixture-market": {"source": {"source": "github", "repo": "example/fixture-market"}},
        },
    }), encoding="utf-8")
    windows = payload / "scripts/install.ps1"
    windows.write_text(windows.read_text().replace(
        "exit 7", "[IO.File]::WriteAllText((Join-Path $InstallDir 'managed-python'), "
        "[string]$env:UV_PYTHON_INSTALL_DIR)\nexit 7",
    ), encoding="utf-8")
    posix = payload / "scripts/install.sh"
    posix.write_text(posix.read_text().replace(
        "exit 7", 'printf "%s" "${UV_PYTHON_INSTALL_DIR:-}" >"$3/managed-python"\nexit 7',
    ), encoding="utf-8")
    return payload, installation.with_name(".budget-guidance"), project


@pytest.mark.parametrize("action", ["stamp", "provision", "install"])
def test_budget_v1_fixed_action_retains_source_context_and_result(
    owning_budget: tuple[Path, Path, Path], area: Path, monkeypatch: Any, action: str,
) -> None:
    payload, installation, project = owning_budget
    root = area / "queue"
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(area / "managed-python"))
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert repeated.id == request.id and not installation.exists()
    assert dict(request.spec.context)["UV_PYTHON_INSTALL_DIR"] == str(area / "managed-python")
    for key in ("ACTION", "FORCE", "INSTALL_DIR"):
        monkeypatch.setenv("BUDGET_GUIDANCE_" + key, "foreign")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(area / "foreign-python"))
    drain(root=root)
    result = status(request.id, root=root)
    assert isinstance(result, Request)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert (installation / "managed-python").read_text() == str(area / "managed-python")
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "budget-guidance"
    assert frozen.parent.parent == installation / ".install-stage"
    assert (frozen / "payload-invocation.json").read_bytes() == (
        payload / "payload-invocation.json"
    ).read_bytes()


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "v2", "module", "launcher", "lifecycle", "missing-installer", "python-version",
    "compound", "no-self", "pinned",
    "managed", "copy", "custom-root",
])
def test_budget_refusal_precedes_queue_acquisition_and_installer(
    owning_budget: tuple[Path, Path, Path], area: Path, monkeypatch: Any,
    native: bool, reason: str,
) -> None:
    payload, installation, project = owning_budget
    action = "stamp-provision" if reason == "compound" else "install"
    manifest_path = payload / "payload-invocation.json"
    manifest = json.loads(manifest_path.read_text())
    if reason == "missing-installer":
        del manifest["installer"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif reason in ("v2", "module", "launcher", "lifecycle", "python-version"):
        key, value = {
            "v2": ("version", 2), "module": ("module", "foreign"),
            "launcher": ("pythonLauncher", "python"),
            "lifecycle": ("installerLifecycle", False),
            "python-version": ("bootstrapPython", "3.13"),
        }[reason]
        manifest[key] = value
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif reason in ("no-self", "pinned"):
        monkeypatch.setenv("BUDGET_GUIDANCE_" + {
            "no-self": "NO_SELFPROVISION", "pinned": "PINNED_CONTEXT",
        }[reason], "1")
    elif reason == "managed":
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="budget-guidance",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        (installation / "runtime-deployment.json").write_text(json.dumps({
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        }), encoding="utf-8")
    elif reason == "copy":
        payload = Path(shutil.copytree(payload, area / "foreign/budget-guidance"))
    elif reason == "custom-root":
        installation = area / "other-installation"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    root = area / "cold"
    if native:
        command = native_command(root, payload, installation, project)
        command += ["-InstallerAction", action] if os.name == "nt" else ["--installer-action", action]
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("action", ["stamp", "provision", "install"])
def test_budget_native_admits_v1_before_source_comparison(
    owning_budget: tuple[Path, Path, Path], area: Path, action: str,
) -> None:
    payload, installation, project = owning_budget
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    command += (
        ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64, "-InstallerAction", action]
        if os.name == "nt" else
        ["--run-owner", "sha256:" + "0" * 64, "--installer-action", action]
    )
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, result.stderr
    assert not root.exists() and not installation.exists()


def test_budget_changed_source_never_enters_installer(
    owning_budget: tuple[Path, Path, Path], area: Path,
) -> None:
    payload, installation, project = owning_budget
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="install")
    (payload / "scripts/install.sh").write_text("exit 99\n", encoding="utf-8")
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert isinstance(result, Request)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()
