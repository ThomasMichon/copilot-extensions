"""Sequential reuse preserves prior work and requires positive release evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_codespaces import allocation, coordination, lease
from agent_codespaces import __main__ as cli
from agent_codespaces.config import CodespacesConfig
from agent_codespaces.lifecycle import CodespaceInfo


@pytest.fixture
def venue(monkeypatch, tmp_path):
    from agent_codespaces import account_binding, pool, status, worktrees
    from ssh_manager import locks

    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setitem(sys.modules, "agent_worktrees", None)
    monkeypatch.setattr(locks, "locks_dir", lambda: tmp_path / "target-locks")
    monkeypatch.setattr(lease, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(lease, "_DEPLOY_HOLDS_FILE", tmp_path / "deploy-holds.json")
    monkeypatch.setattr(lease, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(lease, "_this_host", lambda: "test-host")
    monkeypatch.setattr(lease, "resolve_owner_worktree", lambda **kwargs: "new-worktree")
    monkeypatch.setattr(account_binding, "BINDINGS_FILE", tmp_path / "bindings.json")
    monkeypatch.setattr(account_binding, "_LOCK_FILE", tmp_path / "bindings.lock")
    monkeypatch.setattr(account_binding, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(coordination, "owner_ref", lambda **kwargs: "machine/project/new-worktree")
    readiness = coordination.PreflightResult(
        "ready", capabilities=frozenset({coordination.RELEASED_ONLY_CAPABILITY}),
    )
    monkeypatch.setattr(coordination, "preflight", lambda holder: readiness)
    monkeypatch.setattr(pool, "list_status", lambda: [])
    monkeypatch.setattr(pool, "codespaces_in_use", lambda names: {})
    monkeypatch.setattr("agent_codespaces.live_users.codespaces_in_use", lambda names: {})
    monkeypatch.setattr(cli, "_require_codespace_scope", lambda *args, **kwargs: pytest.fail("creation scope"))
    monkeypatch.setattr(cli, "create_codespace", lambda *args, **kwargs: pytest.fail("new provider venue"))
    monkeypatch.setattr(allocation, "allocation_context", lambda repo: ("selected", {"GH_TOKEN": "fixture"}))
    config = CodespacesConfig(default_machine_type="premiumLinux")
    monkeypatch.setattr(cli, "load_merged_config", lambda: config)
    rows = [CodespaceInfo(
        name="returned", display_name="returned", repository="example/project",
        branch="retained-feature", state="Shutdown", machine="premiumLinux", account="selected",
    )] + [CodespaceInfo(
        name=f"other-{index}", display_name=f"other-{index}", repository="example/other",
        branch="main", state="Shutdown", machine="premiumLinux", account="selected",
    ) for index in range(29)]
    monkeypatch.setattr(allocation, "list_account_codespaces", lambda *args, **kwargs: rows)
    shared = {
        "returned": coordination.L2Lease(
            "returned", "machine/project/previous", False, token="a" * 40, state="released",
        ),
    }
    monkeypatch.setattr(coordination, "list_leases", lambda **kwargs: shared)
    monkeypatch.setattr(coordination, "inspect", lambda name: {
        "state": shared[name].state, "live": shared[name].live,
        "holder": shared[name].holder, "token": shared[name].token,
    })
    events = []
    ledger = {"new-worktree": set()}

    def ownership(args, **kwargs):
        if args[:2] == ["claims", "owner"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({
                "kind": "codespace", "ref": args[3], "owners": [],
            }), "")
        assert args[:3] == ["--project", "project", "claims"] and args[-1] == "--json"
        worktree = args[3]
        outbound = [
            {"kind": "codespace", "ref": name, "state": "active"}
            for name in sorted(ledger.get(worktree, ()))
        ]
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "version": 1, "machine": "machine", "repo": "project",
            "worktree_id": worktree, "outbound": outbound,
        }), "")

    monkeypatch.setattr(coordination, "_run", ownership)

    def journal(name, holder, **kwargs):
        events.append(("journal", name, holder))
        ledger.setdefault(holder.rsplit("/", 1)[-1], set()).add(name)
        return True

    def settle(name, holder, **kwargs):
        events.append(("settle", name, holder))
        ledger[holder.rsplit("/", 1)[-1]].discard(name)
        return True

    monkeypatch.setattr(coordination, "journal_obligation", journal)
    monkeypatch.setattr(coordination, "settle_obligation", settle)

    def acquire(name, holder, **kwargs):
        assert kwargs == {"released_only": True, "preflight_result": readiness}
        assert shared[name].state == "released"
        events.append(("acquire", name, holder))
        shared[name] = coordination.L2Lease(name, holder, True, token="b" * 40, state="leased")
        return coordination.L2Result("ok", token="b" * 40)

    def release(name, token):
        assert token == shared[name].token
        shared[name] = coordination.L2Lease(
            name, shared[name].holder, False, token="c" * 40, state="released",
        )
        events.append(("release", name))
        return coordination.L2Result("ok", token="c" * 40)

    monkeypatch.setattr(coordination, "acquire", acquire)
    monkeypatch.setattr(coordination, "renew", lambda name, token: coordination.L2Result("ok", token=token))
    monkeypatch.setattr(coordination, "release", release)
    monkeypatch.setattr(status, "clear_status", lambda name: events.append(("clear-marker", name)))
    retained = tmp_path / "retained-work.txt"
    retained.write_bytes(b"uncommitted prior effort work\n")
    return SimpleNamespace(
        rows=rows, shared=shared, events=events, config=config, retained=retained, root=tmp_path,
        ledger=ledger,
    )


def test_allocate_create_connect_and_return_preserve_retained_work(venue, capsys):
    from agent_codespaces.account_binding import bound_account, bound_repo

    assert cli.main(["allocate", "example/project", "--json"]) == 0
    advice = json.loads(capsys.readouterr().out)
    assert advice["action"] == "reuse" and advice["codespace"] == "returned"
    assert venue.events == []
    assert not lease.LEASE_FILE.exists()
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert capsys.readouterr().out.strip().endswith("returned")
    held = lease.get_lease("returned")
    assert held.worktree == "new-worktree" and held.lease_token == "b" * 40
    assert venue.events[:2] == [
        ("journal", "returned", "machine/project/new-worktree"),
        ("acquire", "returned", "machine/project/new-worktree"),
    ]
    assert bound_account("returned") == "selected"
    assert bound_repo("returned") == "example/project"
    prior = list(venue.events)
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert venue.events == prior
    capsys.readouterr()
    # The existing Bridge-facing claim is re-entrant; it does not take a new venue.
    connected = lease.claim(
        "returned", "new-worktree", holder_ref="machine/project/new-worktree",
    )
    assert connected.lease_token == held.lease_token
    assert lease.release_claim("returned", "new-worktree")
    assert venue.shared["returned"].state == "released"
    assert lease.get_lease("returned") is None
    assert cli.main(["allocate", "example/project", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "reuse"
    assert venue.rows[0].branch == "retained-feature"
    assert venue.retained.read_bytes() == b"uncommitted prior effort work\n"


@pytest.mark.parametrize("blocker", ["expired-shared", "unknown-shared", "local", "corrupt-local", "users", "headroom"])
def test_unknown_or_incompatible_ownership_never_creates_or_overwrites(venue, monkeypatch, capsys, blocker):
    if blocker == "expired-shared":
        venue.shared["returned"].state = "leased"
    elif blocker == "unknown-shared":
        monkeypatch.setattr(coordination, "list_leases", lambda **kwargs: None)
    elif blocker == "local":
        lease._write_leases({"returned": lease.Lease(
            "returned", "previous-effort", 1, "other-host", 0, 0,
        )})
    elif blocker == "corrupt-local":
        lease.LEASE_FILE.write_text("{", encoding="utf-8")
    elif blocker == "users":
        monkeypatch.setattr("agent_codespaces.pool.codespaces_in_use", lambda names: None)
    elif blocker == "headroom":
        venue.rows[1].state = "Available"
        venue.rows[1].machine = "custom64core"
    before = lease.LEASE_FILE.read_bytes() if lease.LEASE_FILE.exists() else None
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "pressure" in capsys.readouterr().err
    assert venue.events == []
    assert (lease.LEASE_FILE.read_bytes() if lease.LEASE_FILE.exists() else None) == before


def test_unsupported_receiver_preserves_baseline_without_new_invocation(venue, monkeypatch, capsys):
    monkeypatch.setattr(coordination, "preflight", lambda holder: coordination.PreflightResult("ready"))
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert venue.events == []
    assert "owned" in capsys.readouterr().err


@pytest.mark.parametrize("evidence", ["unavailable", "omitted"])
def test_tokened_workstream_needs_shared_evidence_even_with_spare_slots(venue, monkeypatch, capsys, evidence):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    venue.config.max_owned_codespaces = 31
    if evidence == "unavailable":
        monkeypatch.setattr(coordination, "list_leases", lambda **kw: None)
    else:
        venue.shared.pop("returned")
    before = list(venue.events)
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "unconfirmed" in capsys.readouterr().err
    assert venue.events == before


@pytest.mark.parametrize("evidence", [None, {}])
def test_tokenless_local_workstream_retains_legacy_optional_peer_fallback(venue, monkeypatch, evidence):
    from agent_codespaces import reuse
    import time

    local = {"returned": lease.Lease(
        "returned", "new-worktree", 1, "test-host", time.time(), time.time(),
        worktree="new-worktree",
    )}
    assert reuse.workstream_mode("returned", local, evidence) == "own"


@pytest.mark.parametrize("journaled", [False, True])
def test_ordinary_cli_claim_and_legacy_tokened_claim_return_from_empty_ledger(venue, monkeypatch, capsys, journaled):
    monkeypatch.setattr(lease, "active_worktree_ids", lambda: {"new-worktree"})

    def acquire(name, holder, **kwargs):
        assert not kwargs
        venue.shared[name] = coordination.L2Lease(
            name, holder, True, token="b" * 40, state="leased",
        )
        return coordination.L2Result("ok", token="b" * 40)

    monkeypatch.setattr(coordination, "acquire", acquire)
    if journaled:
        assert cli.main(["claim", "returned", "--owner", "new-worktree"]) == 0
        assert any(event[0] == "journal" for event in venue.events)
    else:
        lease.claim("returned", "new-worktree", holder_ref="machine/project/new-worktree")
        assert not any(event[0] == "journal" for event in venue.events)
    assert cli.main(["release-claim", "returned", "--owner", "new-worktree"]) == 0
    assert venue.shared["returned"].state == "released"
    assert lease.get_lease("returned") is None
    assert any(event[0] == "settle" for event in venue.events) is journaled
    capsys.readouterr()


def test_tokened_legacy_return_does_not_infer_absence_from_unreadable_ledger(venue, monkeypatch, capsys):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    before = lease.LEASE_FILE.read_bytes()
    monkeypatch.setattr(coordination, "_run", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="ownership lookup"):
        lease.release_claim("returned", "new-worktree")
    assert lease.LEASE_FILE.read_bytes() == before
    assert venue.shared["returned"].state == "leased"
    assert not any(event[0] in ("release", "settle") for event in venue.events)


@pytest.mark.parametrize("later_state", ["leased", "released"])
def test_settlement_failure_remains_recoverable_after_cross_host_reuse(
    venue, monkeypatch, capsys, later_state,
):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    settle = coordination.settle_obligation
    monkeypatch.setattr(coordination, "settle_obligation", lambda *a, **kw: False)
    with pytest.raises(RuntimeError, match="settlement.*retained"):
        lease.release_claim("returned", "new-worktree")
    assert venue.shared["returned"].state == "released"
    venue.shared["returned"] = coordination.L2Lease(
        "returned", "other-machine/project/second-worktree", later_state == "leased",
        token="d" * 40, state=later_state,
    )
    later = vars(venue.shared["returned"]).copy()
    release_count = sum(event[0] == "release" for event in venue.events)
    monkeypatch.setattr(coordination, "settle_obligation", settle)
    assert lease.release_claim("returned", "new-worktree")
    assert not venue.ledger["new-worktree"]
    assert lease.get_lease("returned") is None
    assert vars(venue.shared["returned"]) == later
    assert sum(event[0] == "release" for event in venue.events) == release_count


def test_overlapping_return_cannot_settle_same_owner_reacquisition(venue, monkeypatch, capsys):
    from agent_codespaces import reuse

    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    settle = coordination.settle_obligation
    entered = False

    def overlapping_settle(name, holder, **kwargs):
        nonlocal entered
        if not entered:
            entered = True
            for action in (
                lambda: lease.release_claim(name, "new-worktree"),
                lambda: lease.claim(name, "new-worktree", holder_ref=holder),
                lambda: lease.heartbeat(name),
                lambda: lease.borrow("other-effort", name, force=True),
                lambda: reuse.reserve(name, "example/project", venue.config, "selected", {}),
            ):
                with pytest.raises(RuntimeError, match="busy"):
                    action()
        return settle(name, holder, **kwargs)

    monkeypatch.setattr(coordination, "settle_obligation", overlapping_settle)
    assert lease.release_claim("returned", "new-worktree")
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert lease.get_lease("returned").worktree == "new-worktree"
    assert venue.ledger["new-worktree"] == {"returned"}
    assert sum(event[0] == "settle" for event in venue.events) == 1
    capsys.readouterr()


@pytest.mark.parametrize("entry", ["cli", "connect"])
def test_native_claim_journaling_is_inside_claim_return_exclusion(venue, monkeypatch, capsys, entry):
    monkeypatch.setattr(lease, "active_worktree_ids", lambda: {"new-worktree"})
    journal = coordination.journal_obligation

    def acquire(name, holder, **kwargs):
        venue.shared[name] = coordination.L2Lease(
            name, holder, True, token="b" * 40, state="leased",
        )
        return coordination.L2Result("ok", token="b" * 40)

    def overlapping_journal(name, holder, **kwargs):
        with pytest.raises(RuntimeError, match="busy"):
            lease.release_claim(name, "new-worktree")
        # Journaling is a native RPC, not part of the short store transaction.
        with lease._lease_lock():
            assert lease._read_leases()[name].worktree == "new-worktree"
        return journal(name, holder, **kwargs)

    monkeypatch.setattr(coordination, "acquire", acquire)
    monkeypatch.setattr(coordination, "journal_obligation", overlapping_journal)
    if entry == "cli":
        assert cli.main(["claim", "returned", "--owner", "new-worktree"]) == 0
    else:
        assert lease.claim_for_connect("returned") == "machine/project/new-worktree"
    assert venue.ledger["new-worktree"] == {"returned"}
    assert lease.release_claim("returned", "new-worktree")
    assert not venue.ledger["new-worktree"]
    capsys.readouterr()


@pytest.mark.parametrize("bulk", [False, True])
def test_preselected_return_cannot_release_a_replacement_same_owner_claim(venue, monkeypatch, capsys, bulk):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    release = lease.release_claim
    replacement = None

    def delayed_release(name, owner, **kwargs):
        nonlocal replacement
        assert release(name, owner)
        assert cli.main(["create", "example/project", "--no-wait"]) == 0
        replacement = lease.LEASE_FILE.read_bytes()
        return release(name, owner, **kwargs)

    monkeypatch.setattr(lease, "release_claim", delayed_release)
    with pytest.raises(RuntimeError, match="changed"):
        if bulk:
            lease.release_worktree_claims("new-worktree")
        else:
            lease.release("returned")
    assert replacement is not None and lease.LEASE_FILE.read_bytes() == replacement
    assert venue.ledger["new-worktree"] == {"returned"}
    assert venue.shared["returned"].state == "leased"
    capsys.readouterr()


def test_unsettled_return_survives_other_writes_and_blocks_owner_reentry(venue, monkeypatch, capsys):
    from agent_codespaces import reuse
    from agent_codespaces.claim_return import RETURN_KEY

    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(coordination, "settle_obligation", lambda *a, **kw: False)
    with pytest.raises(RuntimeError, match="settlement"):
        lease.release_claim("returned", "new-worktree")
    saved = json.loads(lease.LEASE_FILE.read_text(encoding="utf-8"))[RETURN_KEY]
    lease.borrow("other-effort", "other-0")
    assert lease.heartbeat("other-0")
    assert json.loads(lease.LEASE_FILE.read_text(encoding="utf-8"))[RETURN_KEY] == saved
    # The unchanged tolerant reader still sees the exact legacy claim record.
    assert lease._read_leases()["returned"] == reuse.local_snapshot()["returned"]
    assert lease.get_lease("returned", ttl=0) is not None
    assert "returned" not in lease.sweep_dead(active=set(), ttl=0)
    before = lease.LEASE_FILE.read_bytes()
    for action in (
        lambda: lease.claim("returned", "new-worktree", holder_ref="machine/project/new-worktree"),
        lambda: lease.heartbeat("returned"),
        lambda: lease.borrow("other-effort", "returned", force=True),
        lambda: reuse.reserve("returned", "example/project", venue.config, "selected", {}),
    ):
        with pytest.raises(RuntimeError, match="unsettled|local holder"):
            action()
        assert lease.LEASE_FILE.read_bytes() == before


@pytest.mark.parametrize("damage", ["version", "holder", "snapshot", "phase", "missing-lease"])
def test_malformed_return_progress_never_becomes_an_empty_claim_set(venue, monkeypatch, capsys, damage):
    from agent_codespaces import reuse
    from agent_codespaces.claim_return import RETURN_KEY

    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(coordination, "settle_obligation", lambda *a, **kw: False)
    with pytest.raises(RuntimeError):
        lease.release_claim("returned", "new-worktree")
    raw = json.loads(lease.LEASE_FILE.read_text(encoding="utf-8"))
    progress = raw[RETURN_KEY]["returns"]["returned"]
    if damage == "version":
        raw[RETURN_KEY]["version"] = 2
    elif damage == "holder":
        progress["holder"] = "new-worktree"
    elif damage == "snapshot":
        progress["lease"]["lease_token"] = "e" * 40
    elif damage == "phase":
        progress["phase"] = "finished"
    else:
        del raw["returned"]
    lease.LEASE_FILE.write_text(json.dumps(raw), encoding="utf-8")
    before = lease.LEASE_FILE.read_bytes()
    for action in (
        reuse.local_snapshot,
        lambda: lease.release_claim("returned", "new-worktree"),
        lambda: lease.borrow("other-effort", "other-0"),
    ):
        with pytest.raises(RuntimeError, match="progress.*malformed"):
            action()
        assert lease.LEASE_FILE.read_bytes() == before


def test_claim_guard_excludes_a_real_child_and_releases_after_process_exit(venue):
    from agent_procutil import no_window_flags
    from agent_codespaces.claim_guard import claim_guard

    script = """
import os, sys
from pathlib import Path
from agent_codespaces import lease
from agent_codespaces.claim_guard import claim_guard
lease.LEASE_FILE = Path(sys.argv[1])
try:
    with claim_guard("returned"):
        os._exit(0)
except RuntimeError as error:
    assert "busy" in str(error)
    sys.exit(7)
"""
    def child():
        return subprocess.run(
            [sys.executable, "-c", script, str(lease.LEASE_FILE)],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            capture_output=True, text=True, timeout=15, creationflags=no_window_flags(),
        )

    with claim_guard("returned"):
        result = child()
        assert result.returncode == 7, result.stderr
        # A different target remains independent while the original is held.
        with claim_guard("other-0"):
            pass
    result = child()
    assert result.returncode == 0, result.stderr
    with claim_guard("returned"):
        pass


def test_old_writer_losing_progress_never_confirms_another_borrowers_return(venue, monkeypatch, capsys):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    settle = coordination.settle_obligation
    monkeypatch.setattr(coordination, "settle_obligation", lambda *a, **kw: False)
    with pytest.raises(RuntimeError, match="settlement"):
        lease.release_claim("returned", "new-worktree")
    legacy = {name: vars(record) for name, record in lease._read_leases().items()}
    lease.LEASE_FILE.write_text(json.dumps(legacy), encoding="utf-8")
    venue.shared["returned"] = coordination.L2Lease(
        "returned", "other-machine/project/second-worktree", False,
        token="d" * 40, state="released",
    )
    monkeypatch.setattr(coordination, "settle_obligation", settle)
    before = lease.LEASE_FILE.read_bytes()
    with pytest.raises(RuntimeError, match="unconfirmed"):
        lease.release_claim("returned", "new-worktree")
    assert lease.LEASE_FILE.read_bytes() == before
    assert venue.ledger["new-worktree"] == {"returned"}


@pytest.mark.parametrize("phase", ["prepared", "released"])
@pytest.mark.parametrize("published", [False, True])
def test_progress_publication_failure_never_settles_before_recoverable_release(
    venue, monkeypatch, capsys, phase, published,
):
    from agent_codespaces.claim_return import RETURN_KEY

    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    write = lease._write_private_json
    original = lease._read_leases()["returned"]

    def failing_write(path, payload):
        progress = payload.get(RETURN_KEY, {}).get("returns", {}).get("returned")
        if progress and progress["phase"] == phase:
            if published:
                write(path, payload)
            raise OSError("publication acknowledgement unavailable")
        write(path, payload)

    monkeypatch.setattr(lease, "_write_private_json", failing_write)
    with pytest.raises(OSError, match="publication"):
        lease.release_claim("returned", "new-worktree")
    assert lease._read_leases()["returned"] == original
    assert venue.ledger["new-worktree"] == {"returned"}
    assert not any(event[0] == "settle" for event in venue.events)
    assert venue.shared["returned"].state == ("leased" if phase == "prepared" else "released")
    monkeypatch.setattr(lease, "_write_private_json", write)
    assert lease.release_claim("returned", "new-worktree")
    assert not venue.ledger["new-worktree"]
    assert sum(event[0] == "release" for event in venue.events) == 1


def test_quota_retry_reserves_the_returned_venue_instead_of_creating_again(venue, monkeypatch, capsys):
    venue.config.max_owned_codespaces = 31
    venue.rows[0].state = "Available"
    monkeypatch.setattr(cli, "_require_codespace_scope", lambda *args, **kwargs: None)
    creates = []

    def create(*args, **kwargs):
        creates.append(kwargs)
        raise RuntimeError("running codespace quota exceeded")

    def reclaim(*args, **kwargs):
        assert kwargs["account"] == "selected"
        venue.rows[0].state = "Shutdown"
        return "stopped eligible returned venue"

    monkeypatch.setattr(cli, "create_codespace", create)
    monkeypatch.setattr(cli, "_reclaim_for_quota", reclaim)
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert capsys.readouterr().out.strip().endswith("returned")
    assert len(creates) == 1
    assert creates[0]["account"] == "selected"
    assert lease.get_lease("returned").worktree == "new-worktree"
    assert sum(event[0] == "acquire" for event in venue.events) == 1

def test_explicit_branch_mismatch_does_not_reset_returned_venue(venue, capsys):
    assert cli.main(["create", "example/project", "--branch", "main", "--no-wait"]) == 4
    assert venue.rows[0].branch == "retained-feature"
    assert venue.events == []
    capsys.readouterr()


def test_reuse_does_not_silently_reduce_requested_machine_capacity(venue, capsys):
    venue.config.default_machine_type = "largePremiumLinux"
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert venue.events == []
    capsys.readouterr()


@pytest.mark.parametrize("profile", [
    "explicit-devcontainer", "configured-devcontainer", "global-devcontainer",
    "configured-location", "global-location", "display-name",
])
def test_creation_profile_is_not_silently_replaced_by_unqualified_reuse(venue, capsys, profile):
    from agent_codespaces.config import RepoConfig

    extra = []
    if profile == "explicit-devcontainer":
        extra = ["--devcontainer-path", ".devcontainer/alternate/devcontainer.json"]
    elif profile == "configured-devcontainer":
        venue.config.repos["example/project"] = RepoConfig(
            devcontainer_path=".devcontainer/alternate/devcontainer.json",
        )
    elif profile == "global-devcontainer":
        venue.config.default_devcontainer_path = ".devcontainer/alternate/devcontainer.json"
    elif profile == "configured-location":
        venue.config.repos["example/project"] = RepoConfig(location="WestEurope")
    elif profile == "global-location":
        venue.config.default_location = "WestEurope"
    else:
        extra = ["--display-name", "new-profile"]
    assert cli.main(["create", "example/project", "--no-wait", *extra]) == 4
    assert venue.events == []
    assert "pressure" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [
    "unknown-acquire", "inventory-drift", "inventory-omission", "local-race",
])
def test_failed_reservation_retains_native_responsibility_without_replay(venue, monkeypatch, capsys, failure):
    from agent_codespaces import driving_worktrees

    if failure in ("unknown-acquire", "inventory-omission"):
        def acquire(*args, **kwargs):
            venue.shared["returned"] = coordination.L2Lease(
                "returned", "machine/project/new-worktree", True, token="b" * 40, state="leased",
            )
            return coordination.L2Result("unavailable")

        monkeypatch.setattr(coordination, "acquire", acquire)
    elif failure == "inventory-drift":
        original = coordination.acquire

        def acquire(*args, **kwargs):
            result = original(*args, **kwargs)
            venue.rows[0].state = "Available"
            return result

        monkeypatch.setattr(coordination, "acquire", acquire)
    else:
        original = coordination.acquire

        def acquire(*args, **kwargs):
            result = original(*args, **kwargs)
            lease._write_leases({"returned": lease.Lease(
                "returned", "racing-effort", 1, "other-host", 0, 0,
            )})
            return result

        monkeypatch.setattr(coordination, "acquire", acquire)
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "retained" in capsys.readouterr().err
    assert venue.events[0][0] == "journal"
    assert not any(event[0] == "release" for event in venue.events)
    assert driving_worktrees.current_workstream_account("example/project") == "selected"
    if failure == "local-race":
        assert lease._read_leases()["returned"].effort == "racing-effort"
    prior = list(venue.events)
    venue.config.max_owned_codespaces = 31
    if failure == "inventory-omission":
        venue.rows.pop(0)
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "unconfirmed" in capsys.readouterr().err
    assert venue.events == prior


@pytest.mark.parametrize("omitted", [False, True])
@pytest.mark.parametrize("first_held", [False, True])
def test_every_obligation_is_classified_before_reuse_or_new_creation(
    venue, monkeypatch, capsys, omitted, first_held,
):
    from agent_codespaces import account_binding, driving_worktrees
    import time

    venue.config.max_owned_codespaces = 31
    venue.rows[0].name = "a-returned"
    venue.shared["a-returned"] = venue.shared.pop("returned")
    venue.shared["a-returned"].key = "a-returned"
    venue.shared["b-uncertain"] = coordination.L2Lease(
        "b-uncertain", "machine/project/new-worktree", True, token="d" * 40, state="leased",
    )
    if first_held:
        lease._write_leases({"a-returned": lease.Lease(
            "a-returned", "new-worktree", 1, "test-host", time.time(), time.time(),
            worktree="new-worktree", lease_token="b" * 40,
        )})
        venue.shared["a-returned"] = coordination.L2Lease(
            "a-returned", "machine/project/new-worktree", True, token="b" * 40, state="leased",
        )
    if not omitted:
        venue.rows.append(CodespaceInfo(
            name="b-uncertain", display_name="b-uncertain", repository="example/project",
            branch="main", state="Shutdown", machine="premiumLinux", account="selected",
        ))
    account_binding.bind("b-uncertain", "selected", repo="example/project", strict=True)
    monkeypatch.setattr(
        driving_worktrees, "_workstream_claim_refs",
        lambda owner: {"a-returned", "b-uncertain"},
    )
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "b-uncertain" in capsys.readouterr().err
    assert venue.events == []


@pytest.mark.parametrize("holder", [
    "other-machine/project/new-worktree", "machine/other-project/new-worktree",
])
def test_workstream_does_not_resume_a_same_named_foreign_holder(venue, monkeypatch, capsys, holder):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    venue.shared["returned"].holder = holder
    venue.config.max_owned_codespaces = 31
    before = list(venue.events)
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "unconfirmed" in capsys.readouterr().err
    assert venue.events == before


@pytest.mark.parametrize("failure", ["release-unknown", "release-conflict", "settle-unknown", "foreign-token"])
def test_return_keeps_local_custody_when_confirmation_is_missing(venue, monkeypatch, capsys, failure):
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    capsys.readouterr()
    before = lease.LEASE_FILE.read_bytes()
    if failure.startswith("release-"):
        monkeypatch.setattr(coordination, "release", lambda *a, **kw: coordination.L2Result(
            "conflict" if failure == "release-conflict" else "unavailable",
        ))
    elif failure == "settle-unknown":
        monkeypatch.setattr(coordination, "settle_obligation", lambda *a, **kw: False)
    else:
        venue.shared["returned"].token = "d" * 40
    with pytest.raises(RuntimeError, match="retained"):
        lease.release_claim("returned", "new-worktree")
    assert json.loads(lease.LEASE_FILE.read_bytes())["returned"] == json.loads(before)["returned"]
    if failure != "settle-unknown":
        assert not any(event[0] == "settle" for event in venue.events)


def test_return_settles_original_owner_before_another_workstream_reuses(venue, monkeypatch, capsys):
    from agent_codespaces import driving_worktrees

    current = ["new-worktree"]
    obligations = venue.ledger
    obligations["second-worktree"] = set()
    monkeypatch.setattr(lease, "resolve_owner_worktree", lambda: current[0])
    monkeypatch.setattr(coordination, "owner_ref", lambda: f"machine/project/{current[0]}")
    monkeypatch.setattr(driving_worktrees, "_workstream_claim_refs", lambda owner: obligations[current[0]])

    def journal(name, holder, **kwargs):
        obligations[holder.rsplit("/", 1)[-1]].add(name)
        return True

    def settle(name, holder, **kwargs):
        assert venue.shared[name].state == "released"
        assert lease._read_leases()[name].worktree == holder.rsplit("/", 1)[-1]
        obligations[holder.rsplit("/", 1)[-1]].discard(name)
        return True

    monkeypatch.setattr(coordination, "journal_obligation", journal)
    monkeypatch.setattr(coordination, "settle_obligation", settle)
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert obligations["new-worktree"] == {"returned"}
    assert lease.release_claim("returned", "new-worktree")
    assert not obligations["new-worktree"]
    current[0] = "second-worktree"
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert lease.get_lease("returned").worktree == "second-worktree"
    current[0] = "new-worktree"
    venue.config.max_owned_codespaces = 31
    decision = allocation.plan_creation("example/project", venue.config, "selected", {"GH_TOKEN": "fixture"})
    assert decision.action == "create"
    assert not obligations["new-worktree"]
    capsys.readouterr()

def test_context_refusal_after_shared_acquire_preserves_classification_and_custody(venue, monkeypatch, capsys):
    from agent_codespaces.worktrees import ContextRefused

    def listing(*args, **kwargs):
        if any(event[0] == "acquire" for event in venue.events):
            raise ContextRefused("fixture selected context retired")
        return venue.rows

    monkeypatch.setattr(allocation, "list_account_codespaces", listing)
    assert cli.main(["create", "example/project", "--no-wait"]) == cli._COORDINATION_EXIT
    assert "retained" in capsys.readouterr().err
    assert venue.shared["returned"].state == "leased"
    assert not any(event[0] == "release" for event in venue.events)


def test_reservation_serializes_inventory_and_marker_with_destructive_lifecycle(venue, monkeypatch, capsys):
    from contextlib import contextmanager

    held = False
    phases = []

    @contextmanager
    def target_lock(name, **kwargs):
        nonlocal held
        assert name == "returned"
        held = True
        try:
            yield object()
        finally:
            held = False

    def listing(*args, **kwargs):
        if any(event[0] == "acquire" for event in venue.events):
            assert held, "delete can enter before the fresh inventory read"
            phases.append("inventory")
        return venue.rows

    def marker(name):
        assert held, "delete can enter before eligibility is cleared"
        phases.append("marker")

    monkeypatch.setattr("agent_codespaces.lifecycle_lock.lifecycle_lock", target_lock)
    monkeypatch.setattr(allocation, "list_account_codespaces", listing)
    monkeypatch.setattr("agent_codespaces.status.clear_status", marker)
    assert cli.main(["create", "example/project", "--no-wait"]) == 0
    assert phases == ["inventory", "marker"] and not held
    capsys.readouterr()
