"""Retained-input cleanup must not replace the installation's primary failure."""
from __future__ import annotations

import importlib.util
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / "libs" / "installer-engine" / "installer_dependency_policy.py"
SPEC = importlib.util.spec_from_file_location("installer_dependency_cleanup_policy", PATH)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


@pytest.fixture
def cleanup_failure(monkeypatch, tmp_path):
    error = PermissionError("retained inputs remain locked")
    attempts = []

    class TemporaryInputs:
        name = str(tmp_path / "retained")

        def __init__(self, **kwargs):
            pass

        def cleanup(self):
            attempts.append(self.name)
            raise error

    monkeypatch.setattr(policy.tempfile, "TemporaryDirectory", TemporaryInputs)
    return error, attempts


@pytest.mark.parametrize("primary", [
    OSError("child could not start"),
    subprocess.CalledProcessError(17, ["uv", "pip", "install"], output="child output", stderr="child error"),
    policy.DependencyPolicyError("source admission refused"),
    KeyboardInterrupt(),
    SystemExit(23),
])
def test_active_failure_survives_exit_stack_cleanup(cleanup_failure, capsys, primary):
    with pytest.raises(type(primary)) as caught:
        with ExitStack() as retained:
            retained.enter_context(policy._temporary_inputs(None))
            raise primary
    assert caught.value is primary
    assert len(cleanup_failure[1]) == 1
    diagnostic = capsys.readouterr()
    assert diagnostic.out == ""
    assert diagnostic.err.count("retained-input-cleanup:") == 1
    assert "retained inputs remain locked" in diagnostic.err


def test_cleanup_only_failure_is_distinct_and_chained(cleanup_failure, capsys):
    with pytest.raises(policy.RetainedInputCleanupError) as caught:
        with policy._temporary_inputs(None):
            pass
    assert caught.value.__cause__ is cleanup_failure[0]
    assert len(cleanup_failure[1]) == 1
    assert capsys.readouterr().err == ""


def install_transaction(monkeypatch):
    @contextmanager
    def retain(*args, **kwargs):
        with policy._temporary_inputs(None):
            yield SimpleNamespace(argv=("uv", "pip", "install"), environment={}, cwd=Path.cwd())

    monkeypatch.setattr(policy, "retain_uv_install", retain)


@pytest.mark.parametrize("returncode", [1, 17, -9])
def test_nonzero_child_result_survives_cleanup(
    monkeypatch, cleanup_failure, capsys, returncode,
):
    install_transaction(monkeypatch)
    result = subprocess.CompletedProcess(["uv", "pip", "install"], returncode, "output", "error")
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: result)
    assert policy.run_uv_install("uv", []) is result
    assert len(cleanup_failure[1]) == 1
    diagnostic = capsys.readouterr()
    assert diagnostic.out == ""
    assert diagnostic.err.count("retained-input-cleanup:") == 1
    assert "retained inputs remain locked" in diagnostic.err


def test_check_true_preserves_exact_child_exception(monkeypatch, cleanup_failure, capsys):
    install_transaction(monkeypatch)
    primary = subprocess.CalledProcessError(17, ["uv"], output="output", stderr="error")

    def run(*args, **kwargs):
        assert kwargs["check"] is True
        raise primary

    monkeypatch.setattr(policy.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        policy.run_uv_install("uv", [], check=True)
    assert caught.value is primary
    assert caught.value.output == "output"
    assert caught.value.stderr == "error"
    assert capsys.readouterr().err.count("retained-input-cleanup:") == 1


def test_successful_child_cannot_hide_cleanup_failure(monkeypatch, cleanup_failure):
    install_transaction(monkeypatch)
    monkeypatch.setattr(
        policy.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(["uv"], 0),
    )
    with pytest.raises(policy.RetainedInputCleanupError) as caught:
        policy.run_uv_install("uv", [])
    assert caught.value.__cause__ is cleanup_failure[0]


@pytest.mark.parametrize("returncode", [0, 17])
def test_cli_distinguishes_cleanup_from_dependency_policy(
    monkeypatch, cleanup_failure, capsys, returncode,
):
    install_transaction(monkeypatch)
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(["uv"], returncode),
    )
    assert policy.main(["--uv", "uv", "--registry-only"]) == (returncode or 1)
    diagnostic = capsys.readouterr()
    assert diagnostic.out == ""
    assert diagnostic.err.count("retained-input-cleanup:") == 1
    assert "dependency-seasoning:" not in diagnostic.err


def test_normal_cleanup_preserves_success(monkeypatch, tmp_path, capsys):
    attempts = []

    class TemporaryInputs:
        name = str(tmp_path / "retained")

        def __init__(self, **kwargs):
            pass

        def cleanup(self):
            attempts.append(self.name)

    monkeypatch.setattr(policy.tempfile, "TemporaryDirectory", TemporaryInputs)
    install_transaction(monkeypatch)
    result = subprocess.CompletedProcess(["uv"], 0, "output", "error")
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: result)
    assert policy.run_uv_install("uv", []) is result
    assert attempts == [policy._native_retained_path(TemporaryInputs.name)]
    assert capsys.readouterr().err == ""
