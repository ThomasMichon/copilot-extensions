"""Real isolated Git and CLI receiver qualification, not provider adoption."""

import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import config
from agent_worktrees.lease_cli import run_lease
from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_protocol import ProtocolError, format_timestamp, serialize_record
from agent_worktrees.lease_store import GitLeaseStore, ServiceCustodyConflict, ServiceCustodySnapshot
from agent_worktrees.service_custody_protocol import serialize_custody
from service_custody_support import BASE, custody_record
from test_lease_store import git, remote_oid

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_worktrees.service_custody_receiver"),
]


def append_raw(remote, ref, parent, message):
    """Test-only producer: the receiver deliberately has no adoption writer."""
    tree = git("--git-dir", str(remote), "mktree", input_text="").stdout.strip()
    args = ["--git-dir", str(remote), "commit-tree", tree]
    if parent:
        args.extend(["-p", parent])
    oid = git(*args, input_text=message + "\n").stdout.strip()
    git("--git-dir", str(remote), "update-ref", ref, oid, parent)
    return oid


@pytest.fixture
def adopted(tmp_path):
    remote = tmp_path / "coordination.git"
    git("init", "--bare", str(remote))
    settings = LeaseSettings(
        origin=str(remote), default_ttl_seconds=60, max_ttl_seconds=3600,
        clock_skew_seconds=10,
    )
    client = GitLeaseStore(settings, now=lambda: BASE)
    task = client.acquire("machine", "service-host", "task-owner")
    renewed = client.renew("machine", "service-host", task.oid)
    record = custody_record(
        predecessor=renewed.oid,
        accepted_at=format_timestamp(BASE + timedelta(seconds=50)),
    )
    oid = append_raw(remote, task.ref, renewed.oid, serialize_custody(record))
    return remote, settings, renewed, record, oid


def test_canonical_mixed_history_survives_client_restart_and_elapsed_years(adopted):
    remote, settings, task, record, oid = adopted
    client = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=3650))
    snapshot = client.inspect_resource("machine", "service-host")
    assert isinstance(snapshot, ServiceCustodySnapshot)
    assert snapshot.record == record
    assert snapshot.oid == remote_oid(remote, task.ref) == oid
    assert git("--git-dir", str(remote), "rev-list", "--count", oid).stdout.strip() == "3"
    values = client.list_resources()
    assert values == [snapshot]
    assert not {"live", "safe_deadline", "expires_at", "ttl_seconds"} & snapshot.to_dict().keys()
    assert snapshot.to_dict()["custody_type"] == "service"
    with pytest.raises(ServiceCustodyConflict):
        client.inspect("machine", "service-host")
    with pytest.raises(ServiceCustodyConflict):
        client.list()
    assert client.squash_stale(retention_days=1) == []
    assert remote_oid(remote, task.ref) == oid


@pytest.mark.parametrize("client_margin", [10, 30, 120])
def test_custody_freezes_acceptance_and_predecessor_history_margin(adopted, client_margin):
    remote, settings, task, record, oid = adopted
    git("--git-dir", str(remote), "update-ref", task.ref, task.oid, oid)
    stamp = format_timestamp(BASE + timedelta(seconds=50))
    transferred = replace(
        task.record, event="transfer", lease_id="c" * 32, holder="next-owner",
        issued_at=stamp, renewed_at=stamp,
        expires_at=format_timestamp(BASE + timedelta(seconds=110)),
    )
    parent = append_raw(remote, task.ref, task.oid, serialize_record(transferred))
    record = replace(
        record, predecessor=parent,
        accepted_at=format_timestamp(BASE + timedelta(seconds=60)),
    )
    adopted_oid = append_raw(remote, task.ref, parent, serialize_custody(record))
    reader = GitLeaseStore(replace(settings, clock_skew_seconds=client_margin))
    snapshot = reader.inspect_resource("machine", "service-host")
    assert snapshot.record == record and snapshot.oid == adopted_oid
    with pytest.raises(ServiceCustodyConflict):
        reader.release("machine", "service-host", adopted_oid)
    assert remote_oid(remote, task.ref) == adopted_oid


@pytest.mark.parametrize("operation", ["acquire", "borrow", "renew", "release", "transfer"])
@pytest.mark.parametrize("fence", ["task", "service"])
def test_all_task_mutations_refuse_service_custody_with_both_fences(adopted, operation, fence):
    remote, settings, task, _record, oid = adopted
    client = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=3650))
    token = task.oid if fence == "task" else oid
    if operation in {"acquire", "borrow"}:
        args = ["machine", "service-host", "task-owner"]
    else:
        args = ["machine", "service-host", token]
        if operation == "transfer":
            args.append("other-owner")
    with pytest.raises(ServiceCustodyConflict) as error:
        getattr(client, operation)(*args)
    assert error.value.snapshot.oid == oid
    assert remote_oid(remote, task.ref) == oid


def test_cli_reports_permanent_inventory_and_structured_task_refusal(adopted, capsys):
    remote, _settings, task, _record, oid = adopted
    for verb in ("inspect", "status"):
        assert run_lease([verb, "machine", "service-host", "--origin", str(remote)]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["state"] == "service-owned" and data["token"] == oid
        assert "expires_at" not in data
    assert run_lease(["list", "--origin", str(remote)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["custody_type"] == "service"
    for verb in ("renew", "release"):
        assert run_lease([
            verb, "machine", "service-host", "--token", task.oid,
            "--origin", str(remote),
        ]) == 3
        result = capsys.readouterr()
        assert not result.out
        assert json.loads(result.err)["code"] == "service_custody_conflict"
    assert remote_oid(remote, task.ref) == oid


@pytest.mark.parametrize(("verb", "code"), [("inspect", 0), ("release", 3)])
def test_fresh_cli_process_reads_and_refuses_without_controller(adopted, verb, code):
    remote, _settings, task, _record, oid = adopted
    project = config.project_dir("custody-test")
    project.mkdir(parents=True)
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "custody-test",
        "machine": "test",
        "repos": {"custody-test": {"anchor": str(remote.parent)}},
    }), encoding="utf-8")
    argv = [verb, "machine", "service-host", "--origin", str(remote)]
    if verb == "release":
        argv.extend(["--token", oid])
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "--project", "custody-test", "lease", *argv],
        capture_output=True, text=True, timeout=15, check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == code, result.stderr
    data = json.loads(result.stdout if code == 0 else result.stderr)
    if code == 0:
        assert data["state"] == "service-owned" and data["token"] == oid
    else:
        assert data["code"] == "service_custody_conflict" and not result.stdout
    assert remote_oid(remote, task.ref) == oid


def test_mixed_reader_rejects_task_successor_even_after_original_expiry(adopted):
    remote, settings, task, _record, oid = adopted
    takeover = replace(
        task.record, event="takeover", lease_id="c" * 32,
        issued_at=format_timestamp(BASE + timedelta(days=1)),
        renewed_at=format_timestamp(BASE + timedelta(days=1)),
        expires_at=format_timestamp(BASE + timedelta(days=1, seconds=60)),
    )
    append_raw(remote, task.ref, oid, serialize_record(takeover))
    with pytest.raises(ProtocolError, match="cannot be changed"):
        GitLeaseStore(settings).inspect_resource("machine", "service-host")


def test_history_rejects_renewal_race_and_wrong_predecessor(adopted):
    remote, settings, task, record, oid = adopted
    bad = replace(record, predecessor="d" * 40)
    # A different current task fence must not be accepted merely because its
    # payload otherwise matches an earlier approved proposal.
    git("--git-dir", str(remote), "update-ref", task.ref, task.oid, oid)
    append_raw(remote, task.ref, task.oid, serialize_custody(bad))
    with pytest.raises(ProtocolError, match="Git parent"):
        GitLeaseStore(settings).inspect_resource("machine", "service-host")


def test_already_admitted_task_renewal_cannot_overwrite_custody(adopted, monkeypatch):
    remote, settings, task, record, oid = adopted
    git("--git-dir", str(remote), "update-ref", task.ref, task.oid, oid)
    client = GitLeaseStore(settings, now=lambda: BASE)
    original = client._transition
    published = []

    def adopt_before_task_push(item, expected, renewed):
        published.append(append_raw(
            remote, task.ref, task.oid, serialize_custody(record),
        ))
        return original(item, expected, renewed)

    monkeypatch.setattr(client, "_transition", adopt_before_task_push)
    from agent_worktrees.lease_store import LeaseLost

    with pytest.raises(LeaseLost, match="compare-and-swap"):
        client.renew("machine", "service-host", task.oid)
    assert remote_oid(remote, task.ref) == published[0]
    assert isinstance(client.inspect_resource("machine", "service-host"), ServiceCustodySnapshot)
