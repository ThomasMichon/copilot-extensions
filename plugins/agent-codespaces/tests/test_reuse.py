"""Sequential reuse preserves prior work and requires positive release evidence."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_codespaces import allocation, coordination, lease
from agent_codespaces import __main__ as cli
from agent_codespaces.config import CodespacesConfig
from agent_codespaces.lifecycle import CodespaceInfo


@pytest.fixture
def venue(monkeypatch, tmp_path):
    from agent_codespaces import account_binding, pool, status, worktrees

    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lease, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(lease, "_DEPLOY_HOLDS_FILE", tmp_path / "deploy-holds.json")
    monkeypatch.setattr(lease, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(lease, "_this_host", lambda: "test-host")
    monkeypatch.setattr(lease, "resolve_owner_worktree", lambda: "new-worktree")
    monkeypatch.setattr(account_binding, "BINDINGS_FILE", tmp_path / "bindings.json")
    monkeypatch.setattr(account_binding, "_LOCK_FILE", tmp_path / "bindings.lock")
    monkeypatch.setattr(account_binding, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(coordination, "owner_ref", lambda: "machine/project/new-worktree")
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
    })
    events = []
    monkeypatch.setattr(
        "agent_codespaces.driving_worktrees.resolve_current_workstream_box",
        lambda *args: "returned" if any(event[0] == "journal" for event in events) else None,
    )
    monkeypatch.setattr(
        coordination, "journal_obligation",
        lambda name, holder: events.append(("journal", name, holder)) or True,
    )

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


@pytest.mark.parametrize("profile", ["explicit-devcontainer", "configured-devcontainer", "display-name"])
def test_creation_profile_is_not_silently_replaced_by_unqualified_reuse(venue, capsys, profile):
    from agent_codespaces.config import RepoConfig

    extra = []
    if profile == "explicit-devcontainer":
        extra = ["--devcontainer-path", ".devcontainer/alternate/devcontainer.json"]
    elif profile == "configured-devcontainer":
        venue.config.repos["example/project"] = RepoConfig(
            devcontainer_path=".devcontainer/alternate/devcontainer.json",
        )
    else:
        extra = ["--display-name", "new-profile"]
    assert cli.main(["create", "example/project", "--no-wait", *extra]) == 4
    assert venue.events == []
    assert "pressure" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["unknown-acquire", "inventory-drift", "local-race"])
def test_failed_reservation_retains_native_responsibility_without_replay(venue, monkeypatch, capsys, failure):
    import sys
    from types import ModuleType
    from agent_codespaces import driving_worktrees

    if failure == "unknown-acquire":
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
    peer = ModuleType("agent_worktrees")
    peer.claims_owner = SimpleNamespace(claims_of_owner=lambda *args: ["returned"])
    monkeypatch.setitem(sys.modules, "agent_worktrees", peer)
    assert driving_worktrees.current_workstream_account("example/project") == "selected"
    if failure == "local-race":
        assert lease._read_leases()["returned"].effort == "racing-effort"
    prior = list(venue.events)
    venue.config.max_owned_codespaces = 31
    assert cli.main(["create", "example/project", "--no-wait"]) == 4
    assert "unconfirmed" in capsys.readouterr().err
    assert venue.events == prior


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
