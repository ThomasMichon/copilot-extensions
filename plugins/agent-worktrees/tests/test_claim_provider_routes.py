"""Registered callback prefixes cross the real child-process boundary intact."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import claim_providers as cp
from agent_worktrees import peer_launch_adapter as pla


@pytest.mark.parametrize("kind", ["status", "reclaim"])
def test_cell_callback_keeps_registered_prefix_and_child_context(tmp_path, monkeypatch, kind):
    cell = tmp_path / "marketplaces" / "fixture"
    owner = cell / "plugins" / "agent-worktrees"
    peer = cell / "plugins" / "agent-containers"
    peer.mkdir(parents=True)
    cwd = tmp_path / "producer cwd"
    cwd.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(owner / "install.json"))
    monkeypatch.setenv("CALLBACK_FIXTURE_CONTEXT", "retained")
    monkeypatch.setattr(pla, "validate_context", lambda: {
        "cellRoot": str(cell), "pluginRoot": str(owner),
    })
    monkeypatch.setattr(pla, "launch_prefix", lambda *args: [
        sys.executable, "-I", "-X", "utf8", "-c",
        "import json, os, sys; print(json.dumps({"
        "'exists': True, 'reclaimed': True, 'argv': sys.argv[1:], "
        "'cwd': os.getcwd(), 'context': os.environ['CALLBACK_FIXTURE_CONTEXT']}))",
    ])
    provider = cp.ClaimProviderManifest(
        namespace="raw-docker",
        plugin="agent-containers@fixture",
        plugin_root=str(peer),
        status_command=(str(peer / "bin" / "agent-containers"), "raw-docker", "--observe"),
        reclaim_command=(str(peer / "bin" / "agent-containers"), "raw-docker", "--cleanup"),
    )
    monkeypatch.setattr(cp, "discover_claim_providers", lambda *a: ({"raw-docker": provider}, ()))
    monkeypatch.setattr(cp, "_run_legacy_command", lambda *a, **k: pytest.fail("legacy downgrade"))
    callback = ("claim-status", "resource-123") if kind == "status" else (
        "claim-reclaim", "resource-123", "--apply",
    )
    result = cp._run_provider_process(
        provider, callback_args=callback, legacy_command=("must-not-run",),
        timeout=10, cwd=str(cwd), kind=kind,
    )
    assert result is not None and result.returncode == 0
    observed = json.loads(result.stdout)
    assert observed["argv"] == [
        "raw-docker", "--observe" if kind == "status" else "--cleanup", *callback,
    ]
    assert Path(observed["cwd"]) == cwd
    assert observed["context"] == "retained"

    if kind == "status":
        assert cp.resolve_claim_status("raw-docker:resource-123")["argv"] == observed["argv"]
    else:
        assert cp.resolve_claim_reclaim("raw-docker:resource-123", apply=True)["argv"] == observed["argv"]


@pytest.mark.parametrize("command", ["other-callback", "agent-containers.bat"])
def test_cell_callback_never_substitutes_another_registered_command(monkeypatch, command):
    monkeypatch.setattr(pla, "explicit_context", lambda: True)
    monkeypatch.setattr(pla, "run", lambda *a, **k: pytest.fail("module substituted"))
    monkeypatch.setattr(cp, "_run_legacy_command", lambda *a, **k: pytest.fail("legacy downgrade"))
    provider = cp.ClaimProviderManifest(
        namespace="raw-docker", plugin="agent-containers@fixture", plugin_root="/fixture",
        status_command=(command, "raw-docker"),
    )
    assert cp._run_provider_process(
        provider, callback_args=("claim-status", "id"), legacy_command=(command,),
        timeout=10,
    ) is None


@pytest.mark.parametrize("kind", ["status", "reclaim"])
def test_legacy_callback_keeps_prefix_and_suppresses_provisioning_only_in_child(
    tmp_path, monkeypatch, kind,
):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "wrong-caller-payload")
    monkeypatch.setenv("GH_TOKEN", "fixture-credential")
    monkeypatch.setenv("FIXTURE_NO_SELFPROVISION", "0")
    monkeypatch.setenv("CALLBACK_FIXTURE_CONTEXT", "retained")
    payload = tmp_path / "plugin payload"
    child = payload / "callback.py"
    child.parent.mkdir()
    child.write_text(
        "import json, os, sys\n"
        "blocked = os.environ.get('FIXTURE_NO_SELFPROVISION') == '1'\n"
        "if not blocked:\n"
        "    raise SystemExit('unexpected provisioning permission')\n"
        "print(json.dumps({'exists': True, 'reclaimed': True, 'argv': sys.argv[1:], "
        "'cwd': os.getcwd(), 'routing': [key for key in "
        "('GH_TOKEN', 'COPILOT_PLUGIN_ROOT', 'COPILOT_EXTENSIONS_CONTEXT') if key in os.environ], "
        "'context': os.environ['CALLBACK_FIXTURE_CONTEXT']}))\n",
        encoding="utf-8",
    )
    bin_dir = payload / "bin"
    bin_dir.mkdir()
    stub = bin_dir / ("fixture.cmd" if os.name == "nt" else "fixture")
    if os.name == "nt":
        # CMD needs an ordinary spelling of the same interpreter file.
        interpreter = sys.executable
        if interpreter.startswith("\\\\?\\UNC\\"):
            interpreter = "\\\\" + interpreter[8:]
        elif interpreter.startswith("\\\\?\\"):
            interpreter = interpreter[4:]
        assert os.path.samefile(interpreter, sys.executable)
        stub.write_text(
            "@echo off\r\n" + subprocess.list2cmdline([interpreter, "-I", str(child)]) + " %*\r\n",
            encoding="utf-8",
        )
    else:
        import shlex
        stub.write_text(
            "#!/bin/sh\nexec " + shlex.join([sys.executable, "-I", str(child)]) + ' "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o700)
    prefix = ("raw-docker", "--fixture-prefix", "prefix with spaces", "C:\\path with spaces\\")
    command = cp._resolve_command(("fixture", *prefix), root=payload)
    provider = cp.ClaimProviderManifest(
        namespace="raw-docker", plugin="fixture@fixture", plugin_root=str(payload),
        status_command=command, reclaim_command=command,
        no_self_provision_env="FIXTURE_NO_SELFPROVISION",
    )
    monkeypatch.setattr(cp, "discover_claim_providers", lambda *a: ({"raw-docker": provider}, ()))
    monkeypatch.setattr(pla, "run", lambda *a, **k: pytest.fail("cell launch in legacy mode"))
    monkeypatch.chdir(tmp_path)
    run_legacy = cp._run_legacy_command
    callbacks = []

    def observed_callback(*args, **kwargs):
        result = run_legacy(*args, **kwargs)
        callbacks.append(result)
        return result

    monkeypatch.setattr(cp, "_run_legacy_command", observed_callback)
    if kind == "status":
        observed = cp.resolve_claim_status("raw-docker:resource-123")
        suffix = ["claim-status", "resource-123"]
    else:
        observed = cp.resolve_claim_reclaim("raw-docker:resource-123", apply=False)
        suffix = ["claim-reclaim", "resource-123"]
    assert observed["available"] is True, [
        None if result is None else (result.returncode, result.stdout, result.stderr)
        for result in callbacks
    ]
    assert observed["argv"] == [*prefix, *suffix]
    assert Path(observed["cwd"]) == tmp_path
    assert observed["routing"] == []
    assert observed["context"] == "retained"
    assert os.environ["FIXTURE_NO_SELFPROVISION"] == "0"
    assert os.environ["GH_TOKEN"] == "fixture-credential"


@pytest.mark.parametrize("value", ["HOME", "GH_TOKEN", "X_NO_SELFPROVISION\n", "", 1, [], True])
def test_manifest_provisioning_suppression_rejects_other_environment_writes(value):
    with pytest.raises(cp.ManifestError, match="no_self_provision_env"):
        cp.parse_manifest({
            "schema_version": 1, "namespace": "raw-docker",
            "status_command": ["fixture"], "no_self_provision_env": value,
        })


@pytest.mark.parametrize("token", ["raw&other", "%EXPAND%", "raw!other", "raw\nother", '"raw"'])
def test_windows_callback_refuses_shell_significant_registered_prefix(tmp_path, monkeypatch, token):
    root = tmp_path / "fixture"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "fixture.cmd").write_text("@exit /b 0\r\n")
    monkeypatch.setattr(cp.os, "name", "nt")
    with pytest.raises(cp.TargetUnusableError, match="prefix"):
        cp._resolve_command(("fixture", token), root=root)
