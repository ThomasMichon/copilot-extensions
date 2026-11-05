"""MCP's fixed recipes keep the owning init front and installation custody."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from update_driver import drain, status
from update_driver import first_use as provision

from test_first_use_native import native_command
from test_first_use import primitive


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native MCP recipes are qualified only on Windows and Linux",
)
REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.fixture
def owning_mcp(owning_codespaces, monkeypatch):
    payload, installation, project = owning_codespaces
    payload = Path(shutil.copytree(payload, payload.with_name("agent-mcp")))
    for name in ("plugin.json", "pyproject.toml", "scripts/install.ps1", "scripts/install.sh"):
        path = payload / name
        path.write_text(path.read_text().replace(
            "agent-codespaces", "agent-mcp",
        ).replace("AGENT_CODESPACES", "AGENT_MCP"), encoding="utf-8")
    shutil.copyfile(
        REPOSITORY / "plugins/agent-mcp/payload-invocation.json",
        payload / "payload-invocation.json",
    )
    scripts = payload / "scripts"
    for suffix in ("ps1", "sh"):
        (scripts / f"install.{suffix}").rename(scripts / f"init.{suffix}")
    for source in (REPOSITORY / "plugins/agent-mcp/scripts").iterdir():
        target = scripts / source.name
        if source.is_file() and not target.exists():
            shutil.copyfile(source, target)
    for suffix in ("ps1", "sh"):
        shutil.copyfile(
            REPOSITORY / f"libs/installer-engine/installer-engine.{suffix}",
            scripts / f"installer-engine.{suffix}",
        )
    for key in (
        "HOME", "INSTALLATION_ID", "NO_SELFPROVISION", "PINNED_CONTEXT", "ACTION",
        "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "MARKETPLACE_ROOT", "NO_CUTOVER",
        "CUTOVER_DRAIN_TIMEOUT", "CUTOVER_HEALTH_TIMEOUT", "NO_VERSION_REAP",
    ):
        monkeypatch.delenv("AGENT_MCP_" + key, raising=False)
    return payload, installation.with_name(".agent-mcp"), project


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_mcp_recipe_runs_own_init_under_same_queue_and_preserves_result(
    owning_mcp, area, monkeypatch, action,
):
    payload, installation, project = owning_mcp
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert request.id == repeated.id and not installation.exists()
    for key in ("ACTION", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION"):
        monkeypatch.setenv("AGENT_MCP_" + key, "foreign-worker-value")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert Path((installation / "project-path").read_text()) == project
    assert (installation / "client-only").read_text().strip() == "0"
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-mcp" and frozen.parent.parent == installation / ".install-stage"
    assert (frozen / "scripts/init.ps1").is_file()
    assert not (frozen / "scripts/install.ps1").exists()
    assert (frozen / "payload-invocation.json").read_bytes() == (
        payload / "payload-invocation.json"
    ).read_bytes()
    admission = json.loads(request.spec.source)
    assert admission["plugin"] == "agent-mcp" and admission["payload"] == str(payload)
    assert admission["runtime_root"] == str(installation) and admission["project"] == str(project)


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_mcp_native_admits_own_recipe_before_source_comparison(owning_mcp, area, action):
    payload, installation, project = owning_mcp
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


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "installer", "dispatcher", "copy", "custom-root", "cell", "managed", "no-self",
    "pinned", "nested", "missing-init", "missing-helper", "install", "compound", "client-only",
    "lifecycle", "cmd-catalog",
])
def test_mcp_refusal_precedes_acquisition_queue_and_publication(
    owning_mcp, area, monkeypatch, native, reason,
):
    payload, installation, project = owning_mcp
    action = "provision"
    if reason == "copy":
        payload = Path(shutil.copytree(payload, area / "copy/agent-mcp"))
    elif reason == "custom-root":
        installation = area / "other-mcp"
    elif reason in {"cell", "no-self", "pinned", "nested"}:
        monkeypatch.setenv({
            "cell": "AGENT_MCP_INSTALLATION_ID", "no-self": "AGENT_MCP_NO_SELFPROVISION",
            "pinned": "AGENT_MCP_PINNED_CONTEXT", "nested": "UPDATE_DRIVER_WITNESS",
        }[reason], "foreign")
    elif reason == "managed":
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-mcp",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        (installation / "runtime-deployment.json").write_text(json.dumps({
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        }), encoding="utf-8")
    elif reason in {"installer", "dispatcher", "lifecycle", "cmd-catalog"}:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "installer":
            manifest["installer"] = "install"
        elif reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/foreign.ps1"
        elif reason == "lifecycle":
            manifest["installerLifecycle"] = False
        else:
            manifest["windowsCatalogShim"] = "foreign"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif reason in {"missing-init", "missing-helper"}:
        (payload / "scripts" / (
            "init.ps1" if reason == "missing-init" else "installer_instance_reconcile.py"
        )).unlink()
    elif reason in {"install", "compound"}:
        action = "install" if reason == "install" else "stamp-provision"
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        command = native_command(root, payload, installation, project, client_only=reason == "client-only")
        command += ["-InstallerAction", action] if os.name == "nt" else ["--installer-action", action]
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(payload, installation, project, root=root,
                              client_only=reason == "client-only", installer_action=action)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source
