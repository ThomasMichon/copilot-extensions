from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_machines import __main__ as cli
from agent_machines import cli_copilot as control


@pytest.fixture
def native(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[Path, Path, list[tuple[list[str], dict[str, Any]]]]:
    home = tmp_path / "copilot-home"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(
        json.dumps({"autoUpdate": False, "model": "existing-model", "footer": {"showQuota": True}}),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setenv("COPILOT_HOME", str(home))
    for name in control.OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(control, "_executable", lambda selected: selected or "fixture-copilot")
    monkeypatch.setattr(control, "_cache_directories", lambda: [cache])
    monkeypatch.setattr(control, "_user_overrides", lambda: {})

    def invoke(executable: str, arguments: list[str], **kwargs: Any) -> str:
        calls.append((arguments, kwargs))
        if arguments == ["config", "--help"]:
            return "Usage: copilot config [key] [value]"
        if arguments[:2] == ["config", "autoUpdate"]:
            value = json.loads(settings.read_text(encoding="utf-8"))
            value["autoUpdate"] = arguments[2] == "true"
            settings.write_text(json.dumps(value), encoding="utf-8")
            return "Set autoUpdate"
        return "GitHub Copilot CLI 1.0.100-1."

    monkeypatch.setattr(control, "_native", invoke)
    return settings, cache, calls


def cached(cache: Path, version: str = "1.0.100-1") -> Path:
    directory = cache / version
    directory.mkdir()
    (directory / ".extraction-complete").touch()
    (directory / "index.js").write_text("", encoding="utf-8")
    (directory / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return directory


def test_policy_preview_never_writes_or_invokes_native(
    native: tuple, capsys: pytest.CaptureFixture[str],
) -> None:
    settings, _, calls = native
    before = settings.read_bytes()
    assert cli.main(["copilot-cli", "auto-update", "on", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is False
    assert result["command"][-4:] == ["config", "autoUpdate", "true", "--global"]
    assert calls == [] and settings.read_bytes() == before


@pytest.mark.parametrize("state", ["on", "off"])
def test_live_toggle_uses_native_setting_not_environment_disable(
    native: tuple, capsys: pytest.CaptureFixture[str], state: str,
) -> None:
    settings, _, calls = native
    original = json.loads(settings.read_text())
    original["autoUpdate"] = state == "off"
    settings.write_text(json.dumps(original))
    assert cli.main(["copilot-cli", "auto-update", state, "--apply", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    written = json.loads(settings.read_text())
    assert written == {**original, "autoUpdate": state == "on"}
    assert calls[-1][0] == ["config", "autoUpdate", str(state == "on").lower(), "--global"]
    assert "COPILOT_AUTO_UPDATE" not in calls[-1][1]["environment"]
    assert result["declarative_policy_changed"] is False


def test_explicit_override_cleanup_is_after_native_readback_and_preserves_parent(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    settings, _, calls = native
    monkeypatch.setenv("COPILOT_AUTO_UPDATE", "false")
    monkeypatch.setenv("COPILOT_CLI_DIST_DIR", "old-package")
    observed = {"COPILOT_AUTO_UPDATE": "", "COPILOT_CLI_DIST_DIR": "old-package"}
    monkeypatch.setattr(control, "_user_overrides", lambda: observed)
    removed: list[str] = []

    def clear(expected: dict[str, str]) -> list[str]:
        assert json.loads(settings.read_text())["autoUpdate"] is True
        assert expected == observed
        removed.extend(expected)
        return list(expected)

    monkeypatch.setattr(control, "_clear_user_overrides", clear)
    assert cli.main([
        "copilot-cli", "auto-update", "on", "--apply", "--clear-overrides", "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert removed == list(observed)
    assert all(name not in calls[-1][1]["environment"] for name in control.OVERRIDES)
    assert result["inherited_overrides_still_in_parent"] == {
        "COPILOT_AUTO_UPDATE": "false", "COPILOT_CLI_DIST_DIR": "old-package",
    }
    assert os.environ["COPILOT_AUTO_UPDATE"] == "false"


@pytest.mark.parametrize("failure", ["unsupported", "native_failure", "readback"])
def test_configuration_failure_never_clears_overrides_or_replays(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    settings, _, _ = native
    calls: list[list[str]] = []
    monkeypatch.setattr(control, "_user_overrides", lambda: {"COPILOT_AUTO_UPDATE": "false"})
    monkeypatch.setattr(
        control, "_clear_user_overrides",
        lambda expected: pytest.fail("failed configuration must not clear user overrides"),
    )

    def invoke(executable: str, arguments: list[str], **kwargs: Any) -> str:
        calls.append(arguments)
        if arguments == ["config", "--help"]:
            if failure == "unsupported":
                raise control.CopilotControlError("Invalid command format")
            return "Usage: copilot config"
        if failure == "native_failure":
            raise control.CopilotControlError("write interrupted", effects="unknown")
        return "Set autoUpdate"

    monkeypatch.setattr(control, "_native", invoke)
    assert cli.main([
        "copilot-cli", "auto-update", "on", "--apply", "--clear-overrides", "--json",
    ]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["effects"] == ("none" if failure == "unsupported" else "unknown")
    assert len(calls) == (1 if failure == "unsupported" else 2)
    assert json.loads(settings.read_text())["autoUpdate"] is False


@pytest.mark.parametrize("error", [control.PluginStateError, PermissionError, UnicodeError])
def test_post_write_readback_failure_preserves_overrides_and_reports_unknown(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    error: type[Exception],
) -> None:
    settings, _, calls = native
    original = control._settings
    monkeypatch.setattr(control, "_user_overrides", lambda: {"COPILOT_AUTO_UPDATE": "false"})
    monkeypatch.setattr(
        control, "_clear_user_overrides",
        lambda expected: pytest.fail("unverified write must not clear user overrides"),
    )

    def readback() -> dict[str, Any]:
        if any(arguments[:2] == ["config", "autoUpdate"] for arguments, _ in calls):
            raise error("settings read unavailable")
        return original()

    monkeypatch.setattr(control, "_settings", readback)
    assert cli.main([
        "copilot-cli", "auto-update", "on", "--apply", "--clear-overrides", "--json",
    ]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["effects"] == "unknown" and "read-back failed" in result["error"]
    assert json.loads(settings.read_text())["autoUpdate"] is True
    assert sum(arguments[:2] == ["config", "autoUpdate"] for arguments, _ in calls) == 1


def test_status_reads_jsonc_and_exposes_overrides_without_changing_them(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    settings, cache, _ = native
    settings.write_text('// settings header\n{"autoUpdate": true, "autoUpdatesChannel": "prerelease"}')
    monkeypatch.setenv("COPILOT_AUTO_UPDATE", "false")
    cached(cache)
    before = settings.read_bytes()
    assert cli.main(["copilot-cli", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["downloads_preference"] is True
    assert result["configured_channel"] == "prerelease"
    assert result["inherited_overrides"] == {"COPILOT_AUTO_UPDATE": "false"}
    assert result["cached_versions"][0]["ready"] is True
    assert settings.read_bytes() == before


def test_malformed_settings_refuse_without_native_write(
    native: tuple, capsys: pytest.CaptureFixture[str],
) -> None:
    settings, _, calls = native
    settings.write_text('{"autoUpdate": false, "autoUpdate": true}')
    assert cli.main(["copilot-cli", "auto-update", "off", "--apply", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["effects"] == "none"
    assert calls == []


@pytest.mark.parametrize("channel", [None, "stable", "prerelease"])
def test_update_delegates_effective_channel_and_requires_apply(
    native: tuple, capsys: pytest.CaptureFixture[str], channel: str | None,
) -> None:
    _, _, calls = native
    arguments = ["copilot-cli", "update", "--json", *(["--channel", channel] if channel else [])]
    assert cli.main(arguments) == 0
    assert not json.loads(capsys.readouterr().out)["applied"]
    assert calls == []
    assert cli.main([*arguments, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"]
    assert calls == [(["update", *([channel] if channel else [])], {"timeout": 600, "effectful": True})]


@pytest.mark.parametrize("damage", ["missing", "incomplete", "metadata", "oversize", "linked"])
def test_unsafe_or_incomplete_cached_version_cannot_launch(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    _, cache, _ = native
    if damage != "missing":
        package = cached(cache)
        if damage == "incomplete":
            (package / ".extraction-complete").unlink()
        elif damage == "metadata":
            (package / "package.json").write_text('{"version":"1.0.99"}')
        elif damage == "oversize":
            (package / "package.json").write_text(" " * (64 * 1024 + 1))
        elif damage == "linked":
            original = control._ordinary
            monkeypatch.setattr(control, "_ordinary", lambda path: path != package and original(path))
    monkeypatch.setattr(os, "execvpe", lambda *args: pytest.fail("invalid package was executed"))
    assert cli.main(["copilot-cli", "launch", "1.0.100-1", "--apply", "--json"]) == 2
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_cached_launch_preserves_exact_version_and_never_persists_a_pin(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    settings, cache, calls = native
    cached(cache)
    monkeypatch.setenv("COPILOT_CLI_DIST_DIR", "parent-package")
    before = settings.read_bytes()
    assert cli.main([
        "copilot-cli", "launch", "v1.0.100-1", "--arg=--resume=session-example", "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == [
        "fixture-copilot", "--prefer-version", "1.0.100-1", "--resume=session-example",
    ]
    assert result["persistent_version_pin_changed"] is False
    assert result["unset_environment"] == ["COPILOT_CLI_DIST_DIR"]
    assert settings.read_bytes() == before and calls == []


@pytest.mark.parametrize("version", ["../1.0.100", "latest", "1.0", "1.0.0-01", "01.0.0"])
def test_version_parser_rejects_noncanonical_or_path_selection(version: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        control.version_argument(version)


def test_forwarded_version_override_is_rejected(
    native: tuple, capsys: pytest.CaptureFixture[str],
) -> None:
    _, cache, _ = native
    cached(cache)
    assert cli.main([
        "copilot-cli", "launch", "1.0.100-1", "--arg=--prefer-version=1.0.99", "--json",
    ]) == 2
    assert "override" in json.loads(capsys.readouterr().out)["error"]


def test_cache_inspection_does_not_confuse_python_and_copilot_architectures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPILOT_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(control.platform, "system", lambda: "Windows")
    monkeypatch.setattr(control.platform, "machine", lambda: "AMD64")
    arm = tmp_path / "pkg" / "win32-arm64"
    arm.mkdir(parents=True)
    cached(arm)
    (tmp_path / "pkg" / "linux-x64").mkdir()
    assert control._cache_directories() == [arm]
    versions = control._versions()
    assert len(versions) == 1 and versions[0].ready
    assert versions[0].platform == "win32-arm64"


def test_cached_launch_does_not_forward_arbitrary_native_args_through_cmd(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _, cache, _ = native
    cached(cache)
    monkeypatch.setattr(control, "_executable", lambda selected: "copilot.cmd")
    assert cli.main([
        "copilot-cli", "launch", "1.0.100-1", "--apply", "--arg=--help", "--json",
    ]) == 2
    assert "standalone" in json.loads(capsys.readouterr().out)["error"]


def test_launch_checks_native_selection_and_transfers_only_the_owned_invocation(
    native: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, cache, calls = native
    cached(cache)
    monkeypatch.setenv("COPILOT_CLI_DIST_DIR", "parent-package")
    before = settings.read_bytes()
    executions: list[tuple[str, list[str], dict[str, str]]] = []

    class Executed(BaseException):
        pass

    def execute(arguments: list[str], environment: dict[str, str]) -> None:
        executions.append((arguments[0], arguments, environment))
        raise Executed

    monkeypatch.setattr(control, "_attended_launch", execute)
    with pytest.raises(Executed):
        cli.main([
            "copilot-cli", "launch", "1.0.100-1", "--apply", "--arg=--no-auto-update",
        ])
    assert calls[-1][0] == ["--prefer-version", "1.0.100-1", "--version"]
    assert executions[0][1] == [
        "fixture-copilot", "--prefer-version", "1.0.100-1", "--no-auto-update",
    ]
    assert "COPILOT_CLI_DIST_DIR" not in executions[0][2]
    assert os.environ["COPILOT_CLI_DIST_DIR"] == "parent-package"
    assert settings.read_bytes() == before


@pytest.mark.parametrize("failure", ["wrong_version", "cache_changed"])
def test_native_fallback_or_changed_cache_refuses_before_launch(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    _, cache, _ = native
    package = cached(cache)

    def invoke(executable: str, arguments: list[str], **kwargs: Any) -> str:
        if failure == "cache_changed":
            (package / ".extraction-complete").unlink()
            return "GitHub Copilot CLI 1.0.100-1."
        return "GitHub Copilot CLI 1.0.99."

    monkeypatch.setattr(control, "_native", invoke)
    monkeypatch.setattr(os, "execvpe", lambda *args: pytest.fail("unverified package was launched"))
    assert cli.main(["copilot-cli", "launch", "1.0.100-1", "--apply", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["effects"] == "none"


@pytest.mark.parametrize("phase", ["initial", "after_probe"])
def test_ready_architecture_cannot_mask_another_existing_invalid_candidate(
    native: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path, phase: str,
) -> None:
    architectures = [tmp_path / "win32-x64", tmp_path / "win32-arm64"]
    for directory in architectures:
        directory.mkdir()
        cached(directory)
    monkeypatch.setattr(control, "_cache_directories", lambda: architectures)
    rejected = architectures[0] / "1.0.100-1" / "index.js"
    original = control._ordinary
    damaged = phase == "initial"
    monkeypatch.setattr(
        control, "_ordinary",
        lambda path: not (damaged and path == rejected) and original(path),
    )

    def probe(*args: Any, **kwargs: Any) -> str:
        nonlocal damaged
        assert phase == "after_probe", "initial rejection must precede the native probe"
        damaged = True
        return "GitHub Copilot CLI 1.0.100-1."

    monkeypatch.setattr(control, "_native", probe)
    monkeypatch.setattr(
        control, "_attended_launch",
        lambda *args: pytest.fail("another architecture must not mask a rejected candidate"),
    )
    assert cli.main(["copilot-cli", "launch", "1.0.100-1", "--apply", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["ok"] and "win32-x64" in result["error"]


def test_override_cleanup_deletes_even_empty_values_and_retains_concurrent_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {"COPILOT_AUTO_UPDATE": "", "COPILOT_CLI_DIST_DIR": "changed-package"}
    deleted: list[str] = []

    def delete(key: object, name: str) -> None:
        del values[name]
        deleted.append(name)

    backend = SimpleNamespace(
        HKEY_CURRENT_USER="user", KEY_QUERY_VALUE=1, KEY_SET_VALUE=2,
        OpenKey=lambda *args: contextlib.nullcontext("environment-key"),
        QueryValueEx=lambda key, name: (values[name], 1),
        DeleteValue=delete,
    )
    monkeypatch.setitem(sys.modules, "winreg", backend)
    with pytest.raises(control.CopilotControlError, match="Already removed") as failure:
        control._clear_user_overrides({
            "COPILOT_AUTO_UPDATE": "", "COPILOT_CLI_DIST_DIR": "previous-package",
        })
    assert failure.value.effects == "partial"
    assert deleted == ["COPILOT_AUTO_UPDATE"]
    assert values == {"COPILOT_CLI_DIST_DIR": "changed-package"}


def test_native_failure_retains_nonzero_outcome_and_process_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 7, "", "native update failed")

    monkeypatch.setattr(control, "run_caller_owned", run)
    with pytest.raises(control.CopilotControlError, match="native update failed") as failure:
        control._native("fixture-copilot", ["update"], timeout=600, effectful=True)
    assert failure.value.effects == "unknown"
    assert captured["timeout"] == 600
    assert captured["max_stdout_bytes"] == 512 * 1024


@pytest.mark.parametrize("effectful", [False, True])
@pytest.mark.parametrize("error", [RuntimeError, UnicodeError])
def test_native_execution_and_decode_failures_keep_structured_effects(
    monkeypatch: pytest.MonkeyPatch, effectful: bool, error: type[Exception],
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise error("native completion unavailable")

    monkeypatch.setattr(control, "run_caller_owned", fail)
    with pytest.raises(control.CopilotControlError, match="native completion unavailable") as failure:
        control._native("fixture-copilot", ["update"], effectful=effectful)
    assert failure.value.effects == ("unknown" if effectful else "none")


@pytest.mark.skipif(os.name != "nt", reason="Windows foreground launch lifetime")
def test_windows_attended_launch_waits_for_real_child_and_propagates_status(tmp_path: Path) -> None:
    completed = tmp_path / "completed"
    command = [
        sys.executable, "-I", "-c",
        "import pathlib,sys,time; time.sleep(0.2); "
        "pathlib.Path(sys.argv[1]).write_text('settled'); sys.exit(7)",
        str(completed),
    ]
    with pytest.raises(SystemExit) as result:
        control._attended_launch(command, dict(os.environ))
    assert result.value.code == 7
    assert completed.read_text() == "settled"
