"""Current explicit policy and legacy compatibility, without runtime effects."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TextIO

import pytest

from agent_dispatch.action_controls import action_decision, action_inhibited
from agent_dispatch.action_controls_cli import command
from agent_dispatch.action_policy import ACTIONS, MAX_STATE_BYTES, STATE_NAME, evaluate_action
from agent_dispatch.early_cli import dispatch


def _write(root: Path, value: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / STATE_NAME
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("action", ACTIONS)
def test_current_explicit_state_restores_on_each_attempt(tmp_path: Path, action: str) -> None:
    for _ in range(2):
        assert not evaluate_action(action, tmp_path).inhibited
        path = _write(tmp_path, {action: {"disabled": True, "reason": "temporary"}})
        decision = evaluate_action(action, tmp_path)
        assert decision.inhibited and decision.reason == "temporary"
        assert decision.source == str(path)
        _write(tmp_path, {action: {"disabled": False}})
        assert not evaluate_action(action, tmp_path).inhibited


@pytest.mark.parametrize("bad", [
    [], None, {"unknown": {"disabled": True}},
    {"coordinator-replacement": {"disabled": "false"}},
    {"coordinator-replacement": {"disabled": 1}},
    {"coordinator-replacement": {"reason": "missing boolean"}},
    {"coordinator-replacement": {"disabled": False, "reason": []}},
])
def test_invalid_explicit_policy_blocks_even_another_action(tmp_path: Path, bad: object) -> None:
    _write(tmp_path, bad)
    decision = evaluate_action("supervisor-self-update", tmp_path)
    assert decision.inhibited and decision.error
    _write(tmp_path, {})
    assert not evaluate_action("supervisor-self-update", tmp_path).inhibited


@pytest.mark.parametrize("raw", [b"{", b"\xff", b" " * (MAX_STATE_BYTES + 1)])
def test_corrupt_state_is_visible_and_retryable(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / STATE_NAME
    path.write_bytes(raw)
    assert evaluate_action("coordinator-replacement", tmp_path).error
    path.write_bytes(b"{}")
    assert not evaluate_action("coordinator-replacement", tmp_path).inhibited


def test_linked_state_is_not_followed(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}")
    (tmp_path / STATE_NAME).symlink_to(real)
    decision = evaluate_action("coordinator-replacement", tmp_path)
    assert decision.inhibited and "linked" in (decision.error or "")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX pipe boundary")
def test_pipe_state_never_waits_for_a_writer(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / STATE_NAME)
    decision = evaluate_action("coordinator-replacement", tmp_path)
    assert decision.inhibited and "regular file" in (decision.error or "")


def test_foreign_file_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if not hasattr(os, "getuid"):
        pytest.skip("POSIX ownership")
    _write(tmp_path, {})
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    decision = evaluate_action("coordinator-replacement", tmp_path)
    assert decision.inhibited and "different user" in (decision.error or "")


def test_shared_bootstrap_is_default_automatic_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    root = tmp_path / ".agent-dispatch"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    shared = tmp_path / ".copilot-extensions" / "bootstrap-killswitch.json"
    shared.parent.mkdir()
    shared.write_text('{"active": true}')
    assert action_decision("coordinator-replacement", automatic=True).inhibited
    assert not action_decision("coordinator-replacement").inhibited
    assert not action_decision("supervisor-self-update").inhibited
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "other-install"))
    assert not action_decision("coordinator-replacement", automatic=True).inhibited
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "other-cell")
    assert not action_decision("coordinator-replacement", automatic=True).inhibited


def test_primary_shared_file_wins_and_corruption_retains_legacy_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / ".agent-dispatch"))
    base = tmp_path / ".copilot-extensions"
    (base / "killSwitches").mkdir(parents=True)
    (base / "bootstrap-killswitch.json").write_text('{"active": true}')
    (base / "killSwitches" / "bootstrap-killswitch.json").write_text("bad")
    assert not action_inhibited(
        "supervisor-self-update", automatic=True, logger=logging.getLogger(__name__),
    )
    assert "legacy fail-open" in caplog.text


def test_local_control_does_not_change_lane_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    overrides = tmp_path / "overrides.json"
    overrides.write_text('{"ordinary-lane": {"disabled": true}}')
    for verb in ("inhibit", "clear", "inhibit", "clear"):
        assert command(argparse.Namespace(
            action_command=verb, action="coordinator-replacement", reason="temporary",
        )) == 0
        assert action_decision("coordinator-replacement").inhibited == (verb == "inhibit")
        result = json.loads(capsys.readouterr().out)
        assert result["contract"]["consumer"] == "executing-runtime-only"
        assert overrides.read_text() == '{"ordinary-lane": {"disabled": true}}'


def test_local_control_refuses_corrupt_policy_instead_of_overwriting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    path = _write(tmp_path, {"coordinator-replacement": {"disabled": "false"}})
    before = path.read_bytes()
    assert command(argparse.Namespace(
        action_command="clear", action="coordinator-replacement",
    )) == 2
    assert path.read_bytes() == before
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_early_command_contract_and_strict_syntax(capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch(["actions", "contract", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["contract"]["actions"] == list(ACTIONS)
    with pytest.raises(SystemExit) as exc:
        dispatch(["actions", "inhibit", "coordinator-replacement", "--unknown"])
    assert exc.value.code == 2


def test_early_command_does_not_bypass_task_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_TASK_SCOPE_ID", "restricted-task")
    assert dispatch(["actions", "clear", "coordinator-replacement"]) is None


def test_oversized_mutation_does_not_publish_unreadable_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    path = _write(tmp_path, {})
    assert command(argparse.Namespace(
        action_command="inhibit", action="coordinator-replacement",
        reason="x" * MAX_STATE_BYTES,
    )) == 2
    assert path.read_text() == "{}"
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_local_control_refuses_remote_target_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    assert command(argparse.Namespace(
        action_command="inhibit", action="coordinator-replacement",
        reason="temporary", url="http://remote.invalid",
    )) == 2
    assert not (tmp_path / STATE_NAME).exists()
    assert "installation-local" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_directory_read_error_is_visible_and_rechecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    legacy: bool,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / ".agent-dispatch"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    base = tmp_path / ".copilot-extensions"
    path = (base if legacy else base / "killSwitches") / "bootstrap-killswitch.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"active": true}')
    denied = [True]

    def unreadable(probe: Callable[[Path], bool]) -> Callable[[Path], bool]:
        def guarded(candidate: Path) -> bool:
            if denied[0] and (candidate == base or base in candidate.parents):
                raise PermissionError("synthetic shared-directory denial")
            return probe(candidate)

        return guarded

    for name in ("exists", "is_file", "is_symlink"):
        monkeypatch.setattr(Path, name, unreadable(getattr(Path, name)))
    assert not action_inhibited(
        "supervisor-self-update", automatic=True, logger=logging.getLogger(__name__),
    )
    assert "synthetic shared-directory denial" in caplog.text
    assert "legacy fail-open" in caplog.text
    denied[0] = False
    assert action_decision("supervisor-self-update", automatic=True).inhibited
    path.write_text('{"active": false}')
    assert not action_decision("supervisor-self-update", automatic=True).inhibited


def test_near_limit_policy_uses_exact_utf8_lf_bytes_and_remains_clearable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_dispatch import action_controls_cli, overrides

    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    timestamp = 1700000000.0
    monkeypatch.setattr(action_controls_cli.time, "time", lambda: timestamp)
    real_fdopen = os.fdopen
    newlines: list[str | None] = []

    def fdopen(
        fd: int, mode: str, *, encoding: str | None = None, newline: str | None = None,
    ) -> BinaryIO | TextIO:
        if "b" in mode:
            return real_fdopen(fd, mode)
        newlines.append(newline)
        return real_fdopen(fd, mode, encoding=encoding, newline=newline)

    monkeypatch.setattr(overrides.os, "fdopen", fdopen)
    action = "coordinator-replacement"
    empty = {action: {"disabled": True, "reason": "", "at": timestamp}}
    overhead = len(json.dumps(empty, indent=2, sort_keys=True).encode("utf-8"))
    assert command(argparse.Namespace(
        action_command="inhibit", action=action, reason="x" * (MAX_STATE_BYTES - overhead),
    )) == 0
    capsys.readouterr()
    raw = (tmp_path / STATE_NAME).read_bytes()
    assert len(raw) == MAX_STATE_BYTES
    assert b"\r\n" not in raw
    assert newlines == ["\n"]
    assert action_decision(action).inhibited
    assert command(argparse.Namespace(action_command="clear", action=action)) == 0
    assert not action_decision(action).inhibited
