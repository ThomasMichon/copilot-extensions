"""Authentication-directory inputs are preparation-scoped, not actor state."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "libs" / "installer-engine")]
import installer_dependency_policy as policy  # noqa: E402
import plugin_test_environment as preparation  # noqa: E402
from plugin_test_environment import isolated_environment, preparation_environment  # noqa: E402


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit"])
def test_auth_location_is_captured_before_isolation_and_not_given_to_actors(tmp_path, monkeypatch, explicit):
    from agent_procutil import owned_process

    original = tmp_path / "original"
    credentials = original / "credentials"
    base = {
        "HOME": str(original), "USERPROFILE": str(original), "LOCALAPPDATA": str(original),
        "AGENT_BRIDGE_CONFIG_DIR": str(original / "service"),
        "COPILOT_EXTENSIONS_CONTEXT": "synthetic-participant",
        "GH_TOKEN": "synthetic-not-a-real-token",
        "UV_KEYRING_PROVIDER": "subprocess",
        "UV_INDEX_FIXTURE_PASSWORD": "synthetic-index-password",
    }
    if explicit:
        base["UV_CREDENTIALS_DIR"] = str(credentials)
    monkeypatch.setattr(policy, "_select_uv_executable", lambda uv: "selected-uv")
    calls = []

    def metadata(argv, **options):
        calls.append(argv)
        assert argv == ["selected-uv", "auth", "dir"]
        assert options["env"]["HOME"] == str(original)
        assert not {"GH_TOKEN", "UV_INDEX_FIXTURE_PASSWORD", "COPILOT_EXTENSIONS_CONTEXT",
                    "AGENT_BRIDGE_CONFIG_DIR"} & options["env"].keys()
        assert options["binary"] and options["capture"]
        assert options["timeout"] == 15
        assert options["max_stdout_bytes"] == 32768
        assert options["max_stderr_bytes"] == 16384
        assert options["budget"].cleanup_reserve == 5
        return subprocess.CompletedProcess(argv, 0, (str(credentials) + "\r\n").encode(), b"")

    monkeypatch.setattr(owned_process, "run_caller_owned", metadata)
    project = tmp_path / "project"
    project.mkdir()
    prepared = preparation_environment(base, tmp_path / "prepared", project, uv="caller-uv")
    assert len(calls) == 1
    assert prepared["UV_CREDENTIALS_DIR"] == str(credentials)
    assert prepared["UV_KEYRING_PROVIDER"] == "subprocess"
    assert prepared["UV_INDEX_FIXTURE_PASSWORD"] == "synthetic-index-password"
    assert "GH_TOKEN" not in prepared and "COPILOT_EXTENSIONS_CONTEXT" not in prepared
    assert Path(prepared["HOME"]).is_relative_to(tmp_path / "prepared")
    assert Path(prepared["AGENT_BRIDGE_CONFIG_DIR"]).is_relative_to(tmp_path / "prepared")
    actor_base = dict(prepared)
    if os.name == "nt":
        actor_base["uv_credentials_dir"] = str(original / "lowercase-auth")
        actor_base["Uv_CrEdEnTiAlS_DiR"] = str(original / "mixedcase-auth")
    untouched = dict(actor_base)
    for host_state in (False, True):
        actor = isolated_environment(
            actor_base, tmp_path / f"actor-{host_state}",
            allow_explicit_tiers=host_state, allow_host_state=host_state,
        )
        assert Path(actor["UV_CREDENTIALS_DIR"]).is_relative_to(tmp_path / f"actor-{host_state}")
        assert actor["UV_CREDENTIALS_DIR"] != prepared["UV_CREDENTIALS_DIR"]
        if os.name == "nt":
            assert [name for name in actor if name.casefold() == "uv_credentials_dir"] == ["UV_CREDENTIALS_DIR"]
    assert actor_base == untouched


@pytest.mark.parametrize("failure", [
    "unsupported", "empty", "relative", "multiple", "nul", "utf8", "overflow",
    "timeout", "text-output", "explicit-mismatch", "file", "stderr-overflow",
])
def test_unknown_auth_directory_metadata_refuses_without_echoing_inputs(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(policy, "_select_uv_executable", lambda uv: "selected-uv")
    directory = tmp_path / "auth"
    base = {"HOME": str(tmp_path), "GH_TOKEN": "synthetic-private-value"}
    output = (str(directory) + "\n").encode()
    code = 0
    if failure == "unsupported":
        code = 2
    elif failure == "empty":
        output = b""
    elif failure == "relative":
        output = b"relative/path\n"
    elif failure == "multiple":
        output += output
    elif failure == "nul":
        output += b"\x00"
    elif failure == "utf8":
        output = b"\xff"
    elif failure == "overflow":
        output = b"x" * 32769
    elif failure == "text-output":
        output = str(directory)
    elif failure == "explicit-mismatch":
        base["UV_CREDENTIALS_DIR"] = str(tmp_path / "another")
    elif failure == "file":
        directory.write_text("synthetic fixture", encoding="utf-8")

    def metadata(argv, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 15, output="synthetic-private-value")
        stderr = b"x" * 16385 if failure == "stderr-overflow" else b"synthetic-private-value"
        return subprocess.CompletedProcess(argv, code, output, stderr)

    with pytest.raises(policy.DependencyPolicyError, match="uv auth dir") as caught:
        preparation.capture_preparation_auth("fixture-uv", base, tmp_path, run_metadata=metadata)
    assert "synthetic-private-value" not in str(caught.value)


def test_unsupported_auth_metadata_prevents_install_preparation(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("auth_preparation_runner", ROOT / "tools/run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runner)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "cache")
    calls = []
    monkeypatch.setattr(runner, "_prepare_venv", lambda *args, **kwargs: calls.append("install"))

    def unsupported(*args, **kwargs):
        raise policy.DependencyPolicyError("selected UV lacks uv auth dir metadata")

    monkeypatch.setattr(preparation, "capture_preparation_auth", unsupported)
    with pytest.raises(policy.DependencyPolicyError, match="uv auth dir"):
        runner._ensure_venv("fixture", "fixture-uv", reinstall=False)
    assert calls == []


def test_auth_metadata_unconfirmed_cleanup_retains_owned_job(tmp_path, monkeypatch):
    from agent_procutil import owned_process
    from plugin_test_containment import UnreapedProcessError

    monkeypatch.setattr(policy, "_select_uv_executable", lambda uv: "selected-uv")
    job = object()
    failure = owned_process.OwnedCleanupError(None, job=job)

    def unsettled(*args, **kwargs):
        raise failure

    monkeypatch.setattr(owned_process, "run_caller_owned", unsettled)
    with pytest.raises(UnreapedProcessError) as caught:
        preparation_environment({}, tmp_path, tmp_path, uv="fixture-uv")
    assert caught.value.job is job
    assert caught.value.__cause__ is failure


@pytest.mark.skipif(os.environ.get("COPILOT_EXTENSIONS_UV_AUTH_QUALIFICATION") != "1",
                    reason="explicit native synthetic UV authentication qualification")
@pytest.mark.portfolio_tier("T2")
def test_selected_uv_default_and_explicit_store_survive_only_preparation(tmp_path, record_property):
    uv = shutil.which("uv")
    assert uv
    project = tmp_path / "project"
    project.mkdir()
    minimal = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
               if key in os.environ}
    original = isolated_environment(minimal, tmp_path / "original")
    original.pop("UV_CREDENTIALS_DIR")
    original["UV_OFFLINE"] = "1"

    def native(arguments, env):
        return subprocess.run(
            [uv, *arguments], env=env, cwd=project, capture_output=True, text=True,
            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    record_property("selected-uv", native(["--version"], original).stdout.strip())
    auth_dir = native(["auth", "dir"], original)
    assert auth_dir.returncode == 0
    directory = Path(auth_dir.stdout.strip()).resolve()
    assert directory.is_relative_to(tmp_path.resolve())
    secret = "fixture-only-not-a-real-secret"
    service = "https://fixture.invalid"
    login = native(["auth", "login", service, "--username", "fixture", "--password", secret], original)
    assert login.returncode == 0
    for explicit in (False, True):
        base = dict(original)
        if explicit:
            base["UV_CREDENTIALS_DIR"] = str(directory)
        prepared = preparation_environment(base, tmp_path / f"prepared-{explicit}", project, uv=uv)
        assert prepared["UV_CREDENTIALS_DIR"] == str(directory)
        assert Path(prepared["HOME"]).is_relative_to(tmp_path / f"prepared-{explicit}")
        token = native(["auth", "token", service, "--username", "fixture"], prepared)
        assert token.returncode == 0 and token.stdout.strip() == secret
        actor = isolated_environment(prepared, tmp_path / f"actor-{explicit}")
        assert Path(actor["UV_CREDENTIALS_DIR"]).is_relative_to(tmp_path / f"actor-{explicit}")
        absent = native(["auth", "token", service, "--username", "fixture"], actor)
        assert absent.returncode != 0
