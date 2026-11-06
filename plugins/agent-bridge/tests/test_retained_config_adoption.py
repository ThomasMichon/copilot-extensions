"""Configuration-only adoption through the native Bridge command."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_bridge import telemetry, telemetry_cli
from process_telemetry.retained_config import preview
from single_instance_lease.operation import OperationLock


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    root = tmp_path / "native"
    root.mkdir()
    directory = tmp_path / "spool"
    (directory / "retained").mkdir(parents=True)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    return root / "telemetry.json", directory


def invoke(capsys, directory, *, apply=False, confirm=None):
    args = argparse.Namespace(directory=directory, apply=apply, confirm=confirm)
    with pytest.raises(SystemExit) as outcome:
        telemetry_cli.run(args)
    return outcome.value.code, json.loads(capsys.readouterr().out)


def test_preview_apply_and_idempotence_without_activation(configuration, monkeypatch, capsys):
    target, directory = configuration
    target.write_text('{"unrelated": {"preserve": true}}\n', encoding="utf-8")
    original = target.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("configuration-only command activated or provisioned a sink")

    monkeypatch.setattr(telemetry, "make_retained_spool_sink", forbidden)
    monkeypatch.setattr(telemetry, "load_sink_from_config", forbidden)
    monkeypatch.setattr(telemetry._slot, "set_buffered", forbidden)
    code, result = invoke(capsys, directory)
    assert code == 0 and result["would_change"]
    assert target.read_bytes() == original
    assert not target.with_suffix(".json.lock").exists()
    code, applied = invoke(capsys, directory, apply=True, confirm=result["confirmation"])
    assert code == 0 and applied["changed"]
    assert applied["activation"] == "not-requested"
    assert json.loads(target.read_bytes()) == {
        "unrelated": {"preserve": True}, "retained_spool": {"directory": str(directory)},
    }
    current = target.read_bytes()
    code, result = invoke(capsys, directory)
    assert code == 0 and not result["would_change"]
    code, applied = invoke(capsys, directory, apply=True, confirm=result["confirmation"])
    assert code == 0 and not applied["changed"]
    assert target.read_bytes() == current
    assert list((directory / "retained").iterdir()) == []


@pytest.mark.parametrize("document", [
    '{"sink": "custom.module:factory", "custom": true}',
    '{"spool": "legacy.jsonl"}',
    "different-retained",
])
def test_existing_modes_are_preserved(configuration, capsys, document):
    target, directory = configuration
    if document == "different-retained":
        document = json.dumps({"retained_spool": {"directory": str(directory / "other")}})
    target.write_text(document, encoding="utf-8")
    code, result = invoke(capsys, directory)
    assert code == 2 and result["status"] == "refused"
    assert result["confirmation"] is None
    code, result = invoke(capsys, directory, apply=True, confirm="unapproved")
    assert code == 2
    assert target.read_text(encoding="utf-8") == document


@pytest.mark.parametrize("document", [
    '{"sink": "first", "sink": "second"}', "[]", "{", '{"x": NaN}', '{"x": 1e999}',
    '{"retained_spool": {"unexpected": true}}',
])
def test_invalid_configuration_is_not_repaired(configuration, capsys, document):
    target, directory = configuration
    target.write_text(document, encoding="utf-8")
    code, result = invoke(capsys, directory)
    assert code == 2 and result["status"] == "error"
    assert target.read_text(encoding="utf-8") == document


def test_missing_namespace_is_not_provisioned(configuration, capsys):
    target, directory = configuration
    (directory / "retained").rmdir()
    code, result = invoke(capsys, directory)
    assert code == 2 and result["status"] == "error"
    assert not target.exists() and not (directory / "retained").exists()


@pytest.mark.parametrize("change", ["bytes", "target", "environment", "namespace"])
def test_confirmation_rejects_changed_inputs(configuration, monkeypatch, capsys, change):
    target, directory = configuration
    code, result = invoke(capsys, directory)
    assert code == 0
    if change == "bytes":
        target.write_text('{"another": true}', encoding="utf-8")
    elif change == "target":
        other = target.parent / "other"
        other.mkdir()
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(other))
    elif change == "environment":
        monkeypatch.setenv(telemetry.SINK_ENV_VAR, "custom.module:factory")
    else:
        (directory / "retained").rename(directory / "old")
        (directory / "retained").mkdir()
    before = target.read_bytes() if target.exists() else None
    code, result = invoke(capsys, directory, apply=True, confirm=result["confirmation"])
    assert code == 2
    assert (target.read_bytes() if target.exists() else None) == before


def test_write_boundary_rechecks_after_lock_acquisition(configuration, monkeypatch, capsys):
    target, directory = configuration
    _, result = invoke(capsys, directory)

    class ChangedLock(OperationLock):
        def __enter__(self):
            super().__enter__()
            target.write_text('{"sink": "custom.module:factory"}', encoding="utf-8")
            return self

    monkeypatch.setattr(telemetry_cli, "OperationLock", ChangedLock)
    code, result = invoke(capsys, directory, apply=True, confirm=result["confirmation"])
    assert code == 2
    assert json.loads(target.read_bytes()) == {"sink": "custom.module:factory"}


def test_native_writer_exclusion(configuration, capsys):
    target, directory = configuration
    _, result = invoke(capsys, directory)
    with OperationLock(target.with_name(target.name + ".lock")):
        code, result = invoke(capsys, directory, apply=True, confirm=result["confirmation"])
        assert code == 2
    assert not target.exists()


def test_linked_target_is_not_followed(configuration, tmp_path, capsys):
    target, directory = configuration
    other = tmp_path / "foreign.json"
    other.write_text("{}", encoding="utf-8")
    try:
        target.symlink_to(other)
    except OSError as error:
        pytest.skip(f"native symlink unavailable: {error}")
    code, result = invoke(capsys, directory)
    assert code == 2 and result["status"] == "error"
    assert other.read_text(encoding="utf-8") == "{}"


def test_normal_cli_cross_process_preview_and_apply(configuration):
    target, directory = configuration
    environment = dict(os.environ)
    command = [sys.executable, "-m", "agent_bridge", "telemetry"]

    def call(arguments):
        return subprocess.run(
            command + arguments, env=environment, capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    support = call(["capabilities"])
    assert support.returncode == 0, support.stderr
    assert json.loads(support.stdout)["configuration_only"]
    result = call(["retained", "--directory", str(directory)])
    assert result.returncode == 0, result.stderr
    assert not target.exists()
    token = json.loads(result.stdout)["confirmation"]
    result = call(["retained", "--directory", str(directory), "--apply", "--confirm", token])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "configured"
    assert json.loads(target.read_bytes())["retained_spool"]["directory"] == str(directory)
    rejected = call(["retained", "--directory", str(directory), "--apply", "--confirm", token])
    assert rejected.returncode == 2, rejected.stderr
    assert list((directory / "retained").iterdir()) == []
    unknown = call(["retained", "--directory", str(directory), "--unsupported"])
    assert unknown.returncode == 2


def test_dependency_free_preview_import():
    import process_telemetry

    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from process_telemetry.retained_config import preview",
         str(Path(process_telemetry.__file__).resolve().parents[1])],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr


def test_read_bound_and_relative_directory(configuration):
    target, directory = configuration
    target.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="read bound"):
        preview(target, directory, service="agent-bridge", environment_sink=None)
    target.unlink()
    with pytest.raises(ValueError, match="absolute"):
        preview(target, Path("relative"), service="agent-bridge", environment_sink=None)
