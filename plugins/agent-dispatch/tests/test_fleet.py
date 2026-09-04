"""Tests for fleet dispatch -- a health-gated remote embody pool (Model C).

Covers host selection + the liveness capacity gate (`fleet.py`), the Model-C
remote-embody seed/argv (`embody.py`), and the supervisor's `capacity_gate`
integration (an asleep pool defers a task without burning a spawn attempt).
"""

from __future__ import annotations

import subprocess

import pytest

from agent_dispatch import embody, fleet
from agent_dispatch.queue import SpawnState
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_supervisor import QueueBackedClient

# -- fixtures ----------------------------------------------------------------


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def client(q):
    return QueueBackedClient(q)


def _fake_spawn(record: list | None = None, *, ok: bool = True, rc: int = 0):
    """A fake `spawn_fleet_embodied_worker` returning a CompletedProcess-like."""
    import subprocess

    def spawn(
        host,
        task_id,
        *,
        origin,
        owner,
        worker_id,
        driver,
        project=None,
        repo=None,
        all_repos=False,
        verify_timeout=0,
    ):
        if record is not None:
            record.append(
                {"host": host, "task_id": task_id, "origin": origin, "owner": owner,
                 "project": project, "repo": repo, "all_repos": all_repos}
            )
        stdout = '{"worktree_id": "wt-x", "session_id": "sess-x"}' if ok else ""
        return subprocess.CompletedProcess(
            args=["ssh"], returncode=rc, stdout=stdout, stderr="" if ok else "boom"
        )

    return spawn


def _fake_headless_spawn(record: list | None = None, *, ok: bool = True, rc: int = 0):
    """A fake `spawn_fleet_headless_worker` returning a CompletedProcess-like.

    Mirrors the headless-fleet spawn signature (an ``agent``, no ``driver`` /
    ``verify_timeout`` / ``project``), so a test can assert the FleetSpawner
    routes a headless body through it with the configured agent.
    """
    import subprocess

    def spawn(
        host,
        task_id,
        *,
        origin,
        owner,
        worker_id,
        agent,
        repo=None,
        all_repos=False,
    ):
        if record is not None:
            record.append(
                {"host": host, "task_id": task_id, "origin": origin, "owner": owner,
                 "agent": agent, "repo": repo, "all_repos": all_repos}
            )
        return subprocess.CompletedProcess(
            args=["ssh"], returncode=rc, stdout="", stderr="" if ok else "boom"
        )

    return spawn


# -- host selection + liveness gate ------------------------------------------


def test_select_first_live_host_in_config_order():
    live = {"host-b"}
    f = fleet.FleetSpawner(
        ["host-a", "host-b", "host-c"], origin="orig",
        liveness=lambda h: h in live,
    )
    # host-a asleep, host-b live -> host-b chosen
    assert f.select({"id": "t1"}) == "host-b"
    assert f.can_spawn({"id": "t1"}) is True


def test_all_asleep_pool_selects_nothing():
    f = fleet.FleetSpawner(["a", "b"], origin="orig", liveness=lambda h: False)
    assert f.select({"id": "t1"}) is None
    assert f.can_spawn({"id": "t1"}) is False


def test_target_machine_affinity_tried_first():
    seen = []

    def live(h):
        seen.append(h)
        return True  # first probed is chosen

    f = fleet.FleetSpawner(["a", "b", "c"], origin="orig", liveness=live)
    assert f.select({"id": "t1", "target_machine": "c"}) == "c"
    assert seen[0] == "c"  # the pinned host was probed first


def test_target_machine_outside_pool_ignored():
    f = fleet.FleetSpawner(["a", "b"], origin="orig", liveness=lambda h: True)
    # 'z' is not in the pool -> normal config order, first host wins
    assert f.select({"id": "t1", "target_machine": "z"}) == "a"


def test_liveness_result_is_cached_within_ttl():
    calls = {"n": 0}

    def live(h):
        calls["n"] += 1
        return True

    clock = {"t": 1000.0}
    f = fleet.FleetSpawner(
        ["a"], origin="orig", liveness=live, now=lambda: clock["t"]
    )
    f.can_spawn({"id": "t1"})
    f.can_spawn({"id": "t1"})  # within TTL -> no re-probe
    assert calls["n"] == 1
    clock["t"] += fleet._LIVENESS_TTL + 1  # expire the cache
    f.can_spawn({"id": "t1"})
    assert calls["n"] == 2


def test_empty_pool_rejected():
    with pytest.raises(ValueError):
        fleet.FleetSpawner([], origin="orig")
    with pytest.raises(ValueError):
        fleet.FleetSpawner(["a"], origin="  ")


# -- FleetSpawner.__call__ (SpawnFn contract) --------------------------------


def test_call_success_builds_handle_with_host_and_owner():
    rec: list = []
    f = fleet.FleetSpawner(
        ["a", "b"], origin="orig",
        liveness=lambda h: h == "b",
        spawn_fn=_fake_spawn(rec),
    )
    ok, handle = f({"id": "t1", "repo": "gitea.example/org/widgets"})
    assert ok is True
    assert handle["machine"] == "b"
    assert handle["worktree"] == "wt-x"
    assert handle["session"] == "sess-x"
    assert handle["owner"].startswith("fleet-t1-")
    # the same synthetic owner was handed to the remote body
    assert rec[0]["owner"] == handle["owner"]
    assert rec[0]["host"] == "b"
    assert rec[0]["origin"] == "orig"
    # the task's lane was resolved to a project name for the CWD-neutral body
    assert rec[0]["project"] == "widgets"
    assert rec[0]["repo"] == "gitea.example/org/widgets"
    assert rec[0]["all_repos"] is False


def test_all_repos_fleet_spawn_carries_explicit_claim_mode():
    rec: list = []
    spawner = fleet.FleetSpawner(
        ["a"],
        origin="orig",
        all_repos=True,
        liveness=lambda _host: True,
        spawn_fn=_fake_spawn(rec),
    )

    assert spawner({"id": "t1", "repo": TEST_REPO})[0] is True
    assert rec[0]["repo"] is None
    assert rec[0]["all_repos"] is True


def test_selection_cache_is_released_after_successful_spawn():
    """The per-cycle selection cache is dropped once a task is spawned, so it
    stays bounded to in-flight selections over a long-running supervisor."""
    f = fleet.FleetSpawner(
        ["a"], origin="orig", liveness=lambda h: True, spawn_fn=_fake_spawn()
    )
    f.can_spawn({"id": "t1"})  # populates the selection cache
    assert "t1" in f._selection
    ok, _ = f({"id": "t1"})
    assert ok is True
    assert "t1" not in f._selection  # released after spawn


def test_call_no_live_host_defers():
    f = fleet.FleetSpawner(["a"], origin="orig", liveness=lambda h: False)
    ok, handle = f({"id": "t1"})
    assert ok is False
    assert handle.get("deferred") is True


def test_call_reports_remote_failure():
    f = fleet.FleetSpawner(
        ["a"], origin="orig", liveness=lambda h: True,
        spawn_fn=_fake_spawn(ok=False, rc=127),
    )
    ok, handle = f({"id": "t1"})
    assert ok is False
    assert "failed" in handle["error"]


# -- Model-C seed + remote embody argv ---------------------------------------


def test_fleet_seed_drives_origin_over_ssh_with_explicit_owner():
    seed = embody.fleet_autopilot_worker_prompt(
        "t42", origin="brain", owner="fleet-t42-abc123", worker_id="fleet-t42-abc123"
    )
    # every lifecycle verb reaches the origin over ssh, with the explicit owner
    assert "ssh brain agent-dispatch claim --task t42 --worker fleet-t42-abc123" in seed
    assert "ssh brain agent-dispatch start t42 fleet-t42-abc123" in seed
    assert (
        "ssh brain agent-dispatch steer take t42 fleet-t42-abc123 --all"
        in seed
    )
    assert "ssh brain agent-dispatch complete t42 fleet-t42-abc123 --result-ref" in seed
    assert "ssh brain agent-dispatch progress t42 fleet-t42-abc123" in seed
    # Contract-net evaluation window (dev55) over the SSH mesh: evaluation claim,
    # then accept / decline (yield --exclude-self) / retire (abandon --duplicate-of),
    # all carrying the explicit owner.
    assert (
        "ssh brain agent-dispatch claim --task t42 --worker fleet-t42-abc123 --evaluation"
        in seed
    )
    assert "ssh brain agent-dispatch yield t42 fleet-t42-abc123 --exclude-self machine" in seed
    assert (
        "ssh brain agent-dispatch abandon t42 --worker-id fleet-t42-abc123 --duplicate-of"
        in seed
    )


def test_fleet_seed_carries_explicit_all_repos_claim_mode():
    seed = embody.fleet_autopilot_worker_prompt(
        "t42",
        origin="brain",
        owner="fleet-t42-abc123",
        worker_id="fleet-t42-abc123",
        all_repos=True,
    )
    assert (
        "ssh brain agent-dispatch claim --task t42 --worker "
        "fleet-t42-abc123 --evaluation --all-repos"
    ) in seed


def test_spawn_fleet_embodied_worker_builds_ssh_embody_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        import subprocess

        captured["cmd"] = cmd
        captured["kwargs"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", fake_run)
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "create_session",
        _remote_unavailable,
    )

    embody.spawn_fleet_embodied_worker(
        "Host-B", "t7", origin="brain", owner="fleet-t7-xyz", worker_id="fleet-t7-xyz"
    )
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ssh"
    assert "BatchMode=yes" in cmd
    assert cmd[3] == "host-b"  # alias lowercased
    remote = cmd[4]
    assert remote.startswith("agent-worktrees embody --new --seed ")
    assert "--driver agent-dispatch" in remote
    assert "--json" in remote
    # the seed rides inside the (shlex-quoted) remote command
    assert "ssh brain agent-dispatch claim --task t7 --worker fleet-t7-xyz" in remote


def test_spawn_fleet_embodied_worker_requires_ssh(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: None)
    with pytest.raises(embody.EmbodyUnavailable):
        embody.spawn_fleet_embodied_worker(
            "a", "t1", origin="brain", owner="o", worker_id="o"
        )


# -- headless-fleet: agent-bridge ACP body on the pool host ------------------


def test_host_can_bridge_probes_agent_bridge(monkeypatch):
    """The headless-fleet liveness probe checks for `agent-bridge`, not
    `agent-worktrees` (a headless body embodies via the pool host's bridge)."""
    captured = {}

    def fake_run(cmd, **kw):
        import subprocess

        captured["cmd"] = cmd
        captured["kwargs"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="/usr/bin/agent-bridge", stderr="")

    monkeypatch.setattr(fleet.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_bridge("Host-B") is True
    cmd = captured["cmd"]
    assert cmd[-1] == "command -v agent-bridge"
    assert cmd[-2] == "host-b"  # alias lowercased
    assert captured["kwargs"]["timeout"] == 8.0


def test_spawn_fleet_headless_worker_builds_ssh_agent_bridge_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        import subprocess

        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", fake_run)

    embody.spawn_fleet_headless_worker(
        "Host-B", "t7", origin="brain", owner="fleet-t7-xyz", worker_id="fleet-t7-xyz",
        agent="review-worker",
    )
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ssh"
    assert "BatchMode=yes" in cmd
    assert cmd[3] == "host-b"  # alias lowercased
    remote = cmd[4]
    # a headless ACP body via the pool host's agent-bridge, fire-and-forget, with
    # --json (global) so the created session_id rides stdout for the recovery handle
    assert remote.startswith("agent-bridge --json create review-worker ")
    assert "--no-wait" in remote
    # the SAME fleet seed as the CLI body rides inside the remote command
    assert "ssh brain agent-dispatch claim --task t7 --worker fleet-t7-xyz" in remote
    assert "ssh brain agent-dispatch complete t7 fleet-t7-xyz --result-ref" in remote


def test_spawn_fleet_headless_worker_requires_ssh(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "create_session",
        _remote_unavailable,
    )
    with pytest.raises(embody.EmbodyUnavailable):
        embody.spawn_fleet_headless_worker(
            "a", "t1", origin="brain", owner="o", worker_id="o"
        )


# -- headless-fleet recovery handle + liveness verdict -----------------------


def test_parse_fleet_body_session_reads_session_id():
    import subprocess
    # the real `create --json` output has human preamble before the JSON object
    real = (
        "[>] Starting session for agent 'anomalous-potato-wsl'...\n"
        "[>] Session f0c4c81e-351 (bold-flame) created\n"
        '{\n  "session_id": "f0c4c81e-351",\n  "status": "running"\n}\n'
    )
    cp = subprocess.CompletedProcess(args=[], returncode=0, stdout=real, stderr="")
    assert embody.parse_fleet_body_session(cp) == "f0c4c81e-351"
    # clean JSON (no preamble) still works
    clean = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"session_id": "abc-123"}', stderr="",
    )
    assert embody.parse_fleet_body_session(clean) == "abc-123"
    # a miss (no json / no id) degrades to None (no recovery handle recorded)
    bad = subprocess.CompletedProcess(args=[], returncode=0, stdout="[>] no json here", stderr="")
    assert embody.parse_fleet_body_session(bad) is None
    empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
    assert embody.parse_fleet_body_session(empty) is None


def _fake_status_run(rc, stdout, stderr=""):
    import subprocess

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)

    return run


def _remote_unavailable(*_args, **_kwargs):
    raise embody.bridge_remote.RemoteBridgeUnavailable("not installed")


@pytest.mark.parametrize(
    "rc,stdout,stderr,expected",
    [
        (0, '{"status": "running"}', "", "live"),
        (0, '{"status": "idle"}', "", "live"),          # idle == alive between turns
        (0, '{"status": "stopped"}', "", "gone"),
        (0, '{"status": "completed"}', "", "gone"),
        (0, '{"status": "running", "liveness": "dead"}', "", "gone"),  # liveness wins
        (1, "", "[FAIL] Session x not found", "gone"),   # absent -> gone
        (1, "", "ssh: connect: Connection refused", "unknown"),  # transport -> unknown
        (0, "not json", "", "unknown"),
        (0, '{"status": "mystery"}', "", "unknown"),     # unrecognized -> unknown
    ],
)
def test_fleet_body_verdict_classifies(monkeypatch, rc, stdout, stderr, expected):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", _fake_status_run(rc, stdout, stderr))
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        _remote_unavailable,
    )
    assert embody.fleet_body_verdict("Host-B", "sid-1") == expected


def test_fleet_body_verdict_unknown_without_ssh(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        _remote_unavailable,
    )
    assert embody.fleet_body_verdict("h", "sid") == "unknown"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ('{"status":"running","liveness":"active"}', "ACTIVE"),
        ('{"status":"running","liveness":"stalled"}', "STALLED"),
        ('{"status":"idle","liveness":"idle"}', "IDLE"),
    ],
)
def test_fleet_body_activity_classifies(monkeypatch, payload, expected):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", _fake_status_run(0, payload))
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        _remote_unavailable,
    )
    assert embody.fleet_body_activity("Host-B", "sid-1") == expected


def test_headless_create_uses_carrier_without_ssh(monkeypatch):
    calls = {}

    def create(_self, host, **kwargs):
        calls.update(host=host, **kwargs)
        return {"session_id": "bridge-1"}

    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient, "create_session", create
    )
    monkeypatch.setattr(
        embody.shutil,
        "which",
        lambda _name: pytest.fail("carrier-backed create must not resolve ssh"),
    )

    result = embody.spawn_fleet_headless_worker(
        "Host-B",
        "t7",
        origin="brain",
        owner="fleet-t7-xyz",
        worker_id="fleet-t7-xyz",
        agent="review-worker",
    )

    assert result.returncode == 0
    assert embody.parse_fleet_body_session(result) == "bridge-1"
    assert calls["host"] == "Host-B"
    assert calls["agent"] == "review-worker"
    assert calls["caller_id"] == "fleet-t7-xyz"


def test_carrier_status_preserves_tri_state_without_ssh(monkeypatch):
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        lambda *_args, **_kwargs: {"status": "idle"},
    )
    monkeypatch.setattr(
        embody.shutil,
        "which",
        lambda _name: pytest.fail("carrier-backed status must not resolve ssh"),
    )
    assert embody.fleet_body_verdict("Host-B", "sid-1") == "live"


def test_carrier_not_found_is_gone_without_ssh_fallback(monkeypatch):
    def missing(*_args, **_kwargs):
        raise embody.bridge_remote.RemoteBridgeOperationError(
            "missing", status=404, code="session_not_found"
        )

    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        missing,
    )
    monkeypatch.setattr(
        embody.shutil,
        "which",
        lambda _name: pytest.fail("carrier operation errors must not fall back"),
    )
    assert embody.fleet_body_verdict("Host-B", "sid-1") == "gone"


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["ssh"], 20),
        subprocess.SubprocessError("ssh failed"),
        OSError("ssh unavailable"),
    ],
)
def test_stop_fleet_body_normalizes_ssh_fallback_errors(monkeypatch, error):
    monkeypatch.setattr(embody.shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        embody.bridge_remote.LocalBridgeRemoteClient,
        "end_session",
        _remote_unavailable,
    )

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(embody, "run_ssh_command", fail)

    assert embody.stop_fleet_body("Host-B", "sid-1") is False


def test_headless_call_encodes_fleet_body_recovery_handle():
    """A headless spawn whose create returned a session_id records a
    `fleet-body:<host>:<sid>` recovery handle (so the body is auto-recoverable)."""
    import subprocess

    def spawn(
        host,
        task_id,
        *,
        origin,
        owner,
        worker_id,
        agent,
        repo=None,
        all_repos=False,
    ):
        return subprocess.CompletedProcess(
            args=["ssh"], returncode=0,
            stdout='{"session_id": "brg-77"}', stderr="",
        )

    f = fleet.FleetSpawner(
        ["anomalous-potato-wsl"], origin="orig", headless=True, agent="review-worker",
        liveness=lambda h: True, spawn_fn=spawn,
    )
    ok, handle = f({"id": "t1"})
    assert ok is True
    assert handle["worktree"] is None
    assert handle["session"] == "fleet-body:anomalous-potato-wsl:brg-77"


def test_headless_call_without_session_id_falls_back_to_owner():
    """If the create output has no session_id, the handle degrades to the synthetic
    owner (the body still runs; it just isn't auto-recovered)."""
    import subprocess

    def spawn(
        host,
        task_id,
        *,
        origin,
        owner,
        worker_id,
        agent,
        repo=None,
        all_repos=False,
    ):
        return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")

    f = fleet.FleetSpawner(
        ["h"], origin="orig", headless=True, liveness=lambda h: True, spawn_fn=spawn,
    )
    ok, handle = f({"id": "t1"})
    assert ok is True
    assert handle["session"] == handle["owner"]
    assert not handle["session"].startswith("fleet-body:")


def test_headless_fleetspawner_defaults_to_bridge_liveness_and_headless_body(monkeypatch):
    """A headless FleetSpawner defaults its liveness to host_can_bridge and its
    body to spawn_fleet_headless_worker (no injection)."""
    monkeypatch.setattr(fleet, "host_can_bridge", lambda _h, **_kw: True)
    f = fleet.FleetSpawner(["a"], origin="orig", headless=True)
    assert f.headless is True
    assert f._spawn is embody.spawn_fleet_headless_worker
    assert f.select({"id": "t1"}) == "a"  # bridge-liveness default admits it


def test_headless_call_routes_agent_and_records_no_worktree():
    """A headless spawn is driven through the headless body with the configured
    agent, and its handle carries NO worktree (a headless body is not a worktree)."""
    rec: list = []
    f = fleet.FleetSpawner(
        ["a"], origin="orig", headless=True, agent="review-worker",
        liveness=lambda h: True, spawn_fn=_fake_headless_spawn(rec),
    )
    ok, handle = f({"id": "t1", "repo": "gitea.example/org/widgets"})
    assert ok is True
    assert handle["machine"] == "a"
    assert handle["worktree"] is None  # headless body is not a worktree
    assert handle["session"] == handle["owner"]
    assert handle["owner"].startswith("fleet-t1-")
    # the configured agent-bridge agent was handed to the headless body
    assert rec[0]["agent"] == "review-worker"
    assert rec[0]["owner"] == handle["owner"]


def test_headless_call_reports_remote_failure():
    f = fleet.FleetSpawner(
        ["a"], origin="orig", headless=True, liveness=lambda h: True,
        spawn_fn=_fake_headless_spawn(ok=False, rc=127),
    )
    ok, handle = f({"id": "t1"})
    assert ok is False
    assert "failed" in handle["error"]


# -- Supervisor capacity_gate integration ------------------------------------


def test_asleep_pool_defers_without_burning_an_attempt(q, client):
    """When the whole pool is asleep, the capacity gate defers the task: no
    reservation is created, so no spawn attempt is burned toward dead-letter."""
    t = q.create("work")
    rec: list = []
    f = fleet.FleetSpawner(
        ["a"], origin="orig", liveness=lambda h: False, spawn_fn=_fake_spawn(rec)
    )
    sup = Supervisor(
        client, spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    assert sup.poll_once() == []
    assert rec == []  # nothing dispatched
    assert q.latest_reservation(t.id) is None  # crucially: NO reservation burned


def test_live_pool_reserves_and_spawns(q, client):
    t = q.create("work")
    rec: list = []
    live = {"h": True}
    f = fleet.FleetSpawner(
        ["h"], origin="orig", liveness=lambda _h: live["h"], spawn_fn=_fake_spawn(rec)
    )
    sup = Supervisor(
        client, spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    assert sup.poll_once() == [t.id]
    assert rec and rec[0]["task_id"] == t.id
    res = q.latest_reservation(t.id)
    assert res.state == SpawnState.SPAWNED
    assert res.worktree == "wt-x"

    # a second cycle does not re-spawn the same task (active reservation holds)
    assert sup.poll_once() == []


def test_no_capacity_gate_preserves_local_behavior(q, client):
    """Default (no capacity_gate) admits every eligible task -- the local path
    is unchanged."""
    t = q.create("work")
    calls = []

    def spawn(task):
        calls.append(task["id"])
        return True, {"session": "s", "worktree": "w"}

    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    assert sup.poll_once() == [t.id]
    assert calls == [t.id]
    assert q.latest_reservation(t.id).state == SpawnState.SPAWNED


# -- local body verdict (probe THIS host's bridge directly, no SSH) ------------


@pytest.mark.parametrize(
    "rc,stdout,stderr,expected",
    [
        (0, '{"status": "running"}', "", "live"),
        (0, '{"status": "idle"}', "", "live"),          # idle == alive between turns
        (0, '{"status": "stopped"}', "", "gone"),
        (0, '{"status": "ended"}', "", "gone"),         # `agent-bridge end` -> gone
        (0, '{"status": "running", "liveness": "dead"}', "", "gone"),  # liveness wins
        (1, "", "[FAIL] Session x not found", "gone"),   # absent -> gone
        (1, "", "connection refused", "unknown"),        # transport -> unknown
        (0, "not json", "", "unknown"),
        (0, '{"status": "mystery"}', "", "unknown"),     # unrecognized -> unknown
    ],
)
def test_local_body_verdict_classifies(monkeypatch, rc, stdout, stderr, expected):
    from agent_dispatch import bridge

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["agent-bridge"]
    )
    monkeypatch.setattr(embody.subprocess, "run", _fake_status_run(rc, stdout, stderr))
    assert embody.local_body_verdict("sid-1") == expected


def test_local_body_verdict_unknown_without_bridge(monkeypatch):
    from agent_dispatch import bridge

    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)
    assert embody.local_body_verdict("sid") == "unknown"
