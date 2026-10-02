"""A venue's forwarded bridge route is never taken over by a local daemon.

On a CodeSpace/container/SSH venue, the launcher points ``active.json`` at the
host bridge's forwarded port so the sessions there report to the host. A local
daemon started over that route publishes itself in its place: the sessions then
heartbeat the local daemon, and the host expires them while they keep running.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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


def test_service_stop_treats_forwarded_route_as_local_service_down(
    tmp_path, monkeypatch, capsys,
):
    _route(tmp_path, monkeypatch, FORWARD)
    monkeypatch.setattr(m, "_systemd_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(m, "_pid_from_lock", lambda port: 4242 if port == m._service_port() else None)
    monkeypatch.setattr(m, "_pid_on_port", lambda _port: 4242)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda _pid, *_a: False)
    monkeypatch.setattr(m, "_kill_pid", lambda _pid: (_ for _ in ()).throw(AssertionError("no kill")))
    monkeypatch.setattr(m, "_service_is_running", lambda: True)

    m._service_stop()

    output = capsys.readouterr()
    assert "[OK] agent-bridge stopped" in output.out
    assert "still responding" not in output.err


def test_service_stop_over_a_forward_still_stops_a_local_fixed_port_daemon(tmp_path, monkeypatch):
    """A local daemon on its configured port (no pid file) outlives a rejoin
    that rewrote the route to a forward; stop still finds it by its lock."""
    _forward_down(tmp_path, monkeypatch)
    killed = []
    _stop_setup(monkeypatch, killed)  # 4242 on the forwarded port is the ssh session
    fixed = m._configured_port()
    assert fixed != m._service_port()
    alive = {7777}
    monkeypatch.setattr(m, "_pid_from_lock",
                        lambda port: 7777 if port == fixed and 7777 in alive else None)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid, *_a: pid in alive)
    monkeypatch.setattr(m, "_kill_pid", lambda pid: (killed.append(pid), alive.discard(pid)))
    m._service_stop()
    assert killed == [7777]


def test_service_stop_over_a_same_port_forward_still_stops_the_stray_daemon(tmp_path, monkeypatch):
    """The forward was published on the local daemon's own configured port;
    the stray daemon (no pid file) still holds that port's singleton lock."""
    _forward_down(tmp_path, monkeypatch)
    killed = []
    _stop_setup(monkeypatch, killed)
    same = m._service_port()  # the forwarded port
    monkeypatch.setattr(m, "_configured_port", lambda: same)
    alive = {7777}
    monkeypatch.setattr(m, "_pid_from_lock",
                        lambda port: 7777 if port == same and 7777 in alive else None)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid, *_a: pid in alive)
    monkeypatch.setattr(m, "_kill_pid", lambda pid: (killed.append(pid), alive.discard(pid)))
    m._service_stop()
    assert killed == [7777]  # never 4242, the ssh session holding the forward


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
    recovered: list[bool] = []

    def _recover(*_a, **_k):
        recovered.append(True)
        return {"recovered": False, "reason": "clean"}

    monkeypatch.setattr(zdd.breadcrumb, "recover_stale_cutover", _recover)

    def _args(*, json_out: bool, recover: bool):
        return type(
            "Args",
            (),
            {
                "health_timeout": 1,
                "drain_timeout": 1,
                "force": False,
                "json": json_out,
                "recover": recover,
            },
        )()

    # The output flag never selects lifecycle work: both modes skip before
    # stale-cutover recovery and passive reaping.
    venue_cli._cmd_deploy(_args(json_out=True, recover=False))
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped"] is True
    assert payload["ok"] is False
    assert "no local daemon to deploy" in payload["error"]
    venue_cli._cmd_deploy(_args(json_out=False, recover=False))
    assert "no local daemon to deploy" in capsys.readouterr().out
    assert recovered == []
    # ``--recover`` is the one mode that continues: in both output modes.
    for json_out in (True, False):
        with pytest.raises(SystemExit) as exc:
            venue_cli._cmd_deploy(_args(json_out=json_out, recover=True))
        assert exc.value.code == 0
    assert recovered == [True, True]
    capsys.readouterr()


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


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
@pytest.mark.parametrize(
    ("active", "runtime", "expected"),
    [
        (FORWARD, True, True),
        (BOUND_PIDLESS, True, False),
        (BOUND_PIDLESS, False, True),  # nothing can read the table: fail closed
    ],
)
def test_install_sh_forward_guard_uses_the_managed_runtime(active, runtime, expected, tmp_path):
    """Before this run's slot exists, the guard still reads the route through the
    managed runtime resolver; with no interpreter at all it fails closed."""
    install_dir = tmp_path / "agent-bridge"
    install_dir.mkdir()
    (install_dir / "active.json").write_text(json.dumps({"active": active}))
    text = _INSTALL_SH.read_text(encoding="utf-8")
    fn = text.split("_active_is_forward() {", 1)[1].split("\n}\n\n_active_host", 1)[0]
    rt = f"printf '%s' '{sys.executable}'" if runtime else "return 1"
    script = (
        f"INSTALL_DIR={install_dir!s}; VENV_DIR={tmp_path!s}/missing; PATH=/nonexistent\n"
        f"_rt_python() {{ {rt}; }}\n_bootstrap_python() {{ return 1; }}\n"
        "_active_is_forward() {" + fn + "\n}\n_active_is_forward\n"
    )
    bash = shutil.which("bash")
    result = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert (result.returncode == 0) is expected


def test_install_sh_update_checks_forward_before_lifecycle_actions():
    text = _INSTALL_SH.read_text(encoding="utf-8")
    helper = text.split("_update_lifecycle_drain_stop() {", 1)[1].split("\n}\n\n_update_lifecycle_start", 1)[0]
    body = text.split("do_update() {", 1)[1].split("\n}\n\ncase", 1)[0]
    forward_at = body.index("active_forward=false")
    revalidate_at = body.index('_update_lifecycle_drain_stop "$predecessor_signature"')
    start_at = body.index('_update_lifecycle_start "$predecessor_signature"')
    assert forward_at < revalidate_at < start_at
    assert helper.index("_update_lifecycle_still_targets_predecessor") < helper.index("_drain_service") \
        < helper.rindex("_update_lifecycle_still_targets_predecessor") < helper.index("do_stop")
    assert 'if [[ "$active_forward" == true ]]; then' in body
    assert 'Forwarded host bridge route still active -- not starting a local daemon' in body
    assert 'Forwarded host bridge route appeared during update -- skipping drain/stop/start' in text
    assert 'Active route changed during update -- skipping drain/stop/start' in text
    assert '&& "$active_forward" != true' in body
    assert '_update_lifecycle_drain_stop "$predecessor_signature" 30' in body
    assert '_update_lifecycle_start "$predecessor_signature" "Starting service..."' in body
    assert '_update_lifecycle_start "$predecessor_signature" "Restarting the previous version..."' in body
    assert '_update_lifecycle_start "$predecessor_signature" "Restarting the previous service..."' in body


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


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
@pytest.mark.parametrize(
    ("current", "starts", "stops"),
    [("", True, False), ("sig", True, True), ("other", False, False)],
)
def test_install_sh_update_start_accepts_the_route_its_own_stop_cleared(current, starts, stops):
    text = _INSTALL_SH.read_text(encoding="utf-8")
    helpers = text.split("_update_lifecycle_still_targets_predecessor() {", 1)[1].split(
        "\n}\n\n_active_host", 1)[0]
    script = (
        "_active_is_forward() { return 1; }\n"
        f"_active_signature() {{ printf '%s' '{current}'; }}\n"
        "_step() { :; }\n_drain_service() { :; }\n"
        "do_start() { echo STARTED; }\ndo_stop() { echo STOPPED; }\n"
        "_update_lifecycle_still_targets_predecessor() {" + helpers + "\n}\n"
        "_update_lifecycle_start sig || true\n_update_lifecycle_drain_stop sig || true\n"
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert ("STARTED" in out) is starts
    assert ("STOPPED" in out) is stops


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
@pytest.mark.parametrize(
    ("after", "forward", "stops"),
    [
        ("sig", False, True),     # still the pinned predecessor after the drain
        ("", False, True),        # the drained predecessor exited and cleared its route
        ("other", False, False),  # another daemon took the route during the drain
        ("sig", True, False),     # a venue forward published during the drain
    ],
)
def test_install_sh_update_drain_stop_revalidates_the_route_after_draining(after, forward, stops, tmp_path):
    """The drain can block for its full timeout; a forward published meanwhile
    must not be stopped by the update's last-resort port cleanup."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    helpers = text.split("_update_lifecycle_still_targets_predecessor() {", 1)[1].split(
        "\n}\n\n_active_host", 1)[0]
    marker = tmp_path / "drained"
    script = (
        f"_drained() {{ [[ -e '{marker.as_posix()}' ]]; }}\n"
        f"_active_is_forward() {{ _drained && {'true' if forward else 'false'}; }}\n"
        f"_active_signature() {{ if _drained; then printf '%s' '{after}'; else printf sig; fi; }}\n"
        "_step() { :; }\n_warn() { :; }\n"
        f"_drain_service() {{ touch '{marker.as_posix()}'; echo DRAINED; }}\n"
        "do_stop() { echo STOPPED; }\ndo_start() { :; }\n"
        "_update_lifecycle_still_targets_predecessor() {" + helpers + "\n}\n"
        "_pinned_base_url() { echo http://127.0.0.1:9280; }\n"
        "_update_lifecycle_drain_stop sig && echo RESULT=0 || echo RESULT=1\n"
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "DRAINED" in out
    assert ("STOPPED" in out) is stops
    assert ("RESULT=0" in out) is stops


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
@pytest.mark.parametrize(("after", "acts"), [("", True), ('{"port":41000}', False)])
def test_install_sh_an_empty_pin_expects_the_route_to_stay_empty(after, acts, tmp_path):
    """An empty pin is the legacy fixed-port predecessor with no route; a daemon
    that publishes one during the drain is a successor and is never stopped (and
    allow-absent never lets a start run over it)."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    helpers = text.split("_update_lifecycle_still_targets_predecessor() {", 1)[1].split(
        "\n}\n\n_active_host", 1)[0]
    marker = tmp_path / "drained"
    script = (
        f"_drained() {{ [[ -e '{marker.as_posix()}' ]]; }}\n"
        "_active_is_forward() { return 1; }\n"
        f"_active_signature() {{ if _drained; then printf '%s' '{after}'; fi; }}\n"
        "_step() { :; }\n_warn() { :; }\nPORT=9280\n"
        f"_drain_service() {{ touch '{marker.as_posix()}'; }}\n"
        "do_stop() { echo STOPPED; }\ndo_start() { echo STARTED; }\n"
        "_update_lifecycle_still_targets_predecessor() {" + helpers + "\n}\n"
        "_update_lifecycle_drain_stop '' || true\n_update_lifecycle_start '' || true\n"
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert ("STOPPED" in out) is acts
    assert ("STARTED" in out) is acts


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
@pytest.mark.parametrize(
    ("signature", "url"),
    [
        ('{"bind":"127.0.0.1","generation":7,"pid":123,"port":41000}', "http://127.0.0.1:41000"),
        ('{"bind":"0.0.0.0","generation":null,"pid":null,"port":41000}', "http://127.0.0.1:41000"),
        ('{"bind":"::","generation":null,"pid":null,"port":41000}', "http://[::1]:41000"),
        ("", "http://127.0.0.1:9280"),  # no route: the fixed-port daemon
    ],
)
def test_install_sh_update_drain_is_pinned_to_the_validated_predecessor(signature, url):
    """A route rewritten (e.g. to a forward) after validation can't redirect the
    drain: it targets the predecessor's own endpoint via AGENT_BRIDGE_BASE_URL."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    helpers = text.split("_pinned_base_url() {", 1)[1].split("\n}\n\n_update_lifecycle_start", 1)[0]
    script = (
        "PORT=9280\n"
        "_update_lifecycle_still_targets_predecessor() { return 0; }\n"
        '_drain_service() { echo "DRAIN=${AGENT_BRIDGE_BASE_URL:-}"; }\n'
        "do_stop() { :; }\n_warn() { echo \"WARN $*\"; }\n"
        f"_route_python() {{ printf '%s' '{sys.executable}'; }}\n"
        "_pinned_base_url() {" + helpers + "\n}\n"
        f"_update_lifecycle_drain_stop '{signature}'\n"
        'echo "AFTER=${AGENT_BRIDGE_BASE_URL:-unset}"\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert f"DRAIN={url}" in out
    assert "AFTER=unset" in out  # pinned only for the drain itself
