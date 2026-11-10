"""Real Git effect responsibility, without a live provider or adoption writer."""

import json
import copy
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import timedelta

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_protocol import (
    ProtocolError, format_timestamp, ref_for, resource, serialize_record,
)
from agent_worktrees.lease_store import (
    GitLeaseStore, LeaseLost, ProviderEffectConflict, ProviderEffectSnapshot,
)
from agent_worktrees.service_custody_protocol import serialize_custody
from agent_worktrees.provider_effect_protocol import ProviderEffectRecord, serialize_effect
from conftest import _published_seed_environment
from service_custody_support import BASE, custody_record, task_record
from test_lease_store import git, remote_oid
from test_service_custody_store import append_raw

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_worktrees.provider_effect_custody"),
]

EFFECT = "c" * 32


@pytest.fixture
def task_owned(tmp_path):
    remote = tmp_path / "coordination.git"
    git("init", "--bare", str(remote))
    settings = LeaseSettings(origin=str(remote), clock_skew_seconds=10)
    store = GitLeaseStore(settings, now=lambda: BASE)
    task = store.acquire("machine", "service-host", "task-owner", ttl_seconds=60)
    return remote, settings, task


def begin(store, token, *, effect_id=EFFECT):
    return store.begin_effect(
        "machine", "service-host", token, effect_id=effect_id,
        owner_id="native-owner", provider_id="example-provider",
        provider_resource_id="immutable-instance", action="stop", request_digest="1" * 64,
    )


@pytest.fixture
def admitted(task_owned):
    remote, settings, task = task_owned
    store = GitLeaseStore(settings, now=lambda: BASE)
    claim = begin(store, task.oid)
    return remote, settings, task, claim


@pytest.mark.parametrize("operation", ["acquire", "renew", "transfer", "release"])
def test_restart_and_task_expiry_do_not_clear_native_responsibility(admitted, operation):
    remote, settings, task, claim = admitted
    restarted = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=3650))
    assert restarted.inspect_effect("machine", "service-host", EFFECT) == claim
    args = ["machine", "service-host", "new-owner"] if operation == "acquire" else [
        "machine", "service-host", claim.oid,
    ]
    if operation == "transfer":
        args.append("new-owner")
    with pytest.raises(ProviderEffectConflict):
        getattr(restarted, operation)(*args)
    assert remote_oid(remote, task.ref) == claim.oid


def test_unsettled_inventory_and_compaction_cannot_clear_effect(admitted):
    remote, settings, task, claim = admitted
    restarted = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=3650))
    assert isinstance(restarted.list_resources()[0], ProviderEffectSnapshot)
    assert restarted.squash_stale(retention_days=1) == []
    assert remote_oid(remote, task.ref) == claim.oid


def test_owner_settlement_retains_receipt_through_task_release_and_gc(admitted):
    remote, settings, task, claim = admitted
    store = GitLeaseStore(settings, now=lambda: BASE + timedelta(seconds=1))
    settled = store.settle_effect(
        "machine", "service-host", claim.oid, effect_id=EFFECT, owner_id="native-owner",
        outcome="succeeded", settlement_digest="2" * 64,
    )
    assert store.inspect_resource("machine", "service-host").record == task.record
    assert store.inspect_effect("machine", "service-host", EFFECT) == settled
    assert store.inspect_effect("machine", "service-host", "d" * 32) is None
    with pytest.raises(LeaseLost):
        store.release("machine", "service-host", task.oid)
    released = store.release("machine", "service-host", settled.oid, retain_return_proof=True)
    restarted = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=3650))
    assert restarted.squash_stale(retention_days=1) == []
    assert remote_oid(remote, task.ref) == released.oid
    assert restarted.inspect_effect("machine", "service-host", EFFECT) == settled
    assert restarted.inspect("machine", "service-host").record.returned_generations[0].lease_id == (
        task.record.lease_id
    )


@pytest.mark.parametrize(("owner", "token"), [("other-owner", "current"), ("native-owner", "task")])
def test_wrong_owner_and_stale_task_token_cannot_settle(admitted, owner, token):
    remote, settings, task, claim = admitted
    store = GitLeaseStore(settings, now=lambda: BASE)
    with pytest.raises(LeaseLost, match="does not own"):
        store.settle_effect(
            "machine", "service-host", claim.oid if token == "current" else task.oid,
            effect_id=EFFECT, owner_id=owner, outcome="not-started", settlement_digest="2" * 64,
        )
    assert remote_oid(remote, task.ref) == claim.oid


@pytest.mark.parametrize("successor", ["task", "adoption"])
def test_raw_successor_cannot_hide_unsettled_effect(admitted, successor):
    remote, settings, task, claim = admitted
    message = (
        serialize_record(replace(task.record, event="renew"))
        if successor == "task" else serialize_custody(custody_record(predecessor=claim.oid))
    )
    append_raw(remote, task.ref, claim.oid, message)
    with pytest.raises(ProtocolError, match="unsettled effect"):
        GitLeaseStore(settings).inspect_resource("machine", "service-host")


def test_lost_settle_reply_is_inspectable_without_repeating_effect(admitted, monkeypatch):
    _remote, settings, _task, claim = admitted
    store = GitLeaseStore(settings, now=lambda: BASE)
    transition = store._transition

    def lose_reply(*args):
        transition(*args)
        raise OSError("synthetic lost settlement reply")

    monkeypatch.setattr(store, "_transition", lose_reply)
    with pytest.raises(OSError, match="lost settlement"):
        store.settle_effect(
            "machine", "service-host", claim.oid, effect_id=EFFECT, owner_id="native-owner",
            outcome="not-started", settlement_digest="2" * 64,
        )
    recovered = GitLeaseStore(settings).inspect_effect("machine", "service-host", EFFECT)
    assert recovered.record.state == "effect-settled"
    assert recovered.record.task_token == claim.record.task_token


def test_lost_begin_reply_retains_original_attempt_without_replay(task_owned, monkeypatch):
    remote, settings, task = task_owned
    store = GitLeaseStore(settings, now=lambda: BASE)
    transition = store._transition

    def lose_reply(*args):
        transition(*args)
        raise OSError("synthetic lost admission reply")

    monkeypatch.setattr(store, "_transition", lose_reply)
    with pytest.raises(OSError, match="lost admission"):
        begin(store, task.oid)
    restarted = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=1))
    recovered = restarted.inspect_effect("machine", "service-host", EFFECT)
    assert recovered.record.task_token == task.oid
    assert recovered.oid == remote_oid(remote, task.ref)
    with pytest.raises(ProviderEffectConflict):
        begin(restarted, task.oid)


def test_fresh_process_inventory_keeps_unsettled_effect_visible(admitted):
    _remote, settings, _task, claim = admitted
    script = """
import json, sys
from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_store import GitLeaseStore, ProviderEffectConflict
store = GitLeaseStore(LeaseSettings(origin=sys.argv[1]))
print(json.dumps(store.inspect_resource("machine", "service-host").to_dict()))
try:
    store.acquire("machine", "service-host", "unrelated-owner")
except ProviderEffectConflict:
    sys.exit(3)
sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, settings.origin], capture_output=True, text=True,
        timeout=15, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 3, result.stderr
    value = json.loads(result.stdout)
    assert value["state"] == "effect-unsettled" and value["token"] == claim.oid
    assert not {"live", "expires_at", "safe_deadline"} & value.keys()


def wait_ready(process, marker):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if marker.exists():
            return
        if process.poll() is not None:
            out, err = process.communicate(timeout=5)
            raise AssertionError(f"effect race worker failed before its barrier: {out}\n{err}")
        time.sleep(0.02)
    raise AssertionError(f"effect race worker did not reach its barrier (exit={process.poll()})")


@pytest.mark.parametrize("winner", ["adoption", "effect"])
def test_separate_process_adoption_and_effect_cannot_both_win(task_owned, tmp_path, winner):
    remote, settings, task = task_owned
    ready, proceed = tmp_path / "ready", tmp_path / "proceed"
    script = """
import os, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from agent_procutil import no_window_kwargs
from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_store import GitLeaseStore, LeaseLost
remote, ref, token, ready, proceed, winner, stamp, adoption = sys.argv[1:]
def git(*args, input_text=None, check=True):
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME="effect-test", GIT_COMMITTER_NAME="effect-test",
        GIT_AUTHOR_EMAIL="effect@example.invalid", GIT_COMMITTER_EMAIL="effect@example.invalid")
    return subprocess.run(["git", *args], input=input_text, capture_output=True,
        text=True, timeout=5, env=env, check=check, **no_window_kwargs())
def barrier():
    Path(ready).touch()
    deadline = time.monotonic() + 15
    while not Path(proceed).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("effect race barrier timed out")
        time.sleep(0.02)
if winner == "adoption":
    store = GitLeaseStore(LeaseSettings(origin=remote, clock_skew_seconds=10),
        now=lambda: datetime.fromisoformat(stamp))
    transition = store._transition
    def paused(*args):
        barrier()
        return transition(*args)
    store._transition = paused
    try:
        store.begin_effect("machine", "service-host", token, effect_id="c"*32,
            owner_id="native-owner", provider_id="example-provider",
            provider_resource_id="immutable-instance", action="stop", request_digest="1"*64)
    except LeaseLost:
        print("fenced")
    else:
        raise RuntimeError("effect overwrote adoption")
else:
    tree = git("--git-dir", remote, "mktree", input_text="").stdout.strip()
    oid = git("--git-dir", remote, "commit-tree", tree, "-p", token,
        input_text=adoption+"\\n").stdout.strip()
    barrier()
    result = git("--git-dir", remote, "update-ref", ref, oid, token, check=False)
    if result.returncode == 0:
        raise RuntimeError("adoption overwrote effect")
    print("fenced")
"""
    worker = subprocess.Popen(
        [sys.executable, "-c", script, str(remote), task.ref, task.oid,
         str(ready), str(proceed), winner, BASE.isoformat(),
         serialize_custody(custody_record(predecessor=task.oid))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        wait_ready(worker, ready)
        if winner == "adoption":
            expected = append_raw(remote, task.ref, task.oid,
                                  serialize_custody(custody_record(predecessor=task.oid)))
        else:
            expected = begin(GitLeaseStore(settings, now=lambda: BASE), task.oid).oid
        proceed.touch()
        out, err = worker.communicate(timeout=15)
        assert worker.returncode == 0 and out.strip() == "fenced", err
        assert remote_oid(remote, task.ref) == expected
        record = GitLeaseStore(settings).inspect_resource("machine", "service-host").record
        assert record.state == ("service-owned" if winner == "adoption" else "effect-unsettled")
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)


def test_native_exit_and_expiry_do_not_implicitly_publish_settlement(admitted, tmp_path):
    _remote, settings, _task, claim = admitted
    ready, proceed = tmp_path / "native-ready", tmp_path / "native-proceed"
    script = """
import sys, time
from pathlib import Path
Path(sys.argv[1]).touch()
deadline = time.monotonic() + 15
while not Path(sys.argv[2]).exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("native effect barrier timed out")
    time.sleep(0.02)
"""
    native = subprocess.Popen(
        [sys.executable, "-c", script, str(ready), str(proceed)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        wait_ready(native, ready)
        store = GitLeaseStore(settings, now=lambda: BASE + timedelta(days=1))
        with pytest.raises(ProviderEffectConflict):
            store.acquire("machine", "service-host", "other-owner")
        proceed.touch()
        _, err = native.communicate(timeout=5)
        assert native.returncode == 0, err
        assert store.inspect_effect("machine", "service-host", EFFECT) == claim
        settled = store.settle_effect(
            "machine", "service-host", claim.oid, effect_id=EFFECT, owner_id="native-owner",
            outcome="succeeded", settlement_digest="2" * 64,
        )
        assert settled.record.state == "effect-settled"
    finally:
        if native.poll() is None:
            native.kill()
        native.communicate(timeout=5)


@pytest.fixture(scope="session")
def _cross_margin_seed(tmp_path_factory):
    """Canonical accepted prefix only; each recovery uses a private real remote."""
    remote = tmp_path_factory.mktemp("effect-policy-prefix") / "coordination.git"
    settings = LeaseSettings(origin=str(remote), clock_skew_seconds=10)
    ref = ref_for(settings.ref_prefix, resource("machine", "service-host"))
    task = task_record()
    transferred = replace(
        task, event="transfer", lease_id="d" * 32, holder="second-owner",
        issued_at=format_timestamp(BASE + timedelta(seconds=50)),
        renewed_at=format_timestamp(BASE + timedelta(seconds=50)),
        expires_at=format_timestamp(BASE + timedelta(seconds=110)),
    )
    with _published_seed_environment(tmp_path_factory):
        git("init", "--template=", "--bare", str(remote))
        root = append_raw(remote, ref, "", serialize_record(task))
        parent = append_raw(remote, ref, root, serialize_record(transferred))
        record = ProviderEffectRecord(
            schema_version=1, resource=task.resource, state="effect-unsettled", event="begin",
            effect_id=EFFECT, owner_id="native-owner", provider_id="example-provider",
            provider_resource_id="immutable-instance", action="stop", request_digest="1" * 64,
            task_token=parent, predecessor=parent,
            started_at=format_timestamp(BASE + timedelta(seconds=60)), admission_margin_seconds=10,
        )
        oid = append_raw(remote, ref, parent, serialize_effect(record))
    return remote, transferred, ProviderEffectSnapshot(ref, oid, record)


@pytest.fixture
def cross_margin_history(tmp_path, _cross_margin_seed):
    seed, transferred, claim = _cross_margin_seed
    remote = tmp_path / "coordination.git"
    shutil.copytree(seed, remote, copy_function=shutil.copyfile)
    return remote, LeaseSettings(origin=str(remote), clock_skew_seconds=10), *copy.deepcopy(
        (transferred, claim),
    )


@pytest.mark.parametrize("reader_margin", [0, 10, 30, 120])
def test_cross_margin_restart_can_inspect_and_settle_original_effect(cross_margin_history, reader_margin):
    remote, settings, transferred, claim = cross_margin_history
    restarted = GitLeaseStore(
        replace(settings, clock_skew_seconds=reader_margin),
        now=lambda: BASE + timedelta(seconds=80),
    )
    assert restarted.inspect_effect("machine", "service-host", EFFECT) == claim
    settled = restarted.settle_effect(
        "machine", "service-host", claim.oid, effect_id=EFFECT, owner_id="native-owner",
        outcome="not-started", settlement_digest="2" * 64,
    )
    assert restarted.inspect_effect("machine", "service-host", EFFECT) == settled
    assert restarted.inspect("machine", "service-host").record == transferred
    if reader_margin == 120:
        with pytest.raises(LeaseLost, match="safe local deadline"):
            restarted.renew("machine", "service-host", settled.oid)
    assert remote_oid(remote, claim.ref) == settled.oid
