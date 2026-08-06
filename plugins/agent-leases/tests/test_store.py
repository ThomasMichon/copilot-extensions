from __future__ import annotations

import threading
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_leases.config import Settings
from agent_leases.protocol import (
    LeaseRecord,
    ProtocolError,
    format_timestamp,
    ref_for,
    resource,
    serialize_record,
)
from agent_leases.store import GitLeaseStore, LeaseConflict, LeaseLost

from conftest import git


BASE = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)


def store(settings: Settings, now: datetime = BASE) -> GitLeaseStore:
    return GitLeaseStore(
        settings,
        now=lambda: now,
        sleep=lambda _seconds: None,
        jitter=lambda _low, _high: 0,
    )


def remote_oid(remote: Path, ref: str) -> str:
    return git("--git-dir", str(remote), "rev-parse", ref).stdout.strip()


def commit_parents(remote: Path, oid: str) -> list[str]:
    return git("--git-dir", str(remote), "rev-list", "--parents", "-1", oid).stdout.split()


def push_raw_message(remote: Path, ref: str, message: str) -> str:
    repo = remote.parent / "raw.git"
    git("init", "--bare", str(repo))
    tree = git(f"--git-dir={repo}", "mktree", input_text="").stdout.strip()
    oid = git(
        f"--git-dir={repo}", "commit-tree", tree, input_text=message + "\n"
    ).stdout.strip()
    git(f"--git-dir={repo}", "update-ref", "refs/test/write", oid)
    git(f"--git-dir={repo}", "push", str(remote), f"refs/test/write:{ref}")
    return oid


def push_record(
    remote: Path,
    ref: str,
    record: LeaseRecord,
    *,
    nonempty: bool = False,
) -> str:
    repo = remote.parent / "record.git"
    git("init", "--bare", str(repo))
    if nonempty:
        blob = git(
            f"--git-dir={repo}", "hash-object", "-w", "--stdin", input_text="x"
        ).stdout.strip()
        tree = git(
            f"--git-dir={repo}",
            "mktree",
            input_text=f"100644 blob {blob}\tdata\n",
        ).stdout.strip()
    else:
        tree = git(f"--git-dir={repo}", "mktree", input_text="").stdout.strip()
    oid = git(
        f"--git-dir={repo}",
        "commit-tree",
        tree,
        input_text=serialize_record(record) + "\n",
    ).stdout.strip()
    git(f"--git-dir={repo}", "update-ref", "refs/test/write", oid)
    git(f"--git-dir={repo}", "push", str(remote), f"refs/test/write:{ref}")
    return oid


def test_absent_ref_acquisition_returns_fencing_token(
    remote: Path, settings: Settings
) -> None:
    lease = store(settings).acquire("codespace", "example", "host/session")
    assert lease.oid == remote_oid(remote, lease.ref)
    assert lease.record.lease_id
    assert lease.record.event == "acquire"
    assert commit_parents(remote, lease.oid) == [lease.oid]


def test_two_clients_racing_from_absence_produce_one_winner(
    settings: Settings,
) -> None:
    first = store(settings)
    second = store(settings)
    barrier = threading.Barrier(2)
    original_first = first.inspect
    original_second = second.inspect
    first_reads = 0
    second_reads = 0

    def inspect_first(kind: str, key: str):
        nonlocal first_reads
        result = original_first(kind, key)
        first_reads += 1
        if first_reads == 1:
            barrier.wait(timeout=10)
        return result

    def inspect_second(kind: str, key: str):
        nonlocal second_reads
        result = original_second(kind, key)
        second_reads += 1
        if second_reads == 1:
            barrier.wait(timeout=10)
        return result

    first.inspect = inspect_first  # type: ignore[method-assign]
    second.inspect = inspect_second  # type: ignore[method-assign]
    outcomes: list[object] = []

    def acquire(client: GitLeaseStore, holder: str) -> None:
        try:
            outcomes.append(client.acquire("machine", "runner", holder))
        except Exception as exc:
            outcomes.append(exc)

    threads = [
        threading.Thread(target=acquire, args=(first, "client-a")),
        threading.Thread(target=acquire, args=(second, "client-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads)
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, LeaseConflict) for item in outcomes) == 1


def test_renew_and_release_require_current_token(
    remote: Path, settings: Settings
) -> None:
    client = store(settings)
    acquired = client.acquire("container", "build", "holder")
    renewed = client.renew("container", "build", acquired.oid)
    with pytest.raises(LeaseLost, match="stale"):
        client.renew("container", "build", acquired.oid)
    with pytest.raises(LeaseLost, match="stale"):
        client.release("container", "build", acquired.oid)
    assert remote_oid(remote, renewed.ref) == renewed.oid


def test_stale_takeover_gets_new_lease_id_and_parents_old_head(
    settings: Settings,
) -> None:
    acquired = store(settings).acquire(
        "codespace", "stale", "old-holder", ttl_seconds=60
    )
    takeover = store(settings, BASE + timedelta(seconds=71)).acquire(
        "codespace", "stale", "new-holder", ttl_seconds=60
    )
    assert takeover.record.event == "takeover"
    assert takeover.record.lease_id != acquired.record.lease_id
    assert commit_parents(Path(settings.origin), takeover.oid) == [
        takeover.oid,
        acquired.oid,
    ]


def test_renewal_fails_after_safe_local_deadline(settings: Settings) -> None:
    acquired = store(settings).acquire(
        "machine", "deadline", "holder", ttl_seconds=60
    )
    with pytest.raises(LeaseLost, match="safe local deadline"):
        store(settings, BASE + timedelta(seconds=51)).renew(
            "machine", "deadline", acquired.oid
        )


def test_malformed_payload_fails_closed(remote: Path, settings: Settings) -> None:
    item = resource("machine", "malformed")
    ref = ref_for(settings.ref_prefix, item)
    push_raw_message(remote, ref, "not-an-agent-leases-envelope")
    with pytest.raises(ProtocolError, match="invalid envelope"):
        store(settings).inspect(item.kind, item.key)
    with pytest.raises(ProtocolError, match="invalid envelope"):
        store(settings).acquire(item.kind, item.key, "holder")


def test_malformed_commit_topology_fails_closed(
    remote: Path, settings: Settings
) -> None:
    item = resource("machine", "topology")
    ref = ref_for(settings.ref_prefix, item)
    record = LeaseRecord(
        schema_version=1,
        resource={"identity": item.identity, "kind": item.kind, "key": item.key},
        state="leased",
        event="acquire",
        lease_id="a" * 32,
        holder="holder",
        issued_at=format_timestamp(BASE),
        renewed_at=format_timestamp(BASE),
        expires_at=format_timestamp(BASE + timedelta(seconds=60)),
        ttl_seconds=60,
        context={},
    )
    push_record(remote, ref, record, nonempty=True)
    with pytest.raises(ProtocolError, match="empty tree"):
        store(settings).inspect(item.kind, item.key)


def test_remote_tracking_ref_and_shared_worktree_cannot_weaken_expected_oid(
    remote: Path,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = store(settings)
    acquired = client.acquire("remote-worktree", "shared", "holder")

    checkout = tmp_path / "caller"
    sibling = tmp_path / "sibling"
    git("init", str(checkout))
    git("-C", str(checkout), "remote", "add", "origin", str(remote))
    git("-C", str(checkout), "fetch", "origin", f"{acquired.ref}:refs/heads/base")
    git("-C", str(checkout), "worktree", "add", "-b", "sibling", str(sibling), "base")
    tracking = "refs/remotes/origin/copilot-lease"
    git("-C", str(checkout), "update-ref", tracking, acquired.oid)

    renewed = client.renew("remote-worktree", "shared", acquired.oid)
    git("-C", str(checkout), "update-ref", tracking, acquired.oid)
    monkeypatch.chdir(sibling)
    with pytest.raises(LeaseLost, match="stale"):
        client.release("remote-worktree", "shared", acquired.oid)
    assert remote_oid(remote, renewed.ref) == renewed.oid
    assert git("-C", str(sibling), "rev-parse", tracking).stdout.strip() == acquired.oid


def test_release_appends_tombstone_and_preserves_history_and_ref(
    remote: Path, settings: Settings
) -> None:
    client = store(settings)
    acquired = client.acquire("machine", "history", "holder")
    renewed = client.renew("machine", "history", acquired.oid)
    released = client.release("machine", "history", renewed.oid)
    assert released.record.state == "released"
    assert released.record.event == "release"
    assert remote_oid(remote, released.ref) == released.oid
    assert git(
        "--git-dir", str(remote), "rev-list", "--count", released.oid
    ).stdout.strip() == "3"
    assert commit_parents(remote, released.oid) == [released.oid, renewed.oid]


def test_listing_reports_live_released_and_stale(settings: Settings) -> None:
    client = store(settings)
    active = client.acquire("codespace", "active", "one")
    to_release = client.acquire("codespace", "released", "two")
    client.release("codespace", "released", to_release.oid)
    values = client.list(kind="codespace")
    assert [value.record.resource["key"] for value in values] == ["active", "released"]
    assert values[0].oid == active.oid
    assert values[0].live is True
    assert values[1].record.state == "released"
    assert values[1].live is False


def test_caller_checkout_environment_is_not_used(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unrelated = tmp_path / "unrelated"
    git("init", str(unrelated))
    monkeypatch.chdir(unrelated)
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    lease = store(settings).acquire("machine", "isolated", "holder")
    assert lease.record.resource["key"] == "isolated"
    assert not (unrelated / ".git" / "refs" / "heads" / "copilot-leases").exists()


def test_applied_push_with_lost_status_is_reported_as_success(
    settings: Settings,
) -> None:
    client = store(settings)
    original = client._git

    def unreliable_git(args, **kwargs):
        result = original(args, **kwargs)
        if "push" in args and result.returncode == 0:
            return subprocess.CompletedProcess(
                result.args,
                1,
                result.stdout,
                "simulated lost response",
            )
        return result

    client._git = unreliable_git  # type: ignore[method-assign]
    acquired = client.acquire("machine", "ambiguous-push", "holder")
    assert client.inspect("machine", "ambiguous-push").oid == acquired.oid
