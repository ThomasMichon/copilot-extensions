"""Native offline CLI flow stays separate from coordinator startup."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_dispatch import early_cli, telemetry


def test_imported_parser_exposes_same_configuration_contract(capsys):
    from agent_dispatch.__main__ import build_parser

    arguments = build_parser().parse_args(["telemetry", "capabilities"])
    assert arguments.func(arguments) == 0
    assert json.loads(capsys.readouterr().out)["configuration_only"]


def test_offline_configuration_does_not_start_coordinator(tmp_path, monkeypatch, capsys):
    root = tmp_path / "native"
    root.mkdir()
    directory = tmp_path / "spool"
    (directory / "retained").mkdir(parents=True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("configuration-only invocation activated telemetry")

    monkeypatch.setattr(telemetry, "make_retained_spool_sink", forbidden)
    monkeypatch.setattr(telemetry, "load_sink_from_config", forbidden)
    monkeypatch.setattr(telemetry._slot, "set_buffered", forbidden)
    arguments = ["telemetry", "retained", "--directory", str(directory)]
    assert early_cli.dispatch(arguments) == 0
    token = json.loads(capsys.readouterr().out)["confirmation"]
    assert list(root.iterdir()) == []
    assert early_cli.dispatch(arguments + ["--apply", "--confirm", token]) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured["status"] == "configured" and configured["activation"] == "not-requested"
    assert {item.name for item in root.iterdir()} == {"telemetry.json", "telemetry.json.lock"}
    assert list((directory / "retained").iterdir()) == []


def test_normal_cli_preview_apply_and_custom_refusal(tmp_path, monkeypatch):
    root = tmp_path / "native"
    root.mkdir()
    directory = tmp_path / "spool"
    (directory / "retained").mkdir(parents=True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    command = [sys.executable, "-m", "agent_dispatch", "telemetry"]

    def call(arguments):
        return subprocess.run(
            command + arguments, env=dict(os.environ), capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    support = call(["capabilities"])
    assert support.returncode == 0, support.stderr
    assert json.loads(support.stdout)["configuration_only"]
    arguments = ["retained", "--directory", str(directory)]
    result = call(arguments)
    assert result.returncode == 0, result.stderr
    assert not (root / "telemetry.json").exists()
    token = json.loads(result.stdout)["confirmation"]
    result = call(arguments + ["--apply", "--confirm", token])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "configured"
    assert json.loads((root / "telemetry.json").read_bytes()) == {
        "retained_spool": {"directory": str(directory)},
    }
    custom = '{"sink":"custom.module:factory"}'
    (root / "telemetry.json").write_text(custom, encoding="utf-8")
    result = call(arguments)
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "legacy-or-custom-sink-preserved"
    assert (root / "telemetry.json").read_text(encoding="utf-8") == custom
    assert list((directory / "retained").iterdir()) == []
    assert call(arguments + ["--unsupported"]).returncode == 2


@pytest.mark.parametrize("key", [
    "AGENT_DISPATCH_TASK_TOKEN", "AGENT_DISPATCH_TASK_ID", "AGENT_DISPATCH_TASK_URL",
    "AGENT_DISPATCH_TASK_WORKER_ID", "AGENT_DISPATCH_TASK_TOKEN_FILE",
])
def test_projected_task_retains_full_cli_admission(monkeypatch, key):
    monkeypatch.setenv(key, "")
    assert early_cli.dispatch(["telemetry", "capabilities"]) is None
    assert early_cli.dispatch([
        "telemetry", "retained", "--directory", "unused", "--apply", "--confirm", "unused",
    ]) is None


def test_normal_projected_task_cli_cannot_apply_configuration(tmp_path, monkeypatch, capsys):
    root = tmp_path / "native"
    root.mkdir()
    directory = tmp_path / "spool"
    (directory / "retained").mkdir(parents=True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    arguments = ["telemetry", "retained", "--directory", str(directory)]
    assert early_cli.dispatch(arguments) == 0
    token = json.loads(capsys.readouterr().out)["confirmation"]
    environment = dict(os.environ)
    environment.update({
        "AGENT_DISPATCH_TASK_TOKEN": "synthetic-task-credential",
        "AGENT_DISPATCH_TASK_ID": "synthetic-task",
        "AGENT_DISPATCH_TASK_URL": "http://127.0.0.1:1",
        "AGENT_DISPATCH_TASK_WORKER_ID": "synthetic-worker",
    })
    result = subprocess.run(
        [sys.executable, "-m", "agent_dispatch", *arguments, "--apply", "--confirm", token],
        env=environment, capture_output=True, text=True, timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 1, result.stderr
    assert "command is outside the projected task authority" in result.stderr
    assert result.stdout == ""
    assert list(root.iterdir()) == []
    assert list((directory / "retained").iterdir()) == []
