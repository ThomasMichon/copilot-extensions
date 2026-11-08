"""Prepared startup must not substitute incumbent health or source imports."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from agent_procutil import no_window_kwargs
from agent_vault import cli, configuration, launch, service_generation, source_start
import pytest


@pytest.fixture
def application(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    slot = root / "versions" / "1.0.0+source.example.profile.example"
    prefix = slot / ".venv"
    package = prefix / "site-packages" / "agent_vault" / "source_start.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    helper = slot / ".runtime-bootstrap" / "versioned_runtime.py"
    helper.parent.mkdir()
    helper.write_text(
        "def is_complete(root, generation):\n"
        "    return (root / 'versions' / generation / 'fixture-complete').is_file()\n",
        encoding="utf-8",
    )
    (slot / "fixture-complete").touch()
    (root / "current-version").write_text(slot.name + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(source_start, "__file__", str(package))
    monkeypatch.setenv("AGENT_VAULT_HOME", str(root))
    monkeypatch.setattr(configuration, "runtime_enabled", lambda: True)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("ordinary ensure is not currentness"))
    monkeypatch.setattr(cli, "send_command", lambda *_a: pytest.fail("ordinary ping is not currentness"))
    return root, slot, prefix, helper


@pytest.mark.parametrize("ready", (False, True))
def test_cli_dispatches_prepared_start_to_selected_generation(application, monkeypatch, capsys, ready):
    calls = []
    monkeypatch.setattr(service_generation, "ensure_selected", lambda: calls.append(True) or ready)
    assert cli.main(["__source-start"]) == (0 if ready else 1)
    assert calls == [True]
    if not ready:
        assert "not ready for credential work" in capsys.readouterr().err


def test_inactive_application_never_starts(application, monkeypatch, capsys):
    monkeypatch.setattr(configuration, "runtime_enabled", lambda: False)
    monkeypatch.setattr(service_generation, "ensure_selected", lambda: pytest.fail("inactive startup"))
    assert cli.main(["__source-start"]) == 2
    assert "inactive" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ("source", "root", "selection", "incomplete", "helper"))
def test_unqualified_application_never_ensures(application, monkeypatch, capsys, failure):
    root, slot, prefix, helper = application
    if failure == "source":
        monkeypatch.setattr(source_start, "__file__", str(Path(__file__)))
    elif failure == "root":
        other = root.parent / "other"
        other.mkdir()
        monkeypatch.setenv("AGENT_VAULT_HOME", str(other))
    elif failure == "selection":
        (root / "current-version").write_text("another-build\n", encoding="utf-8")
    elif failure == "incomplete":
        (slot / "fixture-complete").unlink()
    else:
        helper.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(service_generation, "ensure_selected", lambda: pytest.fail("unqualified startup"))
    assert cli.main(["__source-start"]) == 1
    assert "application startup failed" in capsys.readouterr().err


def test_reselection_during_ensure_is_not_success(application, monkeypatch, capsys):
    root, _slot, _prefix, _helper = application

    def ensure():
        (root / "current-version").write_text("another-build\n", encoding="utf-8")
        return True

    monkeypatch.setattr(service_generation, "ensure_selected", ensure)
    assert cli.main(["__source-start"]) == 1
    assert "application startup failed" in capsys.readouterr().err


def test_callback_rejects_unknown_arguments_before_dispatch(application, monkeypatch):
    monkeypatch.setattr(service_generation, "ensure_selected", lambda: pytest.fail("invalid arguments"))
    with pytest.raises(SystemExit) as error:
        cli.main(["__source-start", "--unexpected"])
    assert error.value.code == 2


def test_existing_generation_ensure_does_not_accept_incumbent_health(
    application, monkeypatch,
):
    from agent_vault import service

    root, slot, prefix, _helper = application
    monkeypatch.setattr(service_generation, "PROCESS_SLOT", slot)
    monkeypatch.setattr(service_generation, "PROCESS_GENERATION", slot.name)
    monkeypatch.setattr(service, "run_dir", lambda: root / "run")
    monkeypatch.setattr(service_generation, "read_endpoint", lambda _: object())
    responses = iter(("incumbent", slot.name))
    monkeypatch.setattr(service_generation, "endpoint_health", lambda _: {
        "ok": True, "pid": 42, "generation_protocol": 1,
        "runtime_generation": next(responses), "accepting_work": True,
    })
    calls = []
    monkeypatch.setattr(launch, "spawn_service", lambda args: calls.append(args))
    assert cli.main(["__source-start"]) == 0
    assert calls == [["--foreground", "--persistent"]]


def test_actual_cli_rejects_source_callback_without_starting(tmp_path):
    paths = [path for path in sys.path if path]
    code = (
        f"import sys; sys.path[:0]={json.dumps(paths)}\n"
        "from agent_vault import launch\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('source callback attempted a service launch')\n"
        "launch.spawn_service=forbidden\n"
        "from agent_vault.__main__ import main\n"
        "raise SystemExit(main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, "__source-start"],
        cwd=tmp_path, env={**os.environ, "AGENT_VAULT_HOME": str(tmp_path / "unused")},
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "application startup failed" in result.stderr
    assert "invalid choice" not in result.stderr
    assert not (tmp_path / "unused").exists()
