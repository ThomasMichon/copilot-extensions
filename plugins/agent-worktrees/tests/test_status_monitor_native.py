"""The elected monitor can publish through Core without a Manager."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_remote_driver.mux_observation_types import Observation, Pane
from agent_worktrees import git_ops, mux_link, mux_status_link, push_timeout, status_bar_cli, status_segment_cache
from agent_worktrees import status_monitor_discovery as discovery
from agent_worktrees import status_monitor_native as native
from agent_worktrees import status_monitor_presentation as presentation
from agent_worktrees.status_monitor_content import monitor_content_refresher, refresh_monitor_content
from agent_worktrees import __main__ as monitor
from agent_worktrees.observation_progress import capture_inventory


@pytest.mark.parametrize(
    "case", ["current", "unknown", "replaced", "foreign-pane", "dead-pane", "ambiguous", "foreign-name"],
)
def test_native_attribution_requires_unique_record_and_current_live_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    record = SimpleNamespace(worktree_id="one", worktree_path=str(tmp_path))
    records = [("project", record)] * (2 if case == "ambiguous" else 1)
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: iter(records))
    monkeypatch.setattr(native, "_last_session", None)
    observed = Observation(
        "unknown" if case == "unknown" else "present",
        incarnation="$8:200" if case == "replaced" else "$7:100",
        clients=1,
        panes=(Pane(
            "%1", case == "dead-pane", True,
            str(tmp_path / "elsewhere") if case == "foreign-pane" else str(tmp_path),
        ),),
    )
    monkeypatch.setattr(native, "observe_psmux", lambda *a, **k: observed)
    entries = native.discover_entries({
        "wt-foreign" if case == "foreign-name" else "wt-one": (1, "$7:100"),
    }, set())
    assert set(entries) == ({"wt-one"} if case == "current" else set())
    if entries:
        assert entries["wt-one"]["worktree_path"] == str(tmp_path)
        assert entries["wt-one"]["session_incarnation"] == "$7:100"


def test_native_discovery_rotates_a_bounded_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        ("project", SimpleNamespace(worktree_id=name, worktree_path=str(tmp_path)))
        for name in ("one", "two", "three")
    ]
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: iter(records))
    monkeypatch.setattr(native, "_last_session", None)
    probes: list[str] = []

    def observe(name: str, **kwargs: Any) -> Observation:
        probes.append(name)
        return Observation("unknown")

    monkeypatch.setattr(native, "observe_psmux", observe)
    live = {"wt-" + name: (1, "$7:100") for name in ("one", "two", "three")}
    assert native.discover_entries(live, set()) == {}
    assert len(probes) == 2
    assert native.discover_entries(live, set()) == {}
    assert len(probes) == 4 and len(set(probes)) == 3


def test_current_native_mappings_do_not_rescan_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery, "_tracked_worktrees", lambda: pytest.fail("unnecessary record scan"),
    )
    assert native.discover_entries({"wt-one": (1, "$7:100")}, {"wt-one"}) == {}


def test_unavailable_tracking_does_not_authorize_native_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> Any:
        raise OSError("unavailable")

    monkeypatch.setattr(discovery, "_tracked_worktrees", unavailable)
    monkeypatch.setattr(
        native, "observe_psmux", lambda *a, **k: pytest.fail("unattributed native read"),
    )
    assert native.discover_entries({"wt-one": (1, "$7:100")}, set()) == {}


@pytest.mark.parametrize("failure", [PermissionError, OSError], ids=["permission", "io"])
def test_unavailable_directory_preserves_other_native_candidates(
    monkeypatch: pytest.MonkeyPatch, failure: type[OSError],
) -> None:
    records = [
        ("project", SimpleNamespace(worktree_id=name, worktree_path="/synthetic/" + name))
        for name in ("bad", "good")
    ]
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: iter(records))
    monkeypatch.setattr(native, "_last_session", None)

    def available(path: str) -> bool:
        if path.endswith("/bad"):
            raise failure("unavailable")
        return True

    monkeypatch.setattr(native, "Path", lambda path: SimpleNamespace(is_dir=lambda: available(path)))
    monkeypatch.setattr(native, "observe_psmux", lambda name: Observation(
        "present", incarnation="$7:100", clients=1,
        panes=(Pane("%1", False, True, "/synthetic/good"),),
    ))
    assert set(native.discover_entries({
        "wt-bad": (1, "$7:100"), "wt-good": (1, "$7:100"),
    }, set())) == {"wt-good"}


def test_original_sweep_publishes_completed_content_before_attribution_rotates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    records = []
    for index in range(6):
        path = tmp_path / str(index)
        path.mkdir()
        records.append(("project", SimpleNamespace(worktree_id=str(index), worktree_path=str(path))))
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: iter(records))
    monkeypatch.setattr(native, "_last_session", None)
    probes: list[str] = []

    def observe(name: str) -> Observation:
        probes.append(name)
        index = int(name.removeprefix("wt-"))
        return Observation(
            "present", incarnation="$7:100", clients=1,
            panes=(Pane("%1", False, True, records[index][1].worktree_path),),
        )

    monkeypatch.setattr(native, "observe_psmux", observe)
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    live = {"wt-" + record.worktree_id: (1, "$7:100") for _, record in records}
    monkeypatch.setattr(monitor, "_monitor_list_sessions", capture_inventory(lambda *a: live))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(mux_status_link, "push_discovery_via_daemon", lambda *a, **k: pytest.fail("Manager"))
    monkeypatch.setattr(mux_status_link, "push_status_via_daemon", lambda *a, **k: pytest.fail("Manager"))
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    content = ["DIRTY"]
    monkeypatch.setattr(monitor, "_render_status_segment", lambda *a, **k: content[0])
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: False)
    monkeypatch.setattr(status_segment_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    owner = status_segment_cache._StatusSegmentCache(ttl=60)
    latest: dict[str, str] = {}

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        if "@aw_seg" in values:
            latest[session] = values["@aw_seg"]
        return True

    monkeypatch.setattr(native, "apply_psmux_status", apply)
    ctx_done: set[str] = set()
    published: dict[tuple[str, str], str] = {}
    cache = mux_link.ManagedMuxCache()
    for iteration in range(6):
        if iteration == 3:
            content[0] = "CONVO"
        paths: dict[str, str | None] = {}
        assert presentation.run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done,
            managed_mux_cache=cache, segment_cache=owner, content_paths=paths, published=published,
            refresh_content=monitor_content_refresher(paths, owner, lambda *a: None, RuntimeError),
        ) == 6
        assert len(probes) == 2 * (iteration + 1)
        assert all(latest[name] == content[0] for name in probes[-2:])
        if iteration in (2, 5):
            assert latest == {name: content[0] for name in live}
        clock[0] += 30.0


@pytest.mark.parametrize("applied", [True, False])
def test_native_publication_uses_core_and_commits_only_confirmed_values(
    monkeypatch: pytest.MonkeyPatch, applied: bool,
) -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        calls.append((session, incarnation, values))
        return applied

    monkeypatch.setattr(native, "apply_psmux_status", apply)
    monkeypatch.setattr(
        mux_status_link, "push_status_via_daemon",
        lambda *a, **k: pytest.fail("Manager status dependency"),
    )
    published: dict[tuple[str, str], str] = {}
    checks: list[bool] = []
    result = native.publish_session_status(
        entry={"mux_session": "wt-one", "session_incarnation": "$7:100"},
        values={"@aw_ctx": "context", "@aw_seg": "dirty"}, published=published,
        before_publish=lambda: checks.append(True),
    )
    assert result["context_published"] is applied
    assert checks == [True]
    assert calls[0][:2] == ("wt-one", "$7:100")
    assert calls[0][2]["status-left"] == "#{@aw_ctx} "
    assert calls[0][2]["status-right"] == "#{@aw_seg} %H:%M "
    assert not ({"@aw_updater", "@aw_updater_prefix"} & calls[0][2].keys())
    assert published == ({
        ("wt-one", "@aw_ctx"): "context", ("wt-one", "@aw_seg"): "dirty",
    } if applied else {})


def test_original_sweep_publishes_registered_worktree_with_manager_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = SimpleNamespace(worktree_id="one", worktree_path=str(tmp_path))
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: iter([("project", record)]))
    monkeypatch.setattr(native, "_last_session", None)
    monkeypatch.setattr(native, "observe_psmux", lambda *a, **k: Observation(
        "present", incarnation="$7:100", clients=1,
        panes=(Pane("%1", False, True, str(tmp_path)),),
    ))
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    monkeypatch.setattr(
        monitor, "_monitor_list_sessions",
        capture_inventory(lambda *a: {"wt-one": (1, "$7:100")}),
    )
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(
        mux_status_link, "push_discovery_via_daemon",
        lambda *a, **k: pytest.fail("Manager discovery dependency"),
    )
    monkeypatch.setattr(
        mux_status_link, "push_status_via_daemon",
        lambda *a, **k: pytest.fail("Manager publication dependency"),
    )
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    calls: list[dict[str, str]] = []

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        assert (session, incarnation) == ("wt-one", "$7:100")
        calls.append(values)
        return True

    monkeypatch.setattr(native, "apply_psmux_status", apply)
    class ContentOwner:
        value: str | None = None

        def get_cached(self, path: str) -> str | None:
            return self.value

        def get(self, path: str) -> str:
            self.value = "computed dirty status"
            return self.value

        def observation_blocked(self, project: str, path: str | None = None) -> bool:
            return False

        def pending_segment_for_path(self, path: str) -> str:
            pytest.fail("unexpected pending observation")

        def retain_unclaimed_cleanup(
            self, project: str, path: str | None, error: push_timeout.ObservationCleanupError,
        ) -> None:
            pytest.fail("unexpected observation cleanup")

    owner = ContentOwner()
    ctx_done: set[str] = set()
    content_paths: dict[str, str | None] = {}
    options: dict[str, Any] = {
        "managed_mux_cache": mux_link.ManagedMuxCache(), "published": {}, "incarnations": {},
        "content_paths": content_paths, "segment_cache": owner,
    }
    assert presentation.run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done,
        **options,
        state_snapshot=lambda project: {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [],
        },
    ) == 1
    assert calls[0]["@aw_ctx"] == "context"
    assert "@aw_seg" in calls[0]
    assert ctx_done == {"wt-one"}
    assert content_paths == {str(tmp_path): "project"}
    refresh_monitor_content(
        content_paths, owner, recheck=lambda *a: None,
        governance_deferred=monitor._StatusMonitorGovernanceDeferred,
    )
    assert presentation.run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 1
    assert calls[-1] == {"@aw_seg": "computed dirty status"}
