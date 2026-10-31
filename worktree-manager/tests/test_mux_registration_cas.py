"""Reconnect reconciliation cannot overwrite a newer creation snapshot."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from worktree_manager import mux_daemon, mux_daemon_cli, mux_discovery, mux_mapping_registry as registry


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "default_root", lambda: tmp_path)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *args, **kwargs: {"applied": True})
    def native(command, **kwargs):
        suffix = f"\t{tmp_path}" if "pane_current_path" in command[-1] else ""
        return SimpleNamespace(returncode=0, stdout=f"%42\t0\tmanaged\t$7:100{suffix}\n")
    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", native)
    monkeypatch.setattr(mux_discovery, "run_mux_child", native)
    return {
        "project": "example", "worktree_id": "one", "live": True,
        "worktree_path": str(tmp_path), "mux_bin": "owned-mux",
        "mux_session": "managed", "session_incarnation": "$7:100",
    }


def _cli(pane="%42"):
    return [{"pane_id": pane, "role": "cli", "live": True}]


@pytest.mark.parametrize("alias_kind", ["dotdot", "case"])
def test_equivalent_path_reconnect_preserves_cli_exit_and_native_identity(
    owner, monkeypatch, alias_kind,
):
    if alias_kind == "case" and os.name != "nt":
        pytest.skip("Case aliases require Windows path semantics")
    path = Path(owner["worktree_path"])
    (path / "sub").mkdir()
    alias = str(path / "sub" / "..") if alias_kind == "dotdot" else str(path).swapcase()
    registry.register_next_mapping(dict(
        owner, panes=[*_cli(), *_cli("%99")], native_session_incarnation="$7:100",
    ))
    assert registry.remove_mapping(
        "example", "one", session_incarnation="$7:100", pane_id="%99", exit_code=7,
    )["applied"]
    before = registry.get_mapping("example", "one")

    def unavailable(*args, **kwargs):
        raise TimeoutError("unknown inventory must retain the same owner")

    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", unavailable)
    assert mux_daemon_cli._register_reconnect(dict(owner, worktree_path=alias))["applied"]
    current = registry.get_mapping("example", "one")
    assert current["panes"] == before["panes"]
    assert current["observed_end"] == before["observed_end"]
    assert current["native_session_incarnation"] == "$7:100"
    saved = registry.registry_path().read_bytes()
    assert registry.remove_mapping(
        "example", "one", session_incarnation="$7:100", pane_id="%99", exit_code=7,
    ) == {"applied": True, "reason": "already-observed"}
    assert registry.registry_path().read_bytes() == saved
    assert mux_daemon_cli._register_reconnect(
        dict(owner, worktree_path=alias, panes=_cli("%99")), creation=True,
    ) == {"applied": False, "reason": "ended-pane"}
    assert registry.remove_mapping(
        "example", "one", session_incarnation="$7:100", pane_id="%42", exit_code=0,
    )["applied"]
    current = registry.get_mapping("example", "one")
    assert current["live"] is False
    assert current["observed_end"]["exit_code"] == 0


@pytest.mark.parametrize("operation", ["register", "discover"])
def test_equivalent_path_write_still_invalidates_raw_snapshot_cas(owner, operation):
    path = Path(owner["worktree_path"])
    (path / "sub").mkdir()
    registry.register_next_mapping(dict(owner, panes=_cli()))
    snapshot = registry.get_mapping("example", "one")
    assert registry.register_next_mapping(dict(
        snapshot, worktree_path=str(path / "sub" / ".."),
    ))["applied"]
    if operation == "register":
        result = registry.register_next_mapping(owner, expected=snapshot)
    else:
        result = registry.MuxMappingRegistry(registry.registry_path()).discover(owner, expected=snapshot)
    assert result == {"applied": False, "reason": "mapping-changed"}


def test_explicit_absence_fence_rejects_a_new_mapping(owner):
    assert registry.register_next_mapping(dict(owner, panes=_cli()), expected=None)["applied"]
    result = registry.register_next_mapping(dict(owner, panes=[]), expected=None)
    assert result == {"applied": False, "reason": "mapping-changed"}
    assert registry.get_mapping("example", "one")["panes"] == _cli()


def test_stale_snapshot_does_not_allocate_a_revision_and_omission_remains_unconditional(owner):
    registry.register_next_mapping(dict(owner, panes=_cli()))
    snapshot = registry.get_mapping("example", "one")
    registry.register_next_mapping(dict(owner, panes=_cli("%99")))
    assert registry.register_next_mapping(dict(owner, panes=[]), expected=snapshot) == {
        "applied": False, "reason": "mapping-changed",
    }
    assert registry.get_mapping("example", "one")["mapping_revision"] == 2
    assert registry.register_next_mapping(dict(owner, panes=_cli())) == {"applied": True, "revision": 3}


def test_snapshot_cas_ignores_volatile_observation_and_status_updates(owner):
    registry.register_next_mapping(dict(owner, panes=_cli()))
    snapshot = registry.get_mapping("example", "one")
    registry.register_next_mapping(dict(
        snapshot, attached_clients=2, observed_at="refreshed",
        last_status_rendered_at="2026-01-01T00:00:00Z",
    ))
    assert registry.register_next_mapping(dict(owner, panes=_cli()), expected=snapshot)["applied"]


def test_same_revision_pane_replacement_invalidates_snapshot(owner):
    registry.register_next_mapping(dict(owner, panes=_cli()))
    snapshot = registry.get_mapping("example", "one")
    registry.register_next_mapping(dict(snapshot, panes=_cli("%99")))
    assert registry.register_next_mapping(dict(owner, panes=[]), expected=snapshot) == {
        "applied": False, "reason": "mapping-changed",
    }


def test_conditional_registration_cannot_overwrite_unreadable_registry(owner):
    path = registry.registry_path()
    path.write_text("{invalid", encoding="utf-8")
    assert registry.register_next_mapping(owner, expected=None) == {
        "applied": False, "reason": "registry-unavailable",
    }
    assert path.read_text(encoding="utf-8") == "{invalid"


@pytest.mark.parametrize("created_incarnation", ["$7:100", "$8:200"])
def test_overlapping_creation_cannot_be_clobbered_by_precomputed_empty_reconnect(
    owner, monkeypatch, created_incarnation,
):
    pending, created = Event(), Event()
    attempts = []
    register = mux_daemon_cli.register_next_mapping

    def gate(payload, *, expected):
        attempts.append(expected)
        if len(attempts) == 1:
            assert expected is None
            assert payload["panes"] == []
            pending.set()
            assert created.wait(3)
        return register(payload, expected=expected)

    monkeypatch.setattr(mux_daemon_cli, "register_next_mapping", gate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reconnect = pool.submit(mux_daemon_cli._register_reconnect, owner)
        try:
            assert pending.wait(3)
            result = mux_daemon.register_managed_mapping(dict(
                owner, session_incarnation=created_incarnation, panes=_cli(),
            ))
            assert result["applied"]
        finally:
            created.set()
        result = reconnect.result(timeout=5)
    entry = registry.get_mapping("example", "one")
    assert entry["panes"] == _cli()
    assert entry["session_incarnation"] == created_incarnation
    if created_incarnation == owner["session_incarnation"]:
        assert result["applied"]
        assert len(attempts) == 2
    else:
        assert result == {"applied": False, "reason": "mapping-changed"}
        assert len(attempts) == 1


def test_reconnect_snapshot_contention_has_a_visible_retry_bound(owner, monkeypatch):
    registry.register_next_mapping(dict(owner, panes=_cli()))
    attempts = []

    def changed(payload, **kwargs):
        attempts.append(payload)
        return {"applied": False, "reason": "mapping-changed"}

    monkeypatch.setattr(mux_daemon_cli, "register_next_mapping", changed)
    assert mux_daemon_cli._register_reconnect(owner) == {"applied": False, "reason": "mapping-changed"}
    assert len(attempts) == 3


def test_unknown_reconnect_probe_preserves_same_incarnation_cli(owner, monkeypatch):
    registry.register_next_mapping(dict(owner, panes=_cli()))

    def unavailable(*args, **kwargs):
        raise TimeoutError("native inventory unavailable")

    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", unavailable)
    assert mux_daemon_cli._register_reconnect(owner)["applied"]
    assert registry.get_mapping("example", "one")["panes"] == _cli()


@pytest.mark.parametrize("creation", [False, True])
@pytest.mark.parametrize("native", ["successor", "requested", "unknown", "wrong-path"])
def test_initial_lookup_conflict_requires_native_proof_before_replacement(
    owner, monkeypatch, native, creation,
):
    from worktree_manager import mux_discovery

    successor = dict(owner, session_incarnation="$8:200", panes=_cli("%99"))
    registry.register_next_mapping(successor)
    before = registry.get_mapping("example", "one")
    probes = []

    def inventory(command, **kwargs):
        probes.append(command)
        if native == "unknown":
            raise RuntimeError("native inventory unavailable")
        incarnation = "$8:200" if native == "successor" else owner["session_incarnation"]
        path = owner["worktree_path"] if native != "wrong-path" else owner["worktree_path"] + "-other"
        if command[1] == "list-panes":
            return SimpleNamespace(returncode=0, stdout=f"%42\t0\tmanaged\t{incarnation}\t{path}\n")
        return SimpleNamespace(returncode=0, stdout=f"managed\t1\t{incarnation}\t{path}\n")

    monkeypatch.setattr(mux_discovery, "run_mux_child", inventory)
    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", inventory)
    result = mux_daemon_cli._register_reconnect(dict(owner, panes=_cli()), creation=creation)
    assert probes and probes[0][1] == ("list-panes" if creation else "list-sessions")
    if native == "requested":
        assert result["applied"]
        current = registry.get_mapping("example", "one")
        assert current["session_incarnation"] == owner["session_incarnation"]
        assert current["panes"] == (_cli() if creation else [])
    else:
        assert not result["applied"]
        assert registry.get_mapping("example", "one") == before


def test_creation_uses_registered_pane_path_not_session_home(owner, monkeypatch):
    registry.register_next_mapping(dict(owner, session_incarnation="$8:200", panes=_cli("%99")))
    commands = []

    def native(command, **kwargs):
        commands.append(command)
        assert command[1] == "list-panes", "session_path is not the launch pane's cwd"
        assert "pane_current_path" in command[-1]
        return SimpleNamespace(
            returncode=0,
            stdout=f"%42\t0\tmanaged\t$7:100\t{owner['worktree_path']}\n",
        )

    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", native)
    monkeypatch.setattr(mux_discovery, "run_mux_child", native)
    assert mux_daemon_cli._register_reconnect(dict(owner, panes=_cli()), creation=True)["applied"]
    assert len(commands) == 1


@pytest.mark.parametrize("creation", [False, True])
@pytest.mark.parametrize("other_live", [False, True])
def test_exit_during_registration_cannot_be_revived_by_retry(
    owner, monkeypatch, creation, other_live,
):
    panes = [*_cli(), *(_cli("%99") if other_live else [])]
    registry.register_next_mapping(dict(owner, panes=panes))
    register = mux_daemon_cli.register_next_mapping
    exited = []

    def gate(payload, *, expected):
        if not exited:
            assert registry.remove_mapping(
                "example", "one", session_incarnation="$7:100", pane_id="%42", exit_code=7,
            )["applied"]
            exited.append(registry.get_mapping("example", "one")["observed_end"])
        return register(payload, expected=expected)

    monkeypatch.setattr(mux_daemon_cli, "register_next_mapping", gate)
    result = mux_daemon_cli._register_reconnect(dict(owner, panes=_cli()), creation=creation)
    current = registry.get_mapping("example", "one")
    assert current["observed_end"] == exited[0]
    assert next(pane for pane in current["panes"] if pane["pane_id"] == "%42")["live"] is False
    assert current["live"] is other_live
    assert result["applied"] is (other_live and not creation)


@pytest.mark.parametrize("creation", [False, True])
def test_same_incarnation_tombstone_refuses_even_positive_native_inventory(owner, monkeypatch, creation):
    registry.register_next_mapping(dict(owner, panes=_cli()))
    registry.remove_mapping("example", "one")
    before = registry.registry_path().read_bytes()
    monkeypatch.setattr(
        mux_discovery, "confirm_creation_cli",
        lambda *_: pytest.fail("native inventory cannot revoke a recorded tombstone"),
    )
    assert mux_daemon_cli._register_reconnect(dict(owner, panes=_cli()), creation=creation) == {
        "applied": False, "reason": "incumbent-tombstone",
    }
    assert registry.registry_path().read_bytes() == before


def test_reconnect_keeps_earlier_dead_pane_after_another_exit_replaces_latest_evidence(owner, monkeypatch):
    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="".join(f"{pane}\t0\tmanaged\t$7:100\n" for pane in ("%42", "%99", "%100")),
    ))
    registry.register_next_mapping(dict(owner, panes=[*_cli(), *_cli("%99"), *_cli("%100")]))
    for pane in ("%42", "%99"):
        assert registry.remove_mapping(
            "example", "one", session_incarnation="$7:100", pane_id=pane, exit_code=7,
        )["applied"]
        assert mux_daemon_cli._register_reconnect(owner)["applied"]
    current = registry.get_mapping("example", "one")
    assert current["observed_end"]["pane_id"] == "%99"
    assert {"%42", "%99"} <= {
        pane["pane_id"] for pane in current["panes"] if not pane["live"]
    }
    assert mux_daemon_cli._register_reconnect(dict(owner, panes=_cli()), creation=True) == {
        "applied": False, "reason": "ended-pane",
    }


@pytest.mark.parametrize("incumbent", [False, True])
@pytest.mark.parametrize("native", ["missing", "dead", "wrong-incarnation", "wrong-path", "unknown"])
def test_creation_always_confirms_current_native_cli_before_registration(owner, monkeypatch, incumbent, native):
    if incumbent:
        registry.register_next_mapping(dict(owner, panes=_cli("%99")))
    before = registry.get_mapping("example", "one")

    def inventory(command, **kwargs):
        assert command[1] == "list-panes"
        assert "pane_current_path" in command[-1]
        if native == "unknown":
            raise TimeoutError("native pane unconfirmed")
        pane = "%99" if native == "missing" else "%42"
        dead = "1" if native == "dead" else "0"
        incarnation = "$8:200" if native == "wrong-incarnation" else "$7:100"
        path = owner["worktree_path"] + ("-other" if native == "wrong-path" else "")
        return SimpleNamespace(returncode=0, stdout=f"{pane}\t{dead}\tmanaged\t{incarnation}\t{path}\n")

    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", inventory)
    monkeypatch.setattr(mux_discovery, "run_mux_child", inventory)
    assert not mux_daemon_cli._register_reconnect(dict(owner, panes=_cli()), creation=True)["applied"]
    assert registry.get_mapping("example", "one") == before
