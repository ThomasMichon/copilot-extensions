from __future__ import annotations

import os
import copy
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_protocol import (
    LeaseRecord,
    ProtocolError,
    ReturnedGeneration,
    format_timestamp,
    ref_for,
    resource,
    serialize_record,
)
from agent_worktrees.lease_store import GitLeaseStore, LeaseConflict, LeaseLost
from conftest import _published_seed_environment


@pytest.mark.parametrize("released", [False, True])
def test_released_only_acquisition_never_takes_over_expired_hold(monkeypatch, released):
    from dataclasses import replace

    client = store(LeaseSettings(origin="https://example.invalid/state.git"))
    monkeypatch.setattr(client, "inspect", lambda *args: None)
    transitions = []

    def transition(item, expected, record):
        transitions.append((expected, record.event))
        return "a" * 40

    monkeypatch.setattr(client, "_transition", transition)
    previous = client.acquire("codespace", "reusable", "first", ttl_seconds=60)
    if released:
        previous = replace(
            previous, live=False,
            record=replace(previous.record, state="released", event="release"),
        )
    else:
        previous = replace(previous, live=False)
    monkeypatch.setattr(client, "inspect", lambda *args: previous)
    transitions.clear()

    if released:
        acquired = client.acquire("codespace", "reusable", "next", released_only=True)
        assert acquired.record.holder == "next"
        assert transitions == [(previous.oid, "acquire")]
    else:
        with pytest.raises(LeaseConflict) as error:
            client.acquire("codespace", "reusable", "next", released_only=True)
        assert error.value.snapshot is previous
        assert transitions == []
        acquired = client.acquire("codespace", "reusable", "next")
        assert acquired.record.event == "takeover"


def test_released_only_rechecks_holder_after_cas_race(monkeypatch):
    from dataclasses import replace
    from agent_worktrees.lease_store import _CASConflict

    client = store(LeaseSettings(origin="https://example.invalid/state.git"))
    monkeypatch.setattr(client, "inspect", lambda *args: None)
    monkeypatch.setattr(client, "_transition", lambda *args: "a" * 40)
    previous = client.acquire("codespace", "reusable", "racer")
    expired = replace(previous, live=False)
    observations = iter([None, expired])
    monkeypatch.setattr(client, "inspect", lambda *args: next(observations))
    transitions = []

    def lose_race(*args):
        transitions.append(args)
        raise _CASConflict()

    monkeypatch.setattr(client, "_transition", lose_race)
    with pytest.raises(LeaseConflict) as error:
        client.acquire("codespace", "reusable", "next", released_only=True, retries=1)
    assert error.value.snapshot is expired
    assert len(transitions) == 1


@pytest.mark.parametrize("phase", ["original-return", "successor", "final-release", "compaction"])
def test_return_proof_survives_successor_and_compaction_gc(return_proof_history, phase):
    remote, settings, history = return_proof_history
    start = {
        "original-return": "first", "successor": "returned",
        "final-release": "renewed-transfer", "compaction": "final",
    }[phase]
    ref = ref_for(settings.ref_prefix, resource("codespace", "returned"))
    git(f"--git-dir={remote}", "update-ref", ref, history[start][0])
    moment = BASE + timedelta(seconds={
        "original-return": 1, "successor": 3, "final-release": 6, "compaction": 7,
    }[phase])
    client = store(settings)
    client._now = lambda: moment
    first_oid, first = history["first"]
    returned_oid, returned = history["returned"]
    original_proof = [{
        "lease_id": first.lease_id, "holder": first.holder,
        "released_at": returned.renewed_at,
    }]
    final_oid, final = history["final"]
    proofs = original_proof + [{
        "lease_id": final.lease_id, "holder": final.holder,
        "released_at": final.renewed_at,
    }]
    if phase == "original-return":
        renewed = client.renew("codespace", "returned", first_oid)
        assert renewed.record.lease_id == first.lease_id
        assert renewed.record.returned_generations == ()
        moment += timedelta(seconds=1)
        actual = client.release("codespace", "returned", renewed.oid, retain_return_proof=True)
        assert actual.to_dict()["returned_generations"] == original_proof
    elif phase == "successor":
        successor = client.acquire("codespace", "returned", "next/project/work", released_only=True)
        assert successor.to_dict()["returned_generations"] == original_proof
        moment += timedelta(seconds=1)
        transferred = client.transfer("codespace", "returned", successor.oid, "third/project/work")
        assert transferred.to_dict()["returned_generations"] == original_proof
        moment += timedelta(seconds=1)
        renewed = client.renew("codespace", "returned", transferred.oid)
        assert renewed.record.lease_id == transferred.record.lease_id
        assert renewed.to_dict()["returned_generations"] == original_proof
        assert successor.record.lease_id not in {
            item["lease_id"] for item in renewed.to_dict()["returned_generations"]
        }
    elif phase == "final-release":
        actual = client.release("codespace", "returned", history["renewed-transfer"][0])
        assert actual.to_dict()["returned_generations"] == proofs
        assert history["successor"][1].lease_id not in {
            item["lease_id"] for item in actual.to_dict()["returned_generations"]
        }
        with pytest.raises(LeaseLost):
            client.release("codespace", "returned", history["renewed"][0], retain_return_proof=True)
        assert client.inspect("codespace", "returned").oid == actual.oid
    else:
        assert client.inspect("codespace", "returned").oid == final_oid
        moment += timedelta(days=31)
        assert len(client.squash_stale(retention_days=30, kind="codespace")) == 1
        git(f"--git-dir={remote}", "reflog", "expire", "--expire=now", "--all")
        git(f"--git-dir={remote}", "gc", "--prune=now")
        assert git(f"--git-dir={remote}", "cat-file", "-e", returned_oid, check=False).returncode != 0
        compacted = client.inspect("codespace", "returned")
        assert compacted.to_dict()["returned_generations"] == proofs
        assert compacted.record.lease_id == final.lease_id


def test_return_proof_evicts_only_the_oldest_generation(monkeypatch):
    from dataclasses import replace
    from agent_worktrees.lease_protocol import ReturnedGeneration, parse_record

    client = store(LeaseSettings(origin="https://example.invalid/state.git"))
    monkeypatch.setattr(client, "inspect", lambda *args: None)
    monkeypatch.setattr(client, "_transition", lambda *args: "a" * 40)
    held = client.acquire("codespace", "bounded", "current/project/work")
    earlier = tuple(ReturnedGeneration(
        f"{number:032x}", "earlier/project/work", format_timestamp(BASE - timedelta(seconds=1)),
    ) for number in range(16))
    held = replace(held, record=replace(held.record, schema_version=2, returned_generations=earlier))
    monkeypatch.setattr(client, "inspect", lambda *args: held)
    returned = client.release("codespace", "bounded", held.oid)
    assert returned.record.returned_generations[:-1] == earlier[1:]
    assert returned.record.returned_generations[-1].lease_id == held.record.lease_id
    assert parse_record(serialize_record(returned.record)) == returned.record


@pytest.mark.parametrize("corruption", ["drop", "downgrade"])
def test_return_proof_cannot_be_removed_by_later_writes(settings, corruption):
    from dataclasses import replace

    client = store(settings)
    first = client.acquire("codespace", "preserved", "first/project/work")
    client.release("codespace", "preserved", first.oid, retain_return_proof=True)
    successor = client.acquire("codespace", "preserved", "next/project/work")
    corrupt = replace(
        successor.record, event="renew", returned_generations=(),
        schema_version=1 if corruption == "downgrade" else 2,
    )
    client._transition(resource("codespace", "preserved"), successor.oid, corrupt)
    with pytest.raises(ProtocolError, match="retained return evidence"):
        client.inspect("codespace", "preserved")


def test_lost_release_cas_cannot_publish_return_proof(settings, monkeypatch):
    client = store(settings)
    first = client.acquire("codespace", "racing-return", "first/project/work")
    transition = client._transition
    rival = store(settings)
    renewed = None

    def race(item, expected, record):
        nonlocal renewed
        renewed = rival.renew("codespace", "racing-return", first.oid)
        return transition(item, expected, record)

    monkeypatch.setattr(client, "_transition", race)
    with pytest.raises(LeaseLost, match="compare-and-swap"):
        client.release("codespace", "racing-return", first.oid, retain_return_proof=True)
    seen = rival.inspect("codespace", "racing-return")
    assert seen.oid == renewed.oid
    assert seen.record.state == "leased"
    assert seen.record.returned_generations == ()
    assert seen.record.schema_version == 1


def test_return_proof_survives_unknown_release_after_successor_wins(settings, monkeypatch):
    client = store(settings)
    first = client.acquire("codespace", "unknown-return", "first/project/work")
    original_git = client._git
    rival = store(settings)
    successor = None

    def publish_then_reacquire(args, **kwargs):
        nonlocal successor
        result = original_git(args, **kwargs)
        if "push" in args and result.returncode == 0:
            successor = rival.acquire(
                "codespace", "unknown-return", "next/project/work", released_only=True,
            )
        return result

    monkeypatch.setattr(client, "_git", publish_then_reacquire)
    with pytest.raises(LeaseLost, match="compare-and-swap"):
        client.release("codespace", "unknown-return", first.oid, retain_return_proof=True)
    seen = rival.inspect("codespace", "unknown-return")
    assert seen.oid == successor.oid
    assert seen.record.state == "leased"
    assert seen.record.holder == "next/project/work"
    assert [item.lease_id for item in seen.record.returned_generations] == [first.record.lease_id]


def test_stale_takeover_retains_only_actual_return_proof(monkeypatch):
    from dataclasses import replace
    from agent_worktrees.lease_protocol import ReturnedGeneration

    client = store(LeaseSettings(origin="https://example.invalid/state.git"))
    monkeypatch.setattr(client, "inspect", lambda *args: None)
    monkeypatch.setattr(client, "_transition", lambda *args: "a" * 40)
    held = client.acquire("codespace", "stale-proof", "first/project/work")
    earlier = (ReturnedGeneration(
        "b" * 32, "earlier/project/work", format_timestamp(BASE - timedelta(seconds=1)),
    ),)
    held = replace(
        held, live=False, record=replace(held.record, schema_version=2, returned_generations=earlier),
    )
    monkeypatch.setattr(client, "inspect", lambda *args: held)
    taken = client.acquire("codespace", "stale-proof", "next/project/work")
    assert taken.record.event == "takeover"
    assert taken.record.returned_generations == earlier
    assert held.record.lease_id not in {item.lease_id for item in taken.record.returned_generations}


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


def _settings(remote: Path) -> LeaseSettings:
    return LeaseSettings(
        origin=str(remote),
        default_ttl_seconds=60,
        max_ttl_seconds=3600,
        clock_skew_seconds=10,
        acquire_retries=2,
    )


@pytest.fixture
def settings(remote: Path) -> LeaseSettings:
    return _settings(remote)


BASE = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)


def store(settings: LeaseSettings, now: datetime = BASE) -> GitLeaseStore:
    return GitLeaseStore(
        settings,
        now=lambda: now,
        sleep=lambda _seconds: None,
        jitter=lambda _low, _high: 0,
    )


@pytest.fixture(scope="session")
def _return_proof_history_seed(tmp_path_factory):
    """Seed canonical prefixes; each case exercises its own real Git effects."""
    remote = tmp_path_factory.mktemp("return-proof-seed") / "coordination.git"
    first = LeaseRecord(
        schema_version=1, resource=asdict(resource("codespace", "returned")),
        state="leased", event="acquire", lease_id="1" * 32, holder="first/project/work",
        issued_at=format_timestamp(BASE), renewed_at=format_timestamp(BASE),
        expires_at=format_timestamp(BASE + timedelta(seconds=60)), ttl_seconds=60, context={},
    )
    renewed = replace(
        first, event="renew", renewed_at=format_timestamp(BASE + timedelta(seconds=1)),
        expires_at=format_timestamp(BASE + timedelta(seconds=61)),
    )
    released_at = format_timestamp(BASE + timedelta(seconds=2))
    returned = replace(
        renewed, schema_version=2, state="released", event="release", renewed_at=released_at,
        expires_at=released_at, ttl_seconds=0,
        returned_generations=(ReturnedGeneration(first.lease_id, first.holder, released_at),),
    )
    successor = replace(
        returned, state="leased", event="acquire", lease_id="2" * 32, holder="next/project/work",
        ttl_seconds=60,
        issued_at=format_timestamp(BASE + timedelta(seconds=3)),
        renewed_at=format_timestamp(BASE + timedelta(seconds=3)),
        expires_at=format_timestamp(BASE + timedelta(seconds=63)),
    )
    transferred = replace(
        successor, event="transfer", lease_id="3" * 32, holder="third/project/work",
        issued_at=format_timestamp(BASE + timedelta(seconds=4)),
        renewed_at=format_timestamp(BASE + timedelta(seconds=4)),
        expires_at=format_timestamp(BASE + timedelta(seconds=64)),
    )
    renewed_transfer = replace(
        transferred, event="renew", renewed_at=format_timestamp(BASE + timedelta(seconds=5)),
        expires_at=format_timestamp(BASE + timedelta(seconds=65)),
    )
    final_at = format_timestamp(BASE + timedelta(seconds=6))
    final = replace(
        renewed_transfer, state="released", event="release", renewed_at=final_at, expires_at=final_at,
        ttl_seconds=0,
        returned_generations=returned.returned_generations + (
            ReturnedGeneration(transferred.lease_id, transferred.holder, final_at),
        ),
    )
    records = [
        ("first", first), ("renewed", renewed), ("returned", returned), ("successor", successor),
        ("transferred", transferred), ("renewed-transfer", renewed_transfer), ("final", final),
    ]
    history = {}
    with _published_seed_environment(tmp_path_factory):
        git("init", "--template=", "--bare", str(remote))
        tree = git(f"--git-dir={remote}", "mktree", input_text="").stdout.strip()
        parent = None
        for name, record in records:
            parents = ["-p", parent] if parent else []
            parent = git(
                f"--git-dir={remote}", "commit-tree", tree, *parents,
                input_text=serialize_record(record) + "\n",
            ).stdout.strip()
            history[name] = (parent, record)
        ref = ref_for(_settings(remote).ref_prefix, resource("codespace", "returned"))
        git(f"--git-dir={remote}", "update-ref", ref, parent)
    return remote, history


@pytest.fixture
def return_proof_history(tmp_path, _return_proof_history_seed):
    seed, history = _return_proof_history_seed
    remote = tmp_path / "coordination.git"
    shutil.copytree(seed, remote, copy_function=shutil.copyfile)
    return remote, _settings(remote), copy.deepcopy(history)


@pytest.fixture(scope="session")
def _released_history_seed(tmp_path_factory):
    """Prepare one real released history per worker, never shared for mutation."""
    remote = tmp_path_factory.mktemp("released-lease-seed") / "coordination.git"
    with _published_seed_environment(tmp_path_factory):
        git("init", "--template=", "--bare", str(remote))
        client = store(_settings(remote))
        acquired = client.acquire("machine", "stale-history", "holder")
        renewed = client.renew("machine", "stale-history", acquired.oid)
        released = client.release("machine", "stale-history", renewed.oid)
    return remote, acquired, renewed, released


def _copy_released_history(source, root: Path):
    seed, acquired, renewed, released = source
    remote = root / "coordination.git"
    shutil.copytree(seed, remote, copy_function=shutil.copyfile)
    return remote, _settings(remote), *copy.deepcopy((acquired, renewed, released))


@pytest.fixture
def released_history(tmp_path: Path, _released_history_seed):
    return _copy_released_history(_released_history_seed, tmp_path)


def _remote_snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("iteration", range(2))
def test_released_history_copies_isolate_remote_and_snapshots(tmp_path, _released_history_seed, iteration):
    seed, *seed_records = _released_history_seed
    before = _remote_snapshot(seed)
    records_before = copy.deepcopy(seed_records)
    first = _copy_released_history(_released_history_seed, tmp_path / "first")
    second = _copy_released_history(_released_history_seed, tmp_path / "second")
    remote, settings, acquired, renewed, released = first
    second_before = _remote_snapshot(second[0])
    second_records_before = copy.deepcopy(second[2:])
    assert settings.origin == str(remote)
    assert second[1].origin == str(second[0]) != settings.origin
    assert _remote_snapshot(remote) == before == second_before
    assert remote_oid(remote, released.ref) == released.oid
    assert commit_parents(remote, released.oid) == [released.oid, renewed.oid]
    assert commit_parents(remote, renewed.oid) == [renewed.oid, acquired.oid]
    assert commit_parents(remote, acquired.oid) == [acquired.oid]
    acquired.record.context["copy-only"] = iteration
    released.record.resource["copy-only"] = str(iteration)
    client = store(settings, now=BASE + timedelta(days=60))
    report = client.squash_stale(retention_days=30)
    assert len(report) == 1 and report[0]["old_oid"] == released.oid
    assert remote_oid(remote, released.ref) == report[0]["new_oid"] != released.oid
    reacquired = client.acquire("machine", "stale-history", f"holder-{iteration}")
    assert remote_oid(remote, released.ref) == reacquired.oid
    assert reacquired.record.state == "leased"
    assert _remote_snapshot(seed) == before
    assert _remote_snapshot(second[0]) == second_before
    assert list(_released_history_seed[1:]) == records_before
    assert second[2:] == second_records_before
    for relative in before:
        if "objects" in relative.parts:
            assert relative.name != "alternates"
            assert not (remote / relative).samefile(seed / relative)
            assert not (remote / relative).samefile(second[0] / relative)


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
    released_history, _released_history_seed,
) -> None:
    """worktree-claims-transitive-finalization Phase 5: a `released` ref
    well past the retention window collapses to a minimal two-commit
    history (a synthetic acquire root + the unmodified terminal release)
    -- never deleted, and never a single parentless commit either (the
    protocol's own self-validation requires the history's root to be an
    acquire event)."""
    remote, settings, _acquired, renewed, released = released_history
    seed_ref = remote_oid(_released_history_seed[0], released.ref)
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
    assert remote_oid(_released_history_seed[0], released.ref) == seed_ref


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
    released_history,
) -> None:
    remote, settings, _acquired, _renewed, m = released_history
    client = store(settings)
    c_acquired = client.acquire("codespace", "c-ref", "holder")
    c_renewed = client.renew("codespace", "c-ref", c_acquired.oid)
    c = client.release("codespace", "c-ref", c_renewed.oid)
    later = store(settings, now=BASE + timedelta(days=60))
    report = later.squash_stale(retention_days=30, kind="codespace")
    assert [entry["ref"] for entry in report] == [c.ref]
    assert remote_oid(remote, m.ref) == m.oid


def test_squash_stale_dry_run_reports_without_pushing(
    released_history,
) -> None:
    remote, settings, _acquired, _renewed, released = released_history
    later = store(settings, now=BASE + timedelta(days=60))

    report = later.squash_stale(retention_days=30, dry_run=True)

    assert report == [{"ref": released.ref, "old_oid": released.oid, "new_oid": ""}]
    assert remote_oid(remote, released.ref) == released.oid

    first = later.squash_stale(retention_days=30)
    assert len(first) == 1
    assert first[0]["ref"] == released.ref
    assert first[0]["old_oid"] == released.oid
    squashed_oid = remote_oid(remote, released.ref)
    assert first[0]["new_oid"] == squashed_oid
    assert squashed_oid != released.oid
    second = later.squash_stale(retention_days=30)
    assert second == []
    assert remote_oid(remote, released.ref) == squashed_oid


def test_squash_stale_skips_a_ref_reacquired_mid_sweep(
    released_history,
) -> None:
    """A concurrent `acquire()` racing in between `squash_stale`'s own
    `list()` read and its push (another client re-acquiring the resource
    right after it was released) must never be clobbered -- the push's own
    compare-and-swap (the same `--force-with-lease` discipline every other
    lease mutation uses) rejects it, and `squash_stale` skips that ref
    gracefully rather than raising or corrupting the live lease."""
    remote, settings, _acquired, _renewed, released = released_history

    later = store(settings, now=BASE + timedelta(days=60))
    racer = store(settings, now=BASE + timedelta(days=60))
    real_git = later._git
    reacquired = {}

    def _race_before_push(args, **kwargs):
        if "push" in args and "--force-with-lease" in " ".join(args):
            reacquired["snapshot"] = racer.acquire("machine", "stale-history", "new-holder")
        return real_git(args, **kwargs)

    later._git = _race_before_push  # type: ignore[method-assign]

    report = later.squash_stale(retention_days=30)

    assert report == []
    assert "snapshot" in reacquired
    live = later.inspect("machine", "stale-history")
    assert live.record.state == "leased"
    assert live.record.holder == "new-holder"
    assert live.oid == reacquired["snapshot"].oid
    assert remote_oid(remote, released.ref) == reacquired["snapshot"].oid
