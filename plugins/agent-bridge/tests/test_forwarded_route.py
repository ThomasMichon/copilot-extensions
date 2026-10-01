"""A venue's forwarded bridge route is never taken over by a local daemon.

On a CodeSpace/container/SSH venue, the launcher points ``active.json`` at the
host bridge's forwarded port so the sessions there report to the host. A local
daemon started over that route publishes itself in its place: the sessions then
heartbeat the local daemon, and the host expires them while they keep running.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as m
from agent_bridge import service_process_cli
from agent_bridge.self_retire import is_superseded


def _route(tmp_path, monkeypatch, active):
    (tmp_path / "config.yaml").write_text("port: 0\n", encoding="utf-8")
    (tmp_path / "active.json").write_text(json.dumps({"active": active}), encoding="utf-8")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))


FORWARD = {"bind": "127.0.0.1", "port": 62254, "forwarded": True}
LEGACY_FORWARD = {"port": 62254}  # what launchers wrote before "bind"/"forwarded"
BOUND_PIDLESS = {"bind": "127.0.0.1", "port": 62254}
DAEMON = {"bind": "127.0.0.1", "port": 39881, "pid": 350677, "version": "0.4.4", "generation": 1}


def test_a_forwarded_route_is_recognized_and_resolved(tmp_path, monkeypatch):
    for active in (FORWARD, LEGACY_FORWARD):
        _route(tmp_path, monkeypatch, active)
        assert m._active_endpoint_is_forward()
        assert m._service_port() == 62254  # not the default port


def test_a_daemon_published_route_is_not_a_forward(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, DAEMON)
    assert not m._active_endpoint_is_forward()
    _route(tmp_path, monkeypatch, BOUND_PIDLESS)
    assert not m._active_endpoint_is_forward()
    (tmp_path / "active.json").unlink()
    assert not m._active_endpoint_is_forward()


def _ensure_setup(monkeypatch, tmp_path, *, answers):
    import time as _t

    monkeypatch.setattr(_t, "sleep", lambda *_a: None)
    monkeypatch.setattr(m, "_ENSURE_LOCK", str(tmp_path / ".ensure.lock"))
    monkeypatch.setattr(m, "_ENSURE_MARKER", str(tmp_path / ".ensure-attempt"))
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)
    seq = iter(answers)
    monkeypatch.setattr(m, "_service_is_running", lambda: next(seq, False))
    spawned = []
    monkeypatch.setattr(m, "_spawn_detached_daemon", lambda: spawned.append(1))
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: spawned.append("reconcile"))
    return spawned


def test_ensure_never_starts_a_daemon_over_a_forwarded_route(tmp_path, monkeypatch, capsys):
    _route(tmp_path, monkeypatch, FORWARD)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False] * 10)
    assert m._ensure_daemon() is False
    assert spawned == []
    assert "not starting a local daemon" in capsys.readouterr().err


def test_ensure_rides_out_a_blip_on_the_forward(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, LEGACY_FORWARD)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False, False, True])
    assert m._ensure_daemon() is True
    assert spawned == []


def test_ensure_still_boots_a_local_daemon_without_a_forward(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, BOUND_PIDLESS)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False, False, True])
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: False)
    monkeypatch.setattr(m, "_service_process_is_live", lambda: False)
    monkeypatch.setattr(m, "_acquire_ensure_lock", lambda: 7)
    monkeypatch.setattr(m, "_release_ensure_lock", lambda _fd: None)
    assert m._ensure_daemon() is True
    assert spawned == [1]


def test_ensure_rechecks_forward_after_lock_before_spawning(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, DAEMON)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False] * 10)
    released = []
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: False)
    monkeypatch.setattr(m, "_service_process_is_live", lambda: False)

    def acquire():
        (tmp_path / "active.json").write_text(
            json.dumps({"active": FORWARD}), encoding="utf-8"
        )
        return 7

    monkeypatch.setattr(m, "_acquire_ensure_lock", acquire)
    monkeypatch.setattr(m, "_release_ensure_lock", released.append)
    assert m._ensure_daemon() is False
    assert spawned == []
    assert released == [7]


def test_the_retry_waits_back_off():
    assert list(service_process_cli._FORWARD_RETRY_DELAYS_S) == sorted(
        service_process_cli._FORWARD_RETRY_DELAYS_S
    )


def _superseded(active, *, listening=True, my_pid=350677):
    return is_superseded(
        "/unused", my_pid=my_pid, my_generation=1,
        read_table=lambda _d: {"active": active},
        is_listening=lambda _h, _p: listening,
    )


def test_a_daemon_whose_route_a_live_forward_replaced_retires():
    assert _superseded(FORWARD)


def test_a_daemon_stays_when_the_forward_is_not_explicit_live_or_pid_free():
    assert not _superseded(FORWARD, listening=False)
    assert not _superseded(LEGACY_FORWARD)  # never retire on an ambiguous entry
    assert not _superseded({**FORWARD, "pid": 42, "generation": 9}, listening=False)
    assert not _superseded(DAEMON)  # its own route

# -- the service verbs never act on the host bridge through the forward -------

def _forward_down(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, FORWARD)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)

    def boom(*_a, **_k):
        raise AssertionError("must not act over a forwarded route")

    return boom


def test_service_start_does_not_start_a_daemon_over_a_forward(tmp_path, monkeypatch, capsys):
    boom = _forward_down(tmp_path, monkeypatch)
    for name in ("_reconcile_live_dynamic_daemon", "_systemd_available", "_spawn_detached_daemon"):
        monkeypatch.setattr(m, name, boom)
    m._service_start()
    assert "not starting a local daemon" in capsys.readouterr().out


def _stop_setup(monkeypatch, killed, *, is_bridge=True):
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(m, "_pid_from_lock", lambda _port: None)
    monkeypatch.setattr(m, "_pid_on_port", lambda _port: 4242)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid, *_a: is_bridge)
    monkeypatch.setattr(m, "_kill_pid", killed.append)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)


def test_service_stop_never_kills_the_forwards_listener(tmp_path, monkeypatch):
    _forward_down(tmp_path, monkeypatch)
    killed = []
    _stop_setup(monkeypatch, killed)  # 4242 is the ssh session holding the forward
    m._service_stop()
    assert killed == []
    assert m._service_pid() is None  # nor is it reported as the bridge


def test_service_stop_kills_a_port_listener_only_if_it_is_a_bridge(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, {"bind": "127.0.0.1", "port": 39881, "generation": 1})
    killed = []
    _stop_setup(monkeypatch, killed, is_bridge=False)
    m._service_stop()
    assert killed == []
    _stop_setup(monkeypatch, killed, is_bridge=True)
    m._service_stop()
    assert killed == [4242]


def test_deploy_never_cuts_over_the_host_bridge(tmp_path, monkeypatch, capsys):
    boom = _forward_down(tmp_path, monkeypatch)
    import zdd.cutover

    monkeypatch.setattr(zdd.cutover, "CutoverOrchestrator", boom)
    from agent_bridge import venue_cli

    venue_cli._cmd_deploy(None)
    assert "no local daemon to deploy" in capsys.readouterr().out


def test_deploy_forward_skip_is_structured_json(tmp_path, monkeypatch, capsys):
    _route(tmp_path, monkeypatch, FORWARD)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_reap_abandoned_passive", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_json_out", lambda data: print(json.dumps(data)))

    from agent_bridge import venue_cli
    from agent_bridge import config as bridge_config

    monkeypatch.setattr(bridge_config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge_config, "load_or_create_auth_token", lambda: "tok")

    class _Cfg:
        bind = "127.0.0.1"

    monkeypatch.setattr(bridge_config, "load_config", lambda: _Cfg())

    import zdd.breadcrumb

    monkeypatch.setattr(zdd.breadcrumb, "read_breadcrumb", lambda _d: None)
    monkeypatch.setattr(
        zdd.breadcrumb,
        "recover_stale_cutover",
        lambda *_a, **_k: {"recovered": False, "reason": "clean"},
    )

    args = type(
        "Args",
        (),
        {
            "health_timeout": 1,
            "drain_timeout": 1,
            "force": False,
            "json": True,
            "recover": False,
        },
    )()
    with pytest.raises(SystemExit) as exc:
        venue_cli._cmd_deploy(args)
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] is True
    assert payload["ok"] is False
    assert "no local daemon to deploy" in payload["error"]


def test_deploy_rechecks_forward_inside_cutover(tmp_path, monkeypatch, capsys):
    _route(tmp_path, monkeypatch, DAEMON)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_reap_abandoned_passive", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_json_out", lambda data: print(json.dumps(data)))

    from agent_bridge import venue_cli
    from agent_bridge import config as bridge_config

    monkeypatch.setattr(bridge_config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge_config, "load_or_create_auth_token", lambda: "tok")

    class _Cfg:
        bind = "127.0.0.1"

    monkeypatch.setattr(bridge_config, "load_config", lambda: _Cfg())

    import zdd.breadcrumb
    import zdd.cutover

    monkeypatch.setattr(zdd.breadcrumb, "read_breadcrumb", lambda _d: None)
    monkeypatch.setattr(
        zdd.breadcrumb,
        "recover_stale_cutover",
        lambda *_a, **_k: {"recovered": False, "reason": "clean"},
    )

    class FakeCutoverOrchestrator:
        def __init__(self, *_a, refuse_old=None, **_k):
            self._refuse_old = refuse_old

        def run(self, **_k):
            (tmp_path / "active.json").write_text(
                json.dumps({"active": FORWARD}), encoding="utf-8"
            )
            reason = self._refuse_old(FORWARD) if self._refuse_old else None
            assert reason

            class Result:
                ok = False
                error = reason
                steps = [f"refused: {reason}"]

                def to_dict(self):
                    return {"ok": self.ok, "error": self.error, "steps": self.steps}

            return Result()

    monkeypatch.setattr(zdd.cutover, "CutoverOrchestrator", FakeCutoverOrchestrator)
    args = type(
        "Args",
        (),
        {
            "health_timeout": 1,
            "drain_timeout": 1,
            "force": False,
            "json": False,
            "recover": False,
        },
    )()
    with pytest.raises(SystemExit) as exc:
        venue_cli._cmd_deploy(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "refused:" in out
    assert "no local daemon to deploy" in out


def test_direct_start_skips_instead_of_publishing_over_a_forward(tmp_path, monkeypatch, capsys):
    _route(tmp_path, monkeypatch, FORWARD)

    from agent_bridge import config as bridge_config
    from agent_bridge import service_start_cli
    from agent_bridge import winjob

    cfg = SimpleNamespace(
        port=0,
        bind="127.0.0.1",
        enable_credential_relay=True,
        idle_shutdown_seconds=0,
    )
    monkeypatch.setattr(bridge_config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge_config, "load_config", lambda: cfg)
    monkeypatch.setattr(bridge_config, "migrate_config", lambda loaded: loaded)
    monkeypatch.setattr(bridge_config, "write_default_config", lambda _cfg: None)
    monkeypatch.setattr(bridge_config, "load_or_create_auth_token", lambda: "tok")
    monkeypatch.setattr(winjob, "setup_kill_on_close_job", lambda: None)
    monkeypatch.setattr(
        service_start_cli,
        "_bind_listen_socket",
        lambda *_a, **_k: pytest.fail("must not bind a local daemon socket"),
    )

    args = SimpleNamespace(port=None, bind=None, idle_shutdown=None, passive=False)
    service_start_cli._cmd_start(args)
    assert "not publishing or starting a local daemon" in capsys.readouterr().out


_INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def _install_sh_active_is_forward(active: dict, tmp_path: Path) -> bool:
    install_dir = tmp_path / "agent-bridge"
    install_dir.mkdir()
    (install_dir / "active.json").write_text(json.dumps({"active": active}))
    text = _INSTALL_SH.read_text(encoding="utf-8")
    fn = text.split("_active_is_forward() {", 1)[1].split("\n}\n\n_active_host", 1)[0]
    script = (
        f"INSTALL_DIR={install_dir!s}; VENV_DIR={tmp_path!s}/missing\n"
        "_active_is_forward() {"
        + fn
        + "\n}\n_active_is_forward\n"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
@pytest.mark.parametrize(
    ("active", "expected"),
    [
        (FORWARD, True),
        (LEGACY_FORWARD, True),
        (BOUND_PIDLESS, False),
    ],
)
def test_install_sh_forward_classifier(active, expected, tmp_path):
    assert _install_sh_active_is_forward(active, tmp_path) is expected


def test_install_sh_update_checks_forward_before_lifecycle_actions():
    text = _INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("do_update() {", 1)[1].split("\n}\n\ncase", 1)[0]
    forward_at = body.index("active_forward=false")
    revalidate_at = body.index('_update_lifecycle_still_targets_predecessor "$predecessor_signature"')
    drain_at = body.index("_drain_service")
    stop_at = body.index("do_stop")
    start_at = body.index("do_start")
    assert forward_at < revalidate_at < drain_at < stop_at < start_at
    assert 'if [[ "$active_forward" == true ]]; then' in body
    assert 'Forwarded host bridge route still active -- not starting a local daemon' in body
    assert 'Forwarded host bridge route appeared during update -- skipping drain/stop/start' in text
    assert 'Active route changed during update -- skipping drain/stop/start' in text
    assert '&& "$active_forward" != true' in body


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
def test_install_sh_start_does_not_start_a_daemon_over_a_forward(tmp_path):
    home = tmp_path / "home"
    (home / ".agent-bridge").mkdir(parents=True)
    (home / ".agent-bridge" / "active.json").write_text(json.dumps({"active": FORWARD}))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 7\n")  # the forward is down
    (fake_bin / "curl").chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(_INSTALL_SH), "start"], capture_output=True,
                            text=True, env=env, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "not starting a local daemon" in result.stdout
    assert not (home / ".agent-bridge" / "agent-bridge.pid").exists()
