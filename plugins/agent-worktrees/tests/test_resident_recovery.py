"""Maintenance-aware discovery never replays mutations or repairs a host."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import resident_recovery as recovery, tracking_write as writes


@pytest.fixture
def private_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "_user_profile", lambda: tmp_path)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    root = tmp_path / ".copilot-extensions"
    root.mkdir()
    return root


def notice(root: Path, status: str = "active") -> None:
    (root / "maintenance.json").write_text(json.dumps({
        "version": 1, "status": status, "ownerSessionId": "fixture",
        "summary": "Fixture maintenance", "startedAt": "2026-11-05T00:00:00Z",
    }))


@pytest.mark.parametrize("kind", ["notice", "marker", "invalid", "oversized", "directory", "unreadable"])
def test_maintenance_holds_before_backoff_wake_or_fallback(private_recovery, monkeypatch, kind):
    root = private_recovery
    if kind == "notice":
        notice(root)
    elif kind == "marker":
        (root / "maintenance").touch()
    elif kind == "directory":
        (root / "maintenance.json").mkdir()
    elif kind == "unreadable":
        notice(root)
        def unreadable(*args, **kwargs):
            raise PermissionError("fixture notice cannot be read")
        monkeypatch.setattr(Path, "open", unreadable)
    else:
        (root / "maintenance.json").write_text("?" * (16385 if kind == "oversized" else 1))
    effects = []
    monkeypatch.setattr(writes.time, "sleep", lambda delay: effects.append("sleep"))
    with pytest.raises(recovery.ResidentRecoveryHeld):
        writes.write_with_boot(
            read_lock_data=lambda: None, ensure_monitor=lambda: effects.append("wake"),
            payload={"verb": "fixture"}, fallback=lambda: effects.append("fallback"),
        )
    assert effects == []
    assert recovery.wake_allowed() is False


@pytest.mark.parametrize("mode", [
    "arrives", "wake", "absent", "maintenance", "maintenance-arrives",
    "maintenance-wake", "maintenance-delayed", "ambiguous", "no-wake",
])
def test_bounded_recovery_sequence(private_recovery, monkeypatch, mode):
    clock, effects = [0.0], []
    ready = False
    endpoint = {
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture", "tracking_write_verbs": ["fixture"],
    }

    def sleep(delay):
        nonlocal ready
        effects.append("backoff")
        clock[0] += delay
        if mode in {"maintenance", "maintenance-arrives"}:
            notice(private_recovery)
        if mode in {"arrives", "maintenance-arrives"}:
            ready = True
        if mode == "maintenance-delayed" and clock[0] >= .2:
            notice(private_recovery)
            ready = True

    def wake():
        nonlocal ready
        effects.append("wake")
        ready = mode in {"wake", "ambiguous", "maintenance-wake"}
        if mode == "maintenance-wake":
            notice(private_recovery)

    def send(*args, **kwargs):
        effects.append("send")
        if mode == "ambiguous":
            raise writes.AmbiguousWriteOutcome("possibly committed")
        return {"ok": True}

    monkeypatch.setattr(writes, "time", SimpleNamespace(time=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(writes, "_send_tracking_write_request", send)
    monkeypatch.setattr(writes.wcs_client, "release", lambda *args, **kwargs: None)
    options = dict(
        read_lock_data=lambda: endpoint if ready else None,
        ensure_monitor=None if mode == "no-wake" else wake,
        payload={"verb": "fixture"}, fallback=lambda: effects.append("fallback") or {"direct": True},
        boot_wait_s=.3,
    )
    if mode.startswith("maintenance") or mode == "ambiguous":
        with pytest.raises(
            writes.AmbiguousWriteOutcome if mode == "ambiguous" else recovery.ResidentRecoveryHeld,
        ):
            writes.write_with_boot(**options)
        assert "fallback" not in effects
        assert effects.count("send") == (mode == "ambiguous")
    else:
        result = writes.write_with_boot(**options)
        assert result == ({"ok": True} if mode in {"arrives", "wake"} else {"direct": True})
        assert effects.count("wake") == (mode in {"wake", "absent"})
        assert effects.count("send") == (mode in {"arrives", "wake"})
        assert clock[0] <= .4
        if mode == "no-wake":
            assert effects == ["fallback"]


def test_complete_notice_and_removed_installation_marker_do_not_hold(private_recovery):
    notice(private_recovery, "complete")
    recovery.require_wake_allowed()
    (private_recovery / "maintenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-maintenance",
    }))
    recovery.require_wake_allowed()


def test_effort_dispatch_uses_normal_ensure(private_recovery, monkeypatch):
    from agent_worktrees import status_monitor_runtime as monitor, tracking_effort_binding_write as effort
    received = {}
    monkeypatch.setattr(monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(writes, "dispatch", lambda *args, **kwargs: received.update(kwargs) or {})
    effort.dispatch("effort_bind", {})
    assert received["ensure_monitor"] is monitor._ensure_status_monitor


def test_normal_ensure_does_not_spawn_during_maintenance(private_recovery, monkeypatch):
    from agent_worktrees import status_monitor_runtime as monitor
    notice(private_recovery)
    monkeypatch.setattr(monitor.locks, "read_lock", lambda path: None)
    monkeypatch.setattr(monitor, "_spawn_detached", lambda *args: pytest.fail("unexpected spawn"))
    assert monitor._ensure_status_monitor() is False


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("same_endpoint", [False, True])
def test_refused_connection_can_recover_only_before_any_send(
    private_recovery, monkeypatch, held, same_endpoint,
):
    current = {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": ["fixture"],
    }
    calls = []

    def wake():
        calls.append("wake")
        if not same_endpoint:
            current["tracking_write_endpoint"] = "127.0.0.1:49153"

    def send(host, port, *args, **kwargs):
        calls.append(port)
        if len(calls) == 1:
            raise writes._PreSendFailure("connection refused before request")
        return {"ok": True}

    if held:
        notice(private_recovery)
    monkeypatch.setattr(writes.time, "sleep", lambda delay: None)
    def connect(*args, **kwargs):
        if "wake" not in calls:
            raise ConnectionRefusedError()
        return nullcontext()
    monkeypatch.setattr(writes.socket, "create_connection", connect)
    monkeypatch.setattr(writes, "_send_tracking_write_request", send)
    monkeypatch.setattr(writes.wcs_client, "release", lambda *args, **kwargs: None)
    options = dict(
        read_lock_data=lambda: current, ensure_monitor=wake,
        payload={"verb": "fixture"}, fallback=lambda: pytest.fail("unexpected direct write"),
    )
    if held:
        with pytest.raises(recovery.ResidentRecoveryHeld):
            writes.write_with_boot(**options)
        assert calls == [49152]
    else:
        assert writes.write_with_boot(**options) == {"ok": True}
        assert calls == [49152, "wake", 49152 if same_endpoint else 49153]


@pytest.mark.parametrize("value", [[], {}])
def test_container_status_is_a_safe_refusal(private_recovery, value):
    notice(private_recovery)
    path = private_recovery / "maintenance.json"
    body = json.loads(path.read_text())
    body["status"] = value
    path.write_text(json.dumps(body))
    assert recovery.wake_allowed() is False
    with pytest.raises(recovery.ResidentRecoveryHeld, match="invalid"):
        recovery.require_wake_allowed()


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX passwd profile resolution")
def test_os_profile_hold_survives_redirected_state(tmp_path, monkeypatch):
    import pwd
    profile, redirected = tmp_path / "profile", tmp_path / "redirected"
    (profile / ".copilot-extensions").mkdir(parents=True)
    notice(profile / ".copilot-extensions")
    redirected.mkdir()
    monkeypatch.setenv("HOME", str(redirected))
    monkeypatch.setenv("AGENT_HOME", str(redirected))
    monkeypatch.setattr(cfg, "install_dir", lambda: redirected / "runtime")
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(profile)))
    with pytest.raises(recovery.ResidentRecoveryHeld, match="active"):
        recovery.require_wake_allowed()


@pytest.mark.parametrize("live_owner", [False, True])
@pytest.mark.parametrize("held_when_ready", [False, True])
def test_delayed_same_endpoint_readiness_does_not_consume_send_retry(
    private_recovery, monkeypatch, live_owner, held_when_ready,
):
    from agent_worktrees import locks

    clock, calls = [0.0], []
    endpoint = {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": ["fixture"],
    }
    if live_owner:
        endpoint.update(pid=1234, start_time="fixture-birth")
        monkeypatch.setattr(locks, "pid_alive", lambda pid: pid == 1234)
        monkeypatch.setattr(locks, "process_start_time", lambda pid: "fixture-birth")
    monkeypatch.setattr(writes, "time", SimpleNamespace(
        time=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay),
    ))
    def connect(*args, **kwargs):
        if clock[0] < .5:
            raise ConnectionRefusedError()
        if held_when_ready:
            notice(private_recovery)
        return nullcontext()
    def send(*args, **kwargs):
        calls.append("send")
        if len(calls) == 1:
            raise writes._PreSendFailure("not listening")
        return {"ok": True}
    monkeypatch.setattr(writes.socket, "create_connection", connect)
    monkeypatch.setattr(writes, "_send_tracking_write_request", send)
    monkeypatch.setattr(writes.wcs_client, "release", lambda *args, **kwargs: None)
    options = dict(
        read_lock_data=lambda: endpoint, ensure_monitor=lambda: calls.append("wake"),
        payload={"verb": "fixture"}, fallback=lambda: pytest.fail("premature fallback"),
        boot_wait_s=1,
    )
    if held_when_ready:
        with pytest.raises(recovery.ResidentRecoveryHeld, match="active"):
            writes.write_with_boot(**options)
        assert calls == ["send", "wake"]
    else:
        assert writes.write_with_boot(**options) == {"ok": True}
        assert calls == ["send", "wake", "send"]
    assert .5 <= clock[0] < 1


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX passwd profile resolution")
def test_wsl_recovery_needs_only_os_profile_not_guest_registration(tmp_path, monkeypatch):
    import pwd
    from agent_worktrees import _installation_context as installation_context

    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(installation_context, "_is_wsl_guest", lambda: True)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    assert recovery.wake_allowed() is True
    root = tmp_path / ".copilot-extensions"
    root.mkdir()
    notice(root)
    assert recovery.wake_allowed() is False


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX passwd profile isolation")
def test_shared_home_isolation_covers_canonical_profile(_isolate_agent_worktrees_home):
    root = _isolate_agent_worktrees_home / ".copilot-extensions"
    root.mkdir()
    notice(root)
    with pytest.raises(recovery.ResidentRecoveryHeld, match="active"):
        recovery.require_wake_allowed()


@pytest.mark.parametrize("command", ["embody", "handoff-cutover"])
def test_implicit_json_cli_maintenance_refusal(command, monkeypatch, capfd):
    from agent_worktrees import __main__ as cli

    def held(args):
        raise recovery.ResidentRecoveryHeld("active maintenance")

    monkeypatch.setitem(cli.COMMAND_MAP, command, held)
    assert cli._dispatch_lazy(command, [command]) == 1
    captured = capfd.readouterr()
    assert "maintenance" in json.loads(captured.out)["error"]
    assert not captured.err


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("command", ["add", "resolve", "dismiss", "conclude-session", "link-succession"])
def test_parsed_cli_write_refusals_preserve_output_and_record(
    private_recovery, tmp_path, monkeypatch, capfd, json_output, command,
):
    from agent_worktrees import __main__ as cli
    from agent_worktrees import claims_cli, follow_ups_cli, session_tracking_cli, status_monitor_runtime
    from _native_receipt_fixtures import setup

    owner, path, home = setup(tmp_path / "source", monkeypatch)
    notice(private_recovery)
    monkeypatch.setattr(follow_ups_cli, "_follow_ups_record_path", lambda *args: ("native-one", path))
    monkeypatch.setattr(claims_cli, "_require_coordination_readiness", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_tracking_cli, "_find_tracking_file", lambda *args: path)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(status_monitor_runtime.locks, "read_lock", lambda *args: None)
    monkeypatch.setattr(status_monitor_runtime, "_ensure_status_monitor", lambda: pytest.fail("held wake"))
    monkeypatch.setattr(writes, "run_direct", lambda *args, **kwargs: pytest.fail("held fallback"))
    if command in {"add", "resolve", "dismiss"}:
        name, arguments = "follow-ups", [command, "fixture"]
        if command == "dismiss":
            arguments += ["--reason", "fixture"]
    else:
        name, arguments = command, ["--worktree", "native-one"]
        arguments += (
            ["--session", "head"] if command == "conclude-session"
            else ["--predecessor", "head", "--successor", "next"]
        )
    arguments = [name, *arguments, *(["--json"] if json_output else [])]
    before = path.read_bytes()
    try:
        assert cli._dispatch_lazy(name, arguments) == 1
        captured = capfd.readouterr()
        text = captured.out + captured.err
        assert "maintenance" in text and "Traceback" not in text and "unknown state" not in text
        if json_output or name != "follow-ups":
            assert "maintenance" in json.loads(captured.out)["error"]
        assert path.read_bytes() == before
    finally:
        owner.shutdown()


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("action", ["add", "settle", "release"])
def test_claim_cli_maintenance_refusal_is_not_a_traceback(
    private_recovery, tmp_path, monkeypatch, capfd, json_output, action,
):
    from agent_worktrees import claims_cli, tracking
    from _native_receipt_fixtures import setup
    owner, path, home = setup(tmp_path / "source", monkeypatch)
    def held(*args, **kwargs):
        raise recovery.ResidentRecoveryHeld("active maintenance")
    monkeypatch.setattr(claims_cli.worktree_identity, "_infer_worktree_id", lambda *args: "native-one")
    monkeypatch.setattr(claims_cli, "_require_coordination_readiness", lambda *args, **kwargs: None)
    monkeypatch.setattr(claims_cli, "_dispatch_claim", held)
    try:
        import argparse
        args = argparse.Namespace(json=json_output, owner_ref=None, note="", remove=False)
        if action == "add":
            result = claims_cli._claims_add(args, "codespace", "fixture")
        elif action == "settle":
            args.claim_disposition = "at-rest"
            result = claims_cli._claims_settle(args, "fixture")
        else:
            result = claims_cli._claims_release(args, "fixture")
        assert result == 1
        captured = capfd.readouterr()
        text = captured.out + captured.err
        assert "maintenance" in text and "Traceback" not in text
        if json_output:
            assert "maintenance" in json.loads(captured.out)["error"]
        assert not tracking.load_record(path).resources
    finally:
        owner.shutdown()


def test_recovery_reaches_actual_canonical_capture(private_recovery, tmp_path, monkeypatch):
    from agent_worktrees import tracking
    from agent_worktrees import tracking_write
    from _native_receipt_fixtures import setup, publish, page

    owner, path, home = setup(tmp_path / "source", monkeypatch)
    server = None
    endpoint = None

    def wake():
        nonlocal server, endpoint
        server = tracking_write.start_server(owner.native_compute)
        server.start()
        endpoint = tracking_write.rendezvous_fields(server)
        return True

    try:
        result = writes.dispatch(
            "claim_add", {
                "yaml_path": str(path), "worktree_id": "native-one",
                "kind": "codespace", "ref": "fixture-child",
            },
            read_lock_data=lambda: endpoint, ensure_monitor=wake,
        )
        assert result["ok"]
        assert [claim.ref for claim in tracking.load_record(path).resources] == ["fixture-child"]
        publish(owner, path)
        history = page(owner)
        assert any(row["kind"] == "claim-added" and row["evidence_kind"] == "persisted-mutation"
                   for row in history["entries"])
    finally:
        if server is not None:
            server.close()
        owner.shutdown()
