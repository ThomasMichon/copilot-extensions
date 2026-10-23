"""Tests for ``worktree_manager.mux_attached_clients`` (#4564 --
attached_clients freshness). Split out of ``test_mux_daemon.py`` to mirror
the module split (see ``mux_attached_clients.py``'s own docstring for why)."""

from __future__ import annotations

import subprocess

import pytest

from worktree_manager import mux_attached_clients, mux_daemon_live, mux_discovery
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


def test_mux_attached_clients_returns_none_on_nonzero_exit(monkeypatch, caplog):
    caplog.set_level("DEBUG", logger=mux_attached_clients.__name__)
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_ok=False))
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") is None
    assert caplog.records[-1].levelname == "DEBUG"
    assert "psmux" in caplog.text and "wt-1" in caplog.text


def test_mux_attached_clients_returns_none_on_exception(monkeypatch, caplog):
    def _fake_run(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") is None
    assert caplog.records[-1].levelname == "WARNING"
    assert "psmux" in caplog.text and "wt-1" in caplog.text


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


@pytest.mark.parametrize("native", [False, True])
def test_observer_bounds_each_cycle_and_eventually_probes_all_31_mappings(tmp_path, monkeypatch, native):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    for i in range(31):
        registry.register(_entry(
            worktree_id=f"wt-{i:02}", mux_session=f"session-{i:02}",
            native_session_incarnation="$1:100" if native else None,
        ))
    now = [0.0]
    calls = []

    def probe(binary, session, timeout_s):
        calls.append((session, timeout_s))
        now[0] += timeout_s
        return None

    monkeypatch.setattr(mux_attached_clients.time, "monotonic", lambda: now[0])
    if native:
        monkeypatch.setattr(mux_attached_clients, "mux_attached_clients",
                            lambda *args: pytest.fail("native observation must replace the legacy probe"))
        monkeypatch.setattr(mux_discovery, "confirm_native_session", lambda entry, **kw: (
            probe(entry["mux_bin"], entry["mux_session"], kw["timeout_s"])
            or {"applied": False, "reason": "enumeration-unavailable"}
        ))
    else:
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


@pytest.mark.parametrize("native", [False, True])
def test_observer_reserves_only_the_remaining_shared_budget(tmp_path, monkeypatch, native):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    for worktree_id in ("a", "b"):
        registry.register(_entry(
            worktree_id=worktree_id, native_session_incarnation="$1:100" if native else None,
        ))
    now = [0.0]
    timeouts = []

    def probe(binary, session, timeout_s):
        timeouts.append(timeout_s)
        now[0] += 1.75 if len(timeouts) == 1 else timeout_s
        return None

    monkeypatch.setattr(mux_attached_clients.time, "monotonic", lambda: now[0])
    if native:
        monkeypatch.setattr(mux_attached_clients, "mux_attached_clients",
                            lambda *args: pytest.fail("native observation must replace the legacy probe"))
        monkeypatch.setattr(mux_discovery, "confirm_native_session", lambda entry, **kw: (
            probe(entry["mux_bin"], entry["mux_session"], kw["timeout_s"])
            or {"applied": False, "reason": "enumeration-unavailable"}
        ))
    else:
        monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", probe)
    mux_attached_clients.AttachedClientObserver().observe(registry)
    assert timeouts == [1.0, 0.25]


def _native_observer_fixture(monkeypatch, tmp_path, *, binary="tmux"):
    registry = MuxMappingRegistry(tmp_path / "mux-mapping.json")
    entry = _entry(
        mux_bin=binary, mux_session="wt-one", worktree_path=str(tmp_path),
        session_incarnation="$1:100", native_session_incarnation="$1:100",
        attached_clients=3,
    )
    registry.register(entry)
    published = []
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients",
                        lambda *args: pytest.fail("native entries must not add a legacy probe"))
    monkeypatch.setattr(mux_daemon_live, "publish_live_observation",
                        lambda entry, **kwargs: published.append(dict(entry)) or {"applied": True})
    return registry, entry, published


@pytest.mark.parametrize("code,listing,diagnostic", [
    (0, "", ""),
    (0, "other\t0\t$2:200\telsewhere\n", ""),
    (1, "", "no server running on /run/user/1000/tmux/default\n"),
    (1, "", "error connecting to /run/user/1000/tmux/default (No such file or directory)\n"),
])
def test_observer_retires_absence_without_any_render_request(
    monkeypatch, tmp_path, code, listing, diagnostic,
):
    registry, _, published = _native_observer_fixture(monkeypatch, tmp_path)
    probes = []
    monkeypatch.setattr(mux_discovery, "run_mux_child", lambda argv, **kwargs: (
        probes.append((argv, kwargs))
        or subprocess.CompletedProcess(argv, code, listing, diagnostic)
    ))
    mux_attached_clients.AttachedClientObserver().observe(registry)
    assert len(probes) == 1
    assert probes[0][0][1] == "list-sessions" and probes[0][1]["timeout"] <= 1.0
    assert not registry.has_any_live()
    assert len(published) == 1 and not published[0]["live"]


@pytest.mark.parametrize("binary,code,listing,diagnostic", [
    ("tmux", 1, "", "error connecting to /run/tmux (Permission denied)"),
    ("tmux", 1, "", "error connecting to /run/tmux (Connection refused)"),
    ("tmux", 1, "", "no sessions"),
    ("tmux", 1, "", ""),
    ("tmux", 127, "", "no server running on /run/tmux"),
    ("tmux", 1, "", "bad option\nno server running on /run/tmux"),
    ("tmux", 1, "partial output", "no server running on /run/tmux"),
    ("psmux", 1, "", "no server running on /run/tmux"),
    ("other-program", 1, "", "no server running on /run/tmux"),
    ("tmux", 0, "bad listing", ""),
    ("tmux", 0, "wt-one\t0\t$2:200\tforeign\n", ""),
    ("tmux", 1, b"partial output", "no server running on /run/tmux"),
    ("tmux", 1, "", b"no server running on /run/tmux"),
])
def test_observer_preserves_unknown_or_replaced_ownership(
    monkeypatch, tmp_path, binary, code, listing, diagnostic,
):
    registry, _, published = _native_observer_fixture(monkeypatch, tmp_path, binary=binary)
    before = registry.snapshot()
    monkeypatch.setattr(mux_discovery, "run_mux_child",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, code, listing, diagnostic))
    mux_attached_clients.AttachedClientObserver().observe(registry)
    assert registry.snapshot() == before
    assert published == []


@pytest.mark.parametrize("failure", [
    FileNotFoundError("fixture mux unavailable"), subprocess.TimeoutExpired("tmux", 1),
])
def test_native_probe_failure_keeps_mapping_and_completion_cadence(monkeypatch, tmp_path, failure):
    registry, _, published = _native_observer_fixture(monkeypatch, tmp_path)
    before = registry.snapshot()
    now = [100.0]
    calls = []

    def failed_probe(argv, **kwargs):
        calls.append(kwargs["timeout"])
        now[0] += 1.0
        raise failure

    monkeypatch.setattr(mux_attached_clients.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mux_discovery, "run_mux_child", failed_probe)
    observer = mux_attached_clients.AttachedClientObserver()
    observer.observe(registry)
    now[0] = 120.0
    observer.observe(registry)
    assert calls == [1.0]
    now[0] = 121.0
    observer.observe(registry)
    assert calls == [1.0, 1.0]
    assert registry.snapshot() == before and published == []


@pytest.mark.parametrize("revision,incarnation", [(2, "$1:100"), (1, "$2:200")])
@pytest.mark.parametrize("absent", [False, True])
def test_observer_native_result_cannot_overwrite_a_concurrent_mapping(
    monkeypatch, tmp_path, revision, incarnation, absent,
):
    registry, entry, published = _native_observer_fixture(monkeypatch, tmp_path)

    def replacement_during_probe(argv, **kwargs):
        registry.register(dict(
            entry, mapping_revision=revision, session_incarnation=incarnation,
            native_session_incarnation=incarnation, attached_clients=7,
        ))
        listing = "" if absent else f"wt-one\t0\t$1:100\t{tmp_path}\n"
        return subprocess.CompletedProcess(argv, 0, listing, "")

    monkeypatch.setattr(mux_discovery, "run_mux_child", replacement_during_probe)
    mux_attached_clients.AttachedClientObserver().observe(registry)
    current = registry.get("proj", "wt-1")
    assert current["live"] and current["attached_clients"] == 7
    assert (current["mapping_revision"], current["session_incarnation"]) == (revision, incarnation)
    assert published == []


def test_observer_native_zero_clients_is_presence_not_absence(monkeypatch, tmp_path):
    registry, _, published = _native_observer_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(mux_discovery, "run_mux_child", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, f"wt-one\t0\t$1:100\t{tmp_path}\n", "",
    ))
    mux_attached_clients.AttachedClientObserver().observe(registry)
    current = registry.get("proj", "wt-1")
    assert current["live"] and current["attached_clients"] == 0
    assert published == []


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
