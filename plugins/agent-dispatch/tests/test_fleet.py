"""Tests for fleet dispatch -- a health-gated remote embody pool (Model C).

Covers host selection + the liveness capacity gate (`fleet.py`), the Model-C
remote-embody seed/argv (`embody.py`), and the supervisor's `capacity_gate`
integration (an asleep pool defers a task without burning a spawn attempt).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest

from agent_dispatch import bridge_remote, embody, embody_prompts, fleet
from agent_dispatch.client import DispatchClient
from agent_dispatch.queue import TaskQueue as BaseTaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnState
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_supervisor import QueueBackedClient

# -- fixtures ----------------------------------------------------------------


def _create(q: BaseTaskQueue, title: str) -> Task:
    task = q.create(title)
    assert isinstance(task, Task)
    return task


@pytest.fixture
def q(tmp_path: Path) -> TaskQueue:
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture(autouse=True)
def _isolated_probe_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no remembered host shells and the default bound."""
    monkeypatch.setattr(fleet, "_SHELL_HINTS", {})
    monkeypatch.delenv("AGENT_DISPATCH_FLEET_PROBE_TIMEOUT", raising=False)


@pytest.fixture
def client(q: TaskQueue) -> QueueBackedClient:
    return QueueBackedClient(q)


def _fake_spawn(
    record: list[dict[str, Any]] | None = None, *, ok: bool = True, rc: int = 0,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A fake `spawn_fleet_embodied_worker` returning a CompletedProcess-like."""
    import subprocess

    def spawn(
        host: str,
        task_id: str,
        *,
        origin: str,
        owner: str,
        worker_id: str,
        machine: str | None = None,
        driver: str,
        project: str | None = None,
        repo: str | None = None,
        all_repos: bool = False,
        verify_timeout: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        if record is not None:
            record.append(
                {"host": host, "task_id": task_id, "origin": origin, "owner": owner,
                 "machine": machine, "project": project, "repo": repo,
                 "all_repos": all_repos}
            )
        stdout = '{"worktree_id": "wt-x", "session_id": "sess-x"}' if ok else ""
        return subprocess.CompletedProcess(
            args=["ssh"], returncode=rc, stdout=stdout, stderr="" if ok else "boom"
        )

    return spawn


def _fake_headless_spawn(
    record: list[dict[str, Any]] | None = None, *, ok: bool = True, rc: int = 0,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A fake `spawn_fleet_headless_worker` returning a CompletedProcess-like.

    Mirrors the headless-fleet spawn signature (an ``agent``, no ``driver`` /
    ``verify_timeout`` / ``project``), so a test can assert the FleetSpawner
    routes a headless body through it with the configured agent.
    """
    import subprocess

    def spawn(
        host: str,
        task_id: str,
        *,
        origin: str,
        owner: str,
        worker_id: str,
        machine: str | None = None,
        agent: str,
        charter: str | None = None,
        repo: str | None = None,
        all_repos: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if record is not None:
            record.append(
                {"host": host, "task_id": task_id, "origin": origin, "owner": owner,
                 "machine": machine, "agent": agent, "repo": repo,
                 "all_repos": all_repos}
            )
        return subprocess.CompletedProcess(
            args=["ssh"], returncode=rc, stdout="", stderr="" if ok else "boom"
        )

    return spawn


# -- host selection + liveness gate ------------------------------------------

@pytest.mark.parametrize("authority,route", [("central", " --shared"), ("local", " --local")])
@pytest.mark.parametrize("headless", [False, True])
def test_fleet_body_retains_origin_authority_instead_of_ssh_login_default(monkeypatch: pytest.MonkeyPatch, authority: str, route: str, headless: bool) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    observed: list[str] = []

    def observe(seed: str) -> None:
        assert f"ssh origin-host agent-dispatch{route} show task-1" in seed
        assert f"ssh origin-host agent-dispatch{route} claim" in seed
        observed.append(seed)

    if headless:
        class Carrier:
            def create_session(self, host: str, **kwargs: Any) -> dict[str, str]:
                observe(kwargs["prompt"])
                return {"session_id": "session-1"}

        monkeypatch.setattr(bridge_remote, "LocalBridgeRemoteClient", Carrier)
    else:
        monkeypatch.setattr(shutil, "which", lambda _command: "/fixture/ssh")

        def ssh(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            command = shlex.split(argv[-1])
            observe(command[command.index("--seed") + 1])
            return subprocess.CompletedProcess(
                [], 0, '{"worktree_id":"worktree-1","session_id":"session-1"}', "",
            )

        monkeypatch.setattr(embody, "run_ssh_command", ssh)

    spawner = fleet.FleetSpawner(
        ["pool-host"], origin="origin-host", headless=headless,
        liveness=lambda _host: True,
    )
    assert spawner({"id": "task-1", "repo": TEST_REPO})[0] is True
    assert len(observed) == 1


def test_fleet_explicit_local_route_overrides_central_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    assert fleet.FleetSpawner(["pool-host"], origin="origin", route=" --local").route == " --local"
    with pytest.raises(ValueError, match="require origin discovery"):
        fleet.FleetSpawner(["pool-host"], origin="origin", route=" --url http://raw:8787")


def test_select_first_live_host_in_config_order() -> None:
    live = {"host-b"}
    f = fleet.FleetSpawner(
        ["host-a", "host-b", "host-c"], origin="orig",
        liveness=lambda h: h in live,
    )
    # host-a asleep, host-b live -> host-b chosen
    assert f.select({"id": "t1"}) == "host-b"
    assert f.can_spawn({"id": "t1"}) is True


def test_all_asleep_pool_selects_nothing() -> None:
    f = fleet.FleetSpawner(["a", "b"], origin="orig", liveness=lambda h: False)
    assert f.select({"id": "t1"}) is None
    assert f.can_spawn({"id": "t1"}) is False


def test_target_machine_pin_runs_only_on_that_host() -> None:
    seen: list[str] = []

    def live(h: str) -> bool:
        seen.append(h)
        return True

    f = fleet.FleetSpawner(["a", "b", "c"], origin="orig", liveness=live)
    assert f.select({"id": "t1", "target_machine": "c"}) == "c"
    assert seen == ["c"]  # no other pool host is probed for a pinned task


def test_asleep_pinned_host_defers_without_fallback() -> None:
    seen: list[str] = []

    def live(h: str) -> bool:
        seen.append(h)
        return h != "c"

    f = fleet.FleetSpawner(["a", "b", "c"], origin="orig", liveness=live)
    # another host could not claim a task pinned to 'c', so wait for 'c'
    assert f.select({"id": "t1", "target_machine": "c"}) is None
    assert f.can_spawn({"id": "t1", "target_machine": "c"}) is False
    assert seen == ["c"]


def test_target_machine_pin_matches_pool_host_case_insensitively() -> None:
    f = fleet.FleetSpawner(["host-a", "Host-B"], origin="orig", liveness=lambda h: True)
    assert f.select({"id": "t1", "target_machine": "host-b"}) == "Host-B"


def test_target_machine_outside_pool_has_no_candidate() -> None:
    f = fleet.FleetSpawner(["a", "b"], origin="orig", liveness=lambda h: True)
    # 'z' is not in the pool -> no fleet host could claim the pinned task
    assert f.select({"id": "t1", "target_machine": "z"}) is None
    ok, handle = f({"id": "t1", "target_machine": "z"})
    assert ok is False
    assert handle.get("deferred") is True


def test_machine_excludes_skip_that_host() -> None:
    f = fleet.FleetSpawner(["host-a", "host-b"], origin="orig", liveness=lambda h: True)
    # e.g. after `yield --exclude-self machine --machine host-a`
    assert f.select({"id": "t1", "excludes": ["machine:host-a"]}) == "host-b"
    # the queue row stores selectors as JSON text
    assert f.select({"id": "t2", "excludes": '["machine:host-a"]'}) == "host-b"


def test_machine_requires_limit_candidates() -> None:
    f = fleet.FleetSpawner(["host-a", "host-b"], origin="orig", liveness=lambda h: True)
    assert f.select({"id": "t1", "requires": ["machine:host-b", "os:linux"]}) == "host-b"
    assert f.select({"id": "t2", "requires": ["machine:host-z"]}) is None


def test_excluded_pinned_host_has_no_candidate() -> None:
    f = fleet.FleetSpawner(["host-a", "host-b"], origin="orig", liveness=lambda h: True)
    task = {"id": "t1", "target_machine": "host-a", "excludes": ["machine:host-a"]}
    assert f.select(task) is None


def test_liveness_result_is_cached_within_ttl() -> None:
    calls: dict[str, int] = {"n": 0}

    def live(h: str) -> bool:
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


def test_empty_pool_rejected() -> None:
    with pytest.raises(ValueError):
        fleet.FleetSpawner([], origin="orig")
    with pytest.raises(ValueError):
        fleet.FleetSpawner(["a"], origin="  ")


def test_charter_rejected_for_cli_embodied_fleet() -> None:
    """A non-headless (CLI-embodied) fleet has no charter-binding path -- fail
    fast at construction rather than silently dropping the requested charter
    when spawn_fleet_embodied_worker is invoked without it."""
    with pytest.raises(embody.EmbodyUnavailable):
        fleet.FleetSpawner(
            ["a"], origin="orig", headless=False, charter="cab-charter"
        )
    # A headless fleet accepts the same charter without complaint.
    fleet.FleetSpawner(["a"], origin="orig", headless=True, charter="cab-charter")


# -- FleetSpawner.__call__ (SpawnFn contract) --------------------------------


def test_call_success_builds_handle_with_host_and_owner() -> None:
    rec: list[dict[str, Any]] = []
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
    # the body is told which host it runs on, for --machine on claim/yield
    assert rec[0]["machine"] == "b"
    assert rec[0]["origin"] == "orig"
    # the task's lane was resolved to a project name for the CWD-neutral body
    assert rec[0]["project"] == "widgets"
    assert rec[0]["repo"] == "gitea.example/org/widgets"
    assert rec[0]["all_repos"] is False


def test_all_repos_fleet_spawn_carries_explicit_claim_mode() -> None:
    rec: list[dict[str, Any]] = []
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


def test_selection_cache_is_released_after_successful_spawn() -> None:
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


def test_call_no_live_host_defers() -> None:
    f = fleet.FleetSpawner(["a"], origin="orig", liveness=lambda h: False)
    ok, handle = f({"id": "t1"})
    assert ok is False
    assert handle.get("deferred") is True


def test_call_reports_remote_failure() -> None:
    f = fleet.FleetSpawner(
        ["a"], origin="orig", liveness=lambda h: True,
        spawn_fn=_fake_spawn(ok=False, rc=127),
    )
    ok, handle = f({"id": "t1"})
    assert ok is False
    assert "failed" in handle["error"]


# -- Model-C seed + remote embody argv ---------------------------------------


def test_fleet_seed_drives_origin_over_ssh_with_explicit_owner() -> None:
    seed = embody_prompts.fleet_autopilot_worker_prompt(
        "t42", origin="brain", owner="fleet-t42-abc123", worker_id="fleet-t42-abc123"
    )
    # every lifecycle verb reaches the origin over ssh, with the explicit owner
    assert "ssh brain agent-dispatch charter show operating-procedures" in seed
    assert "ssh brain agent-dispatch charter show autopilot" in seed
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


def test_fleet_seed_carries_explicit_all_repos_claim_mode() -> None:
    seed = embody_prompts.fleet_autopilot_worker_prompt(
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


def test_fleet_seed_carries_executing_machine_on_claim_and_yield() -> None:
    seed = embody_prompts.fleet_autopilot_worker_prompt(
        "t42", origin="brain", owner="fleet-t42-abc123",
        worker_id="fleet-t42-abc123", machine="host-b",
    )
    assert (
        "ssh brain agent-dispatch claim --task t42 --worker fleet-t42-abc123 "
        "--evaluation --machine host-b`"
    ) in seed
    assert (
        "ssh brain agent-dispatch yield t42 fleet-t42-abc123 --exclude-self "
        "machine --machine host-b --note"
    ) in seed

    unpinned = embody_prompts.fleet_autopilot_worker_prompt(
        "t42", origin="brain", owner="fleet-t42-abc123", worker_id="fleet-t42-abc123"
    )
    assert "--machine" not in unpinned


def test_fleet_seed_duplicate_check_sweep_shares_the_claim_lane_scope() -> None:
    # dev-cycle regression: the duplicate-check `list` sweep must respect the
    # same repo scoping as the claim step, not silently sweep every lane on
    # the origin (or default to whatever lane the origin happens to be in).
    seed = embody_prompts.fleet_autopilot_worker_prompt(
        "t42",
        origin="brain",
        owner="fleet-t42-abc123",
        worker_id="fleet-t42-abc123",
        repo="github.com/o/n",
    )
    assert "ssh brain agent-dispatch list --repo github.com/o/n" in seed

    all_repos_seed = embody_prompts.fleet_autopilot_worker_prompt(
        "t42",
        origin="brain",
        owner="fleet-t42-abc123",
        worker_id="fleet-t42-abc123",
        all_repos=True,
    )
    assert "ssh brain agent-dispatch list --all-repos" in all_repos_seed


def test_spawn_fleet_embodied_worker_builds_ssh_embody_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        import subprocess

        captured["cmd"] = cmd
        captured["kwargs"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", fake_run)
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
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


def test_spawn_fleet_embodied_worker_requires_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    with pytest.raises(embody.EmbodyUnavailable):
        embody.spawn_fleet_embodied_worker(
            "a", "t1", origin="brain", owner="o", worker_id="o"
        )


# -- headless-fleet: agent-bridge ACP body on the pool host ------------------


def test_host_can_bridge_probes_agent_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The headless-fleet liveness probe checks for `agent-bridge`, not
    `agent-worktrees` (a headless body embodies via the pool host's bridge)."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        import subprocess

        captured["cmd"] = cmd
        captured["kwargs"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="/usr/bin/agent-bridge", stderr="")

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_bridge("Host-B") is True
    cmd = captured["cmd"]
    assert cmd[-1] == "case posix in posix) command -v agent-bridge;; esac"
    assert cmd[-2] == "host-b"  # alias lowercased
    assert captured["kwargs"]["timeout"] == 15.0


def _probe_runner(
    results: list[tuple[int, str]],
) -> tuple[Callable[..., subprocess.CompletedProcess[str]], list[tuple[str, Any]]]:
    """A fake ``run_ssh_command`` returning queued ``(rc, stdout)`` per probe."""
    calls: list[tuple[str, Any]] = []

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append((cmd[-1], kw.get("timeout")))
        rc, stdout = results[len(calls) - 1]
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

    return fake_run, calls


def test_probe_rejects_empty_output_from_powershell_get_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """PowerShell turns ``command -v x`` into ``Get-Command``: exit 0, no output
    for a missing binary. That must not count as the binary being present."""
    fake_run, calls = _probe_runner([(0, ""), (1, "")])
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_embody("win-host") is False
    assert [c for c, _ in calls] == ["case posix in posix) command -v agent-worktrees;; esac"]


def test_host_bridge_shell_classifies_powershell_host_with_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell answers a bare ``command -v`` through ``Get-Command`` when
    Bridge exists, but rejects the ``case`` probe; such a host is live for the
    carrier route yet classified as Windows, not POSIX."""
    fake_run, calls = _probe_runner(
        [(1, ""), (0, "C:\\bin\\agent-bridge.cmd\r\n")]
    )
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_bridge_shell("win-host") == fleet.WINDOWS_SHELL
    assert [c for c, _ in calls] == [
        "case posix in posix) command -v agent-bridge;; esac", "where.exe agent-bridge",
    ]


def test_host_can_bridge_falls_back_to_where_exe_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_run, calls = _probe_runner(
        [(1, "'command' is not recognized"), (0, "C:\\bin\\agent-bridge.cmd\r\n")]
    )
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_bridge("win-host") is True
    assert [c for c, _ in calls] == [
        "case posix in posix) command -v agent-bridge;; esac", "where.exe agent-bridge",
    ]
    # each round trip gets the full bound; the fallback is not starved
    assert calls[0][1] == 15.0
    assert calls[1][1] == 15.0


def test_slow_windows_host_is_live_when_each_round_trip_nears_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a relayed SSH hop takes ~4-8 s per round trip. The POSIX
    probe used to consume most of a shared budget, so ``where.exe`` timed out
    and a healthy Windows host was reported not live."""
    calls: list[float] = []

    def slow_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        timeout = kw["timeout"]
        calls.append(timeout)
        # each relayed round trip takes 9 s: slow, yet a healthy host
        if timeout < 9.0:
            raise subprocess.TimeoutExpired(cmd, timeout)
        if cmd[-1].startswith("where.exe"):
            return subprocess.CompletedProcess(cmd, 0, stdout="C:\\b\\agent-bridge.cmd\r\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not recognized")

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", slow_run)
    assert fleet.host_can_bridge("mdb7") is True
    assert calls == [15.0, 15.0]


def test_known_windows_host_needs_one_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a host answers under Windows, later probes try ``where.exe`` first."""
    fake_run, calls = _probe_runner(
        [(1, ""), (0, "C:\\b\\agent-bridge.cmd"), (0, "C:\\b\\agent-bridge.cmd")]
    )
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_bridge_shell("Win-Host") == fleet.WINDOWS_SHELL
    assert fleet.host_bridge_shell("win-host") == fleet.WINDOWS_SHELL
    assert [c for c, _ in calls][2:] == ["where.exe agent-bridge"]


def test_stale_windows_hint_still_classifies_posix_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong hint costs one extra round trip, never a wrong classification."""
    monkeypatch.setitem(fleet._SHELL_HINTS, "host", fleet.WINDOWS_SHELL)
    fake_run, calls = _probe_runner([(127, ""), (0, "/usr/bin/agent-bridge")])
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_bridge_shell("host") == fleet.POSIX_SHELL
    assert [c for c, _ in calls] == [
        "where.exe agent-bridge", "case posix in posix) command -v agent-bridge;; esac",
    ]
    assert fleet._SHELL_HINTS["host"] == fleet.POSIX_SHELL


def test_dead_host_fails_within_one_probe_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that never answers is not probed twice: one timeout ends it."""
    calls: list[float] = []

    def hang(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kw["timeout"])
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", hang)
    assert fleet.host_can_bridge("asleep-host") is False
    assert calls == [15.0]


@pytest.mark.parametrize(("raw", "expected"), [("22.5", 22.5), ("0", 15.0), ("inf", 15.0), ("x", 15.0)])
def test_probe_timeout_is_configurable(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_FLEET_PROBE_TIMEOUT", raw)
    fake_run, calls = _probe_runner([(0, "/usr/bin/agent-bridge")])
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_bridge("host") is True
    assert calls[0][1] == expected


def test_host_can_bridge_skips_fallback_on_ssh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persistent SSH-level failure is retried once, then ends the probe
    without the ``where.exe`` fallback."""
    fake_run, calls = _probe_runner([(255, ""), (255, "")])
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_bridge("asleep-host") is False
    assert [c for c, _ in calls] == ["case posix in posix) command -v agent-bridge;; esac"] * 2


def test_transient_ssh_failure_on_cold_relay_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a cold relayed hop can drop the first connect (exit 255);
    a live Windows host must not be reported not live for it."""
    fake_run, calls = _probe_runner(
        [(255, ""), (1, "not recognized"), (0, "C:\\b\\agent-bridge.cmd\r\n")]
    )
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_bridge_shell("mdb4") == fleet.WINDOWS_SHELL
    assert len(calls) == 3


def test_host_can_embody_has_no_windows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CLI-embodied body runs a POSIX remote command, so only POSIX hosts
    qualify; ``where.exe`` is never tried."""
    fake_run, calls = _probe_runner([(1, ""), (0, "C:\\x.cmd")])
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    assert fleet.host_can_embody("win-host") is False
    assert len(calls) == 1


def test_spawn_fleet_headless_worker_builds_ssh_agent_bridge_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        import subprocess

        captured["cmd"] = cmd
        out = "/usr/bin/agent-bridge\n" if cmd[-1].startswith("case posix in posix)") else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    def unavailable_client() -> NoReturn:
        raise bridge_remote.RemoteBridgeUnavailable()

    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", fake_run)
    monkeypatch.setattr(fleet, "run_ssh_command", fake_run)
    monkeypatch.setattr(bridge_remote, "LocalBridgeRemoteClient", unavailable_client)

    embody.spawn_fleet_headless_worker(
        "  Host-B  ",
        "t7",
        origin="brain",
        owner="fleet-t7-xyz",
        worker_id="fleet-t7-xyz",
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


def test_spawn_fleet_headless_worker_requires_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
        "create_session",
        _remote_unavailable,
    )
    with pytest.raises(embody.EmbodyUnavailable):
        embody.spawn_fleet_headless_worker(
            "a", "t1", origin="brain", owner="o", worker_id="o"
        )


# -- headless-fleet recovery handle + liveness verdict -----------------------


def test_parse_fleet_body_session_reads_session_id() -> None:
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


def _fake_status_run(rc: int, stdout: str, stderr: str = "") -> Callable[..., subprocess.CompletedProcess[str]]:
    import subprocess

    def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)

    return run


def _remote_unavailable(*_args: object, **_kwargs: object) -> NoReturn:
    raise bridge_remote.RemoteBridgeUnavailable("not installed")


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
def test_fleet_body_verdict_classifies(monkeypatch: pytest.MonkeyPatch, rc: int, stdout: str, stderr: str, expected: str) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", _fake_status_run(rc, stdout, stderr))
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        _remote_unavailable,
    )
    assert embody.fleet_body_verdict("Host-B", "sid-1") == expected


def test_fleet_body_verdict_unknown_without_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
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
def test_fleet_body_activity_classifies(monkeypatch: pytest.MonkeyPatch, payload: str, expected: str) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", _fake_status_run(0, payload))
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        _remote_unavailable,
    )
    assert embody.fleet_body_activity("Host-B", "sid-1") == expected


def test_headless_create_uses_carrier_without_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def create(_self: object, host: str, **kwargs: Any) -> dict[str, str]:
        calls.update(host=host, **kwargs)
        return {"session_id": "bridge-1"}

    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient, "create_session", create
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda _name: pytest.fail("carrier-backed create must not resolve ssh"),
    )

    result = embody.spawn_fleet_headless_worker(
        "  Host-B  ",
        "t7",
        origin="brain",
        owner="fleet-t7-xyz",
        worker_id="fleet-t7-xyz",
        agent="review-worker",
    )

    assert result.returncode == 0
    assert embody.parse_fleet_body_session(result) == "bridge-1"
    assert calls["host"] == "host-b"
    assert calls["agent"] == "review-worker"
    assert calls["caller_id"] == "fleet-t7-xyz"


def test_carrier_status_preserves_tri_state_without_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def status(_self: object, host: str, session_id: str, **kwargs: Any) -> dict[str, str]:
        calls.update(host=host, session_id=session_id, **kwargs)
        return {"status": "idle"}

    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        status,
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda _name: pytest.fail("carrier-backed status must not resolve ssh"),
    )
    assert embody.fleet_body_verdict("  Host-B  ", "sid-1") == "live"
    assert calls["host"] == "host-b"


def test_carrier_end_and_activity_normalize_host_without_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str, dict[str, Any]]] = []

    def end(_self: object, host: str, session_id: str, **kwargs: Any) -> None:
        calls.append(("end", host, session_id, kwargs))

    def status(_self: object, host: str, session_id: str, **kwargs: Any) -> dict[str, str]:
        calls.append(("status", host, session_id, kwargs))
        return {"status": "running", "liveness": "active"}

    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient, "end_session", end
    )
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient, "session_status", status
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda _name: pytest.fail("carrier operations must not resolve ssh"),
    )

    assert embody.stop_fleet_body("  Host-B  ", "sid-1")
    assert embody.fleet_body_activity("  Host-B  ", "sid-1") == "ACTIVE"
    assert [call[1] for call in calls] == ["host-b", "host-b"]


def test_carrier_not_found_is_gone_without_ssh_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> NoReturn:
        raise bridge_remote.RemoteBridgeOperationError(
            "missing", status=404, code="session_not_found"
        )

    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
        "session_status",
        missing,
    )
    monkeypatch.setattr(
        shutil,
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
def test_stop_fleet_body_normalizes_ssh_fallback_errors(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ssh")
    monkeypatch.setattr(
        bridge_remote.LocalBridgeRemoteClient,
        "end_session",
        _remote_unavailable,
    )

    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(embody, "run_ssh_command", fail)

    assert embody.stop_fleet_body("Host-B", "sid-1") is False


def test_headless_call_encodes_fleet_body_recovery_handle() -> None:
    """A headless spawn whose create returned a session_id records a
    `fleet-body:<host>:<sid>` recovery handle (so the body is auto-recoverable)."""
    import subprocess

    def spawn(
        host: str,
        task_id: str,
        *,
        origin: str,
        owner: str,
        worker_id: str,
        machine: str | None = None,
        agent: str,
        charter: str | None = None,
        repo: str | None = None,
        all_repos: bool = False,
    ) -> subprocess.CompletedProcess[str]:
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


def test_headless_call_without_session_id_falls_back_to_owner() -> None:
    """If the create output has no session_id, the handle degrades to the synthetic
    owner (the body still runs; it just isn't auto-recovered)."""
    import subprocess

    def spawn(
        host: str,
        task_id: str,
        *,
        origin: str,
        owner: str,
        worker_id: str,
        machine: str | None = None,
        agent: str,
        charter: str | None = None,
        repo: str | None = None,
        all_repos: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")

    f = fleet.FleetSpawner(
        ["h"], origin="orig", headless=True, liveness=lambda h: True, spawn_fn=spawn,
    )
    ok, handle = f({"id": "t1"})
    assert ok is True
    assert handle["session"] == handle["owner"]
    assert not handle["session"].startswith("fleet-body:")


def test_headless_fleetspawner_defaults_to_bridge_liveness_and_headless_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A headless FleetSpawner defaults its liveness to host_can_bridge and its
    body to spawn_fleet_headless_worker (no injection)."""
    monkeypatch.setattr(fleet, "host_can_bridge", lambda _h, **_kw: True)
    f = fleet.FleetSpawner(["a"], origin="orig", headless=True)
    assert f.headless is True
    assert f._spawn is embody.spawn_fleet_headless_worker
    assert f.select({"id": "t1"}) == "a"  # bridge-liveness default admits it


def test_headless_call_routes_agent_and_records_no_worktree() -> None:
    """A headless spawn is driven through the headless body with the configured
    agent, and its handle carries NO worktree (a headless body is not a worktree)."""
    rec: list[dict[str, Any]] = []
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
    assert rec[0]["machine"] == "a"


def test_headless_call_reports_remote_failure() -> None:
    f = fleet.FleetSpawner(
        ["a"], origin="orig", headless=True, liveness=lambda h: True,
        spawn_fn=_fake_headless_spawn(ok=False, rc=127),
    )
    ok, handle = f({"id": "t1"})
    assert ok is False
    assert "failed" in handle["error"]


# -- Supervisor capacity_gate integration ------------------------------------


def test_asleep_pool_defers_without_burning_an_attempt(q: BaseTaskQueue, client: QueueBackedClient) -> None:
    """When the whole pool is asleep, the capacity gate defers the task: no
    reservation is created, so no spawn attempt is burned toward dead-letter."""
    t = _create(q, "work")
    rec: list[dict[str, Any]] = []
    f = fleet.FleetSpawner(
        ["a"], origin="orig", liveness=lambda h: False, spawn_fn=_fake_spawn(rec)
    )
    sup = Supervisor(
        cast(DispatchClient, client), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    assert sup.poll_once() == []
    assert rec == []  # nothing dispatched
    assert q.latest_reservation(t.id) is None  # crucially: NO reservation burned


def test_live_pool_reserves_and_spawns(q: BaseTaskQueue, client: QueueBackedClient) -> None:
    t = _create(q, "work")
    rec: list[dict[str, Any]] = []
    live = {"h": True}
    f = fleet.FleetSpawner(
        ["h"], origin="orig", liveness=lambda _h: live["h"], spawn_fn=_fake_spawn(rec)
    )
    sup = Supervisor(
        cast(DispatchClient, client), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    assert sup.poll_once() == [t.id]
    assert rec and rec[0]["task_id"] == t.id
    res = q.latest_reservation(t.id)
    assert res is not None
    assert res.state == SpawnState.SPAWNED
    assert res.worktree == "wt-x"

    # a second cycle does not re-spawn the same task (active reservation holds)
    assert sup.poll_once() == []


def test_no_capacity_gate_preserves_local_behavior(q: BaseTaskQueue, client: QueueBackedClient) -> None:
    """Default (no capacity_gate) admits every eligible task -- the local path
    is unchanged."""
    t = _create(q, "work")
    calls: list[str] = []

    def spawn(task: dict[str, Any]) -> tuple[bool, dict[str, str]]:
        calls.append(task["id"])
        return True, {"session": "s", "worktree": "w"}

    sup = Supervisor(cast(DispatchClient, client), spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    assert sup.poll_once() == [t.id]
    assert calls == [t.id]
    res = q.latest_reservation(t.id)
    assert res is not None
    assert res.state == SpawnState.SPAWNED


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
def test_local_body_verdict_classifies(monkeypatch: pytest.MonkeyPatch, rc: int, stdout: str, stderr: str, expected: str) -> None:
    from agent_dispatch import bridge

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["agent-bridge"]
    )
    monkeypatch.setattr(subprocess, "run", _fake_status_run(rc, stdout, stderr))
    assert embody.local_body_verdict("sid-1") == expected


def test_local_body_verdict_unknown_without_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import bridge

    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)
    assert embody.local_body_verdict("sid") == "unknown"
