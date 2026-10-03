from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_protocol import (
    LeaseRecord,
    ProtocolError,
    format_timestamp,
    ref_for,
    resource,
    serialize_record,
)
from agent_worktrees.lease_store import GitLeaseStore, LeaseConflict, LeaseLost


def git(
    *args: str,
    cwd: Path | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "lease-test",
            "GIT_AUTHOR_EMAIL": "lease-test@example.invalid",
            "GIT_COMMITTER_NAME": "lease-test",
            "GIT_COMMITTER_EMAIL": "lease-test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "coordination.git"
    git("init", "--bare", str(path))
    return path


@pytest.fixture
def settings(remote: Path) -> LeaseSettings:
    return LeaseSettings(
        origin=str(remote),
        default_ttl_seconds=60,
        max_ttl_seconds=3600,
        clock_skew_seconds=10,
        acquire_retries=2,
    )


BASE = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)


def store(settings: LeaseSettings, now: datetime = BASE) -> GitLeaseStore:
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
    remote: Path, settings: LeaseSettings
) -> None:
    lease = store(settings).acquire("codespace", "example", "host/session")
    assert lease.oid == remote_oid(remote, lease.ref)
    assert lease.record.lease_id
    assert lease.record.event == "acquire"
    assert commit_parents(remote, lease.oid) == [lease.oid]


def test_two_clients_racing_from_absence_produce_one_winner(
    settings: LeaseSettings,
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
    remote: Path, settings: LeaseSettings
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
    settings: LeaseSettings,
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


def test_renewal_fails_after_safe_local_deadline(settings: LeaseSettings) -> None:
    acquired = store(settings).acquire(
        "machine", "deadline", "holder", ttl_seconds=60
    )
    with pytest.raises(LeaseLost, match="safe local deadline"):
        store(settings, BASE + timedelta(seconds=51)).renew(
            "machine", "deadline", acquired.oid
        )


def test_malformed_payload_fails_closed(remote: Path, settings: LeaseSettings) -> None:
    item = resource("machine", "malformed")
    ref = ref_for(settings.ref_prefix, item)
    push_raw_message(remote, ref, "not-an-agent-leases-envelope")
    with pytest.raises(ProtocolError, match="invalid envelope"):
        store(settings).inspect(item.kind, item.key)
    with pytest.raises(ProtocolError, match="invalid envelope"):
        store(settings).acquire(item.kind, item.key, "holder")


def test_malformed_commit_topology_fails_closed(
    remote: Path, settings: LeaseSettings
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
    settings: LeaseSettings,
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
    remote: Path, settings: LeaseSettings
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


def test_listing_reports_live_released_and_stale(settings: LeaseSettings) -> None:
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
    settings: LeaseSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unrelated = tmp_path / "unrelated"
    git("init", str(unrelated))
    poisoned = tmp_path / "poisoned"
    poisoned.mkdir()
    (poisoned / ".git").write_text("gitdir: /missing/repository\n", encoding="utf-8")
    temp_parent = tmp_path / "temp"
    temp_parent.mkdir()
    (temp_parent / ".git").write_text(
        "gitdir: /missing/temp-repository\n", encoding="utf-8"
    )
    monkeypatch.setattr(tempfile, "tempdir", str(temp_parent))
    monkeypatch.chdir(poisoned)
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.bareRepository")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "explicit")
    lease = store(settings).acquire("machine", "isolated", "holder")
    assert lease.record.resource["key"] == "isolated"
    assert not (unrelated / ".git" / "refs" / "agent-worktrees").exists()


def test_applied_push_with_lost_status_is_reported_as_success(
    settings: LeaseSettings,
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


def test_squash_stale_collapses_an_old_released_lease(
    remote: Path, settings: LeaseSettings,
) -> None:
    """worktree-claims-transitive-finalization Phase 5: a `released` ref
    well past the retention window collapses to a minimal two-commit
    history (a synthetic acquire root + the unmodified terminal release)
    -- never deleted, and never a single parentless commit either (the
    protocol's own self-validation requires the history's root to be an
    acquire event)."""
    client = store(settings)
    acquired = client.acquire("machine", "stale-history", "holder")
    renewed = client.renew("machine", "stale-history", acquired.oid)
    released = client.release("machine", "stale-history", renewed.oid)
    assert commit_parents(remote, released.oid) == [released.oid, renewed.oid]

    later = store(settings, now=BASE + timedelta(days=31))
    report = later.squash_stale(retention_days=30)

    assert report == [
        {"ref": released.ref, "old_oid": released.oid,
         "new_oid": remote_oid(remote, released.ref)}
    ]
    new_oid = remote_oid(remote, released.ref)
    assert new_oid != released.oid
    parents = commit_parents(remote, new_oid)
    assert len(parents) == 2
    assert parents[0] == new_oid
    squashed = later.inspect("machine", "stale-history")
    assert squashed.record.state == "released"
    assert squashed.record.holder == "holder"
    assert squashed.record.lease_id == released.record.lease_id
    assert squashed.record.issued_at == released.record.issued_at
    assert squashed.record.renewed_at == released.record.renewed_at
    assert squashed.live is False


def test_squash_stale_skips_a_recently_released_lease(
    remote: Path, settings: LeaseSettings,
) -> None:
    client = store(settings)
    released = client.release(
        "machine", "fresh", client.acquire("machine", "fresh", "holder").oid
    )

    same_day = store(settings, now=BASE + timedelta(hours=1))
    assert same_day.squash_stale(retention_days=30) == []
    assert remote_oid(remote, released.ref) == released.oid


def test_squash_stale_never_touches_a_live_lease_regardless_of_age(
    remote: Path, settings: LeaseSettings,
) -> None:
    client = store(settings)
    acquired = client.acquire("machine", "still-leased", "holder")

    much_later = store(settings, now=BASE + timedelta(days=365))
    assert much_later.squash_stale(retention_days=30) == []
    assert remote_oid(remote, acquired.ref) == acquired.oid


def test_squash_stale_is_idempotent_on_an_already_squashed_ref(
    settings: LeaseSettings,
) -> None:
    client = store(settings)
    acquired = client.acquire("machine", "twice", "holder")
    renewed = client.renew("machine", "twice", acquired.oid)
    client.release("machine", "twice", renewed.oid)
    later = store(settings, now=BASE + timedelta(days=60))
    first = later.squash_stale(retention_days=30)
    assert len(first) == 1
    second = later.squash_stale(retention_days=30)
    assert second == []


def test_squash_stale_filters_by_kind(
    remote: Path, settings: LeaseSettings,
) -> None:
    client = store(settings)
    m_acquired = client.acquire("machine", "m-ref", "holder")
    m_renewed = client.renew("machine", "m-ref", m_acquired.oid)
    m = client.release("machine", "m-ref", m_renewed.oid)
    c_acquired = client.acquire("codespace", "c-ref", "holder")
    c_renewed = client.renew("codespace", "c-ref", c_acquired.oid)
    c = client.release("codespace", "c-ref", c_renewed.oid)
    later = store(settings, now=BASE + timedelta(days=60))
    report = later.squash_stale(retention_days=30, kind="codespace")
    assert [entry["ref"] for entry in report] == [c.ref]
    assert remote_oid(remote, m.ref) == m.oid


def test_squash_stale_dry_run_reports_without_pushing(
    remote: Path, settings: LeaseSettings,
) -> None:
    client = store(settings)
    acquired = client.acquire("machine", "preview", "holder")
    renewed = client.renew("machine", "preview", acquired.oid)
    released = client.release("machine", "preview", renewed.oid)
    later = store(settings, now=BASE + timedelta(days=60))

    report = later.squash_stale(retention_days=30, dry_run=True)

    assert report == [{"ref": released.ref, "old_oid": released.oid, "new_oid": ""}]
    assert remote_oid(remote, released.ref) == released.oid


def test_squash_stale_skips_a_ref_reacquired_mid_sweep(
    remote: Path, settings: LeaseSettings,
) -> None:
    """A concurrent `acquire()` racing in between `squash_stale`'s own
    `list()` read and its push (another client re-acquiring the resource
    right after it was released) must never be clobbered -- the push's own
    compare-and-swap (the same `--force-with-lease` discipline every other
    lease mutation uses) rejects it, and `squash_stale` skips that ref
    gracefully rather than raising or corrupting the live lease."""
    client = store(settings)
    acquired = client.acquire("machine", "raced", "holder")
    renewed = client.renew("machine", "raced", acquired.oid)
    released = client.release("machine", "raced", renewed.oid)

    later = store(settings, now=BASE + timedelta(days=60))
    racer = store(settings, now=BASE + timedelta(days=60))
    real_git = later._git
    reacquired = {}

    def _race_before_push(args, **kwargs):
        if "push" in args and "--force-with-lease" in " ".join(args):
            reacquired["snapshot"] = racer.acquire("machine", "raced", "new-holder")
        return real_git(args, **kwargs)

    later._git = _race_before_push  # type: ignore[method-assign]

    report = later.squash_stale(retention_days=30)

    assert report == []
    assert "snapshot" in reacquired
    live = later.inspect("machine", "raced")
    assert live.record.state == "leased"
    assert live.record.holder == "new-holder"
    assert live.oid == reacquired["snapshot"].oid
    assert remote_oid(remote, released.ref) == reacquired["snapshot"].oid
