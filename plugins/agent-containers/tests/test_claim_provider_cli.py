"""Claim-provider callback tests for agent-containers (claim-provider-pattern
effort): ``claim-status``/``claim-reclaim`` for the ``container:`` namespace
agent-worktrees' claim-provider registry resolves.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import types

from agent_containers import claim_provider_cli as cpc
from agent_containers import lifecycle
from agent_containers.lease import DeployHoldError


def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@contextlib.contextmanager
def _fake_hold(name, operation):
    yield types.SimpleNamespace(container=name, operation=operation)


def _fake_hold_raises(name, operation):
    raise DeployHoldError(f"Container '{name}' already has a provider {operation} hold")


def test_claim_status_exists(monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "_docker", lambda *a, **k: _proc(0, stdout="running\n"))
    rc = cpc.cmd_claim_status(argparse.Namespace(name="c-a"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": True, "state": "running"}


def test_claim_status_absent(monkeypatch, capsys):
    monkeypatch.setattr(
        lifecycle, "_docker",
        lambda *a, **k: _proc(1, stderr="Error: No such container: c-missing"))
    rc = cpc.cmd_claim_status(argparse.Namespace(name="c-missing"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"exists": False}


def test_claim_status_backend_failure_is_not_false_absence(monkeypatch, capsys):
    """A real Docker backend error (daemon unreachable, permission denied,
    ...) must exit non-zero -- never a false ``exists: false``."""
    monkeypatch.setattr(
        lifecycle, "_docker",
        lambda *a, **k: _proc(1, stderr="Cannot connect to the Docker daemon"))
    rc = cpc.cmd_claim_status(argparse.Namespace(name="c-a"))
    assert rc != 0
    assert capsys.readouterr().out == ""


def test_claim_status_docker_unavailable_is_not_false_absence(monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("docker CLI not found on PATH")
    monkeypatch.setattr(lifecycle, "_docker", _boom)
    rc = cpc.cmd_claim_status(argparse.Namespace(name="c-a"))
    assert rc != 0
    assert capsys.readouterr().out == ""


def test_claim_status_uses_a_bounded_docker_timeout(monkeypatch, capsys):
    """The registry's own status-callback budget is 15s; the inner docker
    call must leave real margin under it, not use docker's own 30s
    subprocess default."""
    captured = {}

    def _docker(args, **k):
        captured.update(k)
        return _proc(0, stdout="running\n")
    monkeypatch.setattr(lifecycle, "_docker", _docker)
    cpc.cmd_claim_status(argparse.Namespace(name="c-a"))
    assert captured.get("timeout", 999) < 15


def test_claim_reclaim_dry_run_never_removes(monkeypatch, capsys):
    called = {"n": 0}
    monkeypatch.setattr(lifecycle, "remove_container",
                        lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=False))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "would remove" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_apply_success(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "get_lease", lambda name: None)
    monkeypatch.setattr(cpc, "deploy_hold", _fake_hold)
    monkeypatch.setattr(cpc, "active_session_admissions", lambda name: [])
    monkeypatch.setattr(lifecycle, "remove_container", lambda *a, **k: None)
    released = {}
    monkeypatch.setattr(cpc, "release_lease",
                        lambda name: released.setdefault("name", name) or True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "removed" in out["detail"]
    assert released["name"] == "c-a"


def test_claim_reclaim_refuses_actively_leased_container(monkeypatch, capsys):
    """A stale worktree claim must never destroy a container another
    effort currently holds -- mirrors ``lifecycle.cmd_remove``'s own
    guard."""
    called = {"n": 0}
    lease = types.SimpleNamespace(effort="other-effort")
    monkeypatch.setattr(cpc, "get_lease", lambda name: lease)
    monkeypatch.setattr(lifecycle, "remove_container",
                        lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "other-effort" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_refuses_active_session_admission(monkeypatch, capsys):
    """A restricted `exec --stdio` session can hold a container WITHOUT an
    advisory lease at all -- must still block a destructive removal.
    Checked INSIDE the atomic deploy_hold fence, so no new admission can
    start between this check and the removal below."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "get_lease", lambda name: None)
    monkeypatch.setattr(cpc, "deploy_hold", _fake_hold)
    monkeypatch.setattr(cpc, "active_session_admissions",
                        lambda name: [types.SimpleNamespace(token="t1")])
    monkeypatch.setattr(lifecycle, "remove_container",
                        lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "admission" in out["detail"]
    assert called["n"] == 0


def test_claim_reclaim_refuses_when_another_operation_holds_the_fence(monkeypatch, capsys):
    """``deploy_hold`` itself raises when another destructive operation
    already holds the container -- this callback must degrade to a refusal
    rather than propagate/crash."""
    called = {"n": 0}
    monkeypatch.setattr(cpc, "get_lease", lambda name: None)
    monkeypatch.setattr(cpc, "deploy_hold", _fake_hold_raises)
    monkeypatch.setattr(lifecycle, "remove_container",
                        lambda *a, **k: called.__setitem__("n", 1))
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False
    assert called["n"] == 0


def test_claim_reclaim_already_gone_is_idempotent(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "get_lease", lambda name: None)
    monkeypatch.setattr(cpc, "deploy_hold", _fake_hold)
    monkeypatch.setattr(cpc, "active_session_admissions", lambda name: [])

    def _boom(*a, **k):
        raise RuntimeError("docker rm c-a failed: Error: No such container: c-a")
    monkeypatch.setattr(lifecycle, "remove_container", _boom)
    released = {}
    monkeypatch.setattr(cpc, "release_lease",
                        lambda name: released.setdefault("name", name) or True)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is True and "already gone" in out["detail"]
    assert released["name"] == "c-a"


def test_claim_reclaim_real_failure(monkeypatch, capsys):
    monkeypatch.setattr(cpc, "get_lease", lambda name: None)
    monkeypatch.setattr(cpc, "deploy_hold", _fake_hold)
    monkeypatch.setattr(cpc, "active_session_admissions", lambda name: [])

    def _boom(*a, **k):
        raise RuntimeError("docker rm c-a failed: permission denied")
    monkeypatch.setattr(lifecycle, "remove_container", _boom)
    rc = cpc.cmd_claim_reclaim(argparse.Namespace(name="c-a", apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reclaimed"] is False and "permission denied" in out["detail"]
