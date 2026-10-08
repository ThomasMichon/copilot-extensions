"""Tests for ``worktree_manager.mux_attached_clients`` (#4564 --
attached_clients freshness). Split out of ``test_mux_daemon.py`` to mirror
the module split (see ``mux_attached_clients.py``'s own docstring for why)."""

from __future__ import annotations

import subprocess

from worktree_manager import mux_attached_clients
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


def _fake_run_factory(*, list_clients_lines=None, list_clients_ok=True):
    def _fake_run(argv, **kwargs):
        if not list_clients_ok:
            return subprocess.CompletedProcess(argv, 1, stdout="")
        lines = list_clients_lines if list_clients_lines is not None else []
        stdout = "\n".join(lines) + ("\n" if lines else "")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    return _fake_run


def test_mux_attached_clients_counts_list_clients_lines(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run_factory(list_clients_lines=["/dev/pts/1: wt-1", "/dev/pts/2: wt-1"]),
    )
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") == 2


def test_mux_attached_clients_reports_zero_for_empty_but_successful_listing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_lines=[]))
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") == 0


def test_mux_attached_clients_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_ok=False))
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") is None


def test_mux_attached_clients_returns_none_on_exception(monkeypatch):
    def _fake_run(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") is None


def _entry(**overrides) -> dict:
    base = {
        "project": "proj",
        "worktree_id": "wt-1",
        "mux_session": "wt-1",
        "mux_bin": "psmux",
        "session_incarnation": "sess:1",
        "attached_clients": 0,
        "live": True,
        "mapping_revision": 1,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# refresh_attached_clients -- probe-call behavior (fake registry)
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """Minimal registry stand-in recording every ``update_attached_clients``
    call; always reports success unless ``fail`` is set."""

    def __init__(self, *, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def update_attached_clients(self, project, worktree_id, attached_clients, **guards):
        self.calls.append(
            {
                "project": project,
                "worktree_id": worktree_id,
                "attached_clients": attached_clients,
                **guards,
            }
        )
        return {"applied": not self.fail}


def test_refresh_attached_clients_persists_a_changed_count(monkeypatch):
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 3)
    registry = _FakeRegistry()
    current = _entry(attached_clients=0)
    updated = mux_attached_clients.refresh_attached_clients(registry, current)
    assert updated["attached_clients"] == 3
    assert registry.calls == [
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "attached_clients": 3,
            "mapping_revision": 1,
            "mux_session": "wt-1",
            "session_incarnation": "sess:1",
        }
    ]
    # the original mapping dict passed in must not be mutated in place
    assert current["attached_clients"] == 0


def test_refresh_attached_clients_skips_write_when_count_unchanged(monkeypatch):
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 5)
    registry = _FakeRegistry()
    current = _entry(attached_clients=5)
    assert mux_attached_clients.refresh_attached_clients(registry, current) is current
    assert registry.calls == []


def test_refresh_attached_clients_skips_write_on_unknown_probe_result(monkeypatch):
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: None)
    registry = _FakeRegistry()
    current = _entry(attached_clients=5)
    assert mux_attached_clients.refresh_attached_clients(registry, current) is current
    assert registry.calls == []


def test_refresh_attached_clients_returns_stale_snapshot_when_registry_rejects(monkeypatch):
    """When the registry reports the mapping was superseded meanwhile, the
    caller must get back the ORIGINAL (stale) snapshot, not one claiming
    the new count -- the refresh was correctly abandoned, not partially
    applied."""
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 3)
    registry = _FakeRegistry(fail=True)
    current = _entry(attached_clients=0)
    result = mux_attached_clients.refresh_attached_clients(registry, current)
    assert result is current
    assert result["attached_clients"] == 0


# ---------------------------------------------------------------------------
# refresh_attached_clients -- race safety against a real registry
# ---------------------------------------------------------------------------


def test_refresh_attached_clients_does_not_clobber_a_concurrent_session_replacement(
    tmp_path, monkeypatch
):
    """A stale snapshot taken before a slow
    ``list-clients`` probe must not silently overwrite a concurrent
    equal-revision session replacement (register()'s own guard permits
    such a replacement, so it will not catch this on its own)."""
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mux_session="session-a", session_incarnation="incarn-a"))
    stale_snapshot = registry.get("proj", "wt-1")

    # Simulate the race: between the stale snapshot being taken and the
    # probe's result landing, a concurrent launcher replaces the mapping
    # with a new session at the SAME revision.
    registry.register(
        _entry(mux_session="session-b", session_incarnation="incarn-b", mapping_revision=1)
    )

    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 4)
    result = mux_attached_clients.refresh_attached_clients(registry, stale_snapshot)

    # the refresh must be abandoned, not applied against the stale identity
    assert result is stale_snapshot
    stored = registry.get("proj", "wt-1")
    assert stored["mux_session"] == "session-b"
    assert stored["session_incarnation"] == "incarn-b"
    assert stored["attached_clients"] == 0  # untouched by the abandoned refresh


def test_refresh_attached_clients_applies_cleanly_with_no_concurrent_change(tmp_path, monkeypatch):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    current = registry.get("proj", "wt-1")
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 2)
    result = mux_attached_clients.refresh_attached_clients(registry, current)
    assert result["attached_clients"] == 2
    stored = registry.get("proj", "wt-1")
    assert stored["attached_clients"] == 2


# ---------------------------------------------------------------------------
# MuxMappingRegistry.update_attached_clients -- identity guard
# ---------------------------------------------------------------------------


def test_update_attached_clients_applies_when_identity_matches(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    result = registry.update_attached_clients(
        "proj", "wt-1", 2, mapping_revision=1, mux_session="wt-1", session_incarnation="sess:1"
    )
    assert result == {"applied": True, "changed": True}
    assert registry.get("proj", "wt-1")["attached_clients"] == 2


def test_update_attached_clients_no_op_when_count_already_matches(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(attached_clients=2))
    result = registry.update_attached_clients(
        "proj", "wt-1", 2, mapping_revision=1, mux_session="wt-1", session_incarnation="sess:1"
    )
    assert result == {"applied": True, "changed": False}


def test_update_attached_clients_rejects_mismatched_mux_session(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mux_session="session-a", session_incarnation="incarn-a"))
    result = registry.update_attached_clients(
        "proj",
        "wt-1",
        2,
        mapping_revision=1,
        mux_session="session-b",
        session_incarnation="incarn-b",
    )
    assert result == {"applied": False, "reason": "superseded"}
    assert registry.get("proj", "wt-1")["mux_session"] == "session-a"


def test_update_attached_clients_rejects_mismatched_revision(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=2))
    result = registry.update_attached_clients(
        "proj", "wt-1", 2, mapping_revision=1, mux_session="wt-1", session_incarnation="sess:1"
    )
    assert result == {"applied": False, "reason": "superseded"}


def test_update_attached_clients_rejects_when_mapping_absent(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    result = registry.update_attached_clients(
        "proj", "missing", 2, mapping_revision=1, mux_session="wt-1"
    )
    assert result == {"applied": False, "reason": "superseded"}


def test_update_attached_clients_rejects_equal_revision_tombstone(tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(attached_clients=2))
    registry.remove("proj", "wt-1", mapping_revision=1)
    result = registry.update_attached_clients(
        "proj", "wt-1", 0, mapping_revision=1, mux_session="wt-1", session_incarnation="sess:1"
    )
    assert result == {"applied": False, "reason": "superseded"}
    assert registry.get("proj", "wt-1")["attached_clients"] == 2


def test_observer_bounds_each_cycle_and_eventually_probes_all_31_mappings(tmp_path, monkeypatch):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    for i in range(31):
        registry.register(_entry(worktree_id=f"wt-{i:02}", mux_session=f"session-{i:02}"))
    now = [0.0]
    calls = []

    def probe(binary, session, timeout_s):
        calls.append((session, timeout_s))
        now[0] += timeout_s
        return None

    monkeypatch.setattr(mux_attached_clients.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", probe)
    observer = mux_attached_clients.AttachedClientObserver()
    for _ in range(16):
        before = len(calls)
        started = now[0]
        observer.observe(registry)
        assert len(calls) - before <= 2
        assert now[0] - started <= 2.0
        now[0] += 20.0
    assert {session for session, _ in calls} == {f"session-{i:02}" for i in range(31)}
    assert all(0 < timeout <= 1.0 for _, timeout in calls)


def test_observer_reserves_only_the_remaining_shared_budget(tmp_path, monkeypatch):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(worktree_id="a"))
    registry.register(_entry(worktree_id="b"))
    now = [0.0]
    timeouts = []

    def probe(binary, session, timeout_s):
        timeouts.append(timeout_s)
        now[0] += 1.75 if len(timeouts) == 1 else timeout_s
        return None

    monkeypatch.setattr(mux_attached_clients.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", probe)
    mux_attached_clients.AttachedClientObserver().observe(registry)
    assert timeouts == [1.0, 0.25]


def test_observer_attempt_cadence_does_not_depend_on_publication_success(tmp_path, monkeypatch):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    now = [0.0]
    calls = []
    monkeypatch.setattr(mux_attached_clients.time, "monotonic", lambda: now[0])

    def probe(*args):
        calls.append(now[0])
        return None

    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", probe)
    observer = mux_attached_clients.AttachedClientObserver()
    for second in (0, 1, 19, 20, 21, 39, 40):
        now[0] = float(second)
        observer.observe(registry)
    assert calls == [0.0, 20.0, 40.0]


def test_probe_replaces_undecodable_output_when_counting_lines(monkeypatch):
    def run(argv, **kwargs):
        text = b"client-\xff\nclient-two\n".decode(kwargs["encoding"], errors=kwargs["errors"])
        return subprocess.CompletedProcess(argv, 0, stdout=text)

    monkeypatch.setattr(subprocess, "run", run)
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") == 2
