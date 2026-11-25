"""Successive native sweeps reach the Core status publication seam."""

from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest

from agent_worktrees import __main__ as monitor
from agent_worktrees import git_ops, mux_link, mux_status_link, status_bar_cli, tracking
from agent_worktrees import config as cfg, push_timeout
from agent_worktrees import status_monitor_presentation as presentation
from agent_worktrees import status_monitor_discovery as discovery, status_monitor_native as native
from agent_worktrees.observation_progress import NativeInventory, capture_inventory, progress_scope
from agent_worktrees.status_monitor_presentation import run_monitor_sweep
from agent_worktrees.status_monitor_roster_demand import managed_roster_projects

_T = TypeVar("_T")


class UnloadedRecord(tracking.WorktreeRecord):
    _loaded_record_subject: tuple[object, ...] | None = None


def set_inventory(
    monkeypatch: pytest.MonkeyPatch, collect: Callable[[str], NativeInventory | None],
) -> None:
    monkeypatch.setattr(monitor, "_monitor_list_sessions", capture_inventory(collect))


class Segments:
    value = "first"

    def observation_blocked(self, project: str, path: str | None = None) -> bool:
        return False

    def get_cached(self, path: str) -> str | None:
        return self.value

    def get(self, path: str) -> str:
        pytest.fail("projection requested computation")

def test_successive_native_sweeps_publish_full_payload_and_changed_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    segments = Segments()
    ctx_done: set[str] = set()
    published: dict[tuple[str, str], str] = {}
    options: dict[str, Any] = {
        "managed_mux_cache": cache, "segment_cache": segments,
        "published": published, "incarnations": {},
    }
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 1
    segments.value = "second"
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 1
    assert requests[0]["values"] == {
        "@aw_seg": "first", "@aw_ctx": "context",
    }
    assert requests[1]["values"] == {"@aw_seg": "second"}
    assert ctx_done == {"wt-one"}
    assert all(request["session_incarnation"] == "$7:100" for request in requests)


def test_unknown_native_observation_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: None)
    monkeypatch.setattr(
        native, "publish_session_status",
        lambda **k: pytest.fail("unknown native observation"),
    )
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=mux_link.ManagedMuxCache(), segment_cache=Segments(),
    ) == -1


def test_native_presentation_precedes_bulk_roster_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    requests: list[dict[str, Any]] = []
    before_collection: list[list[dict[str, Any]]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    def collect_roster() -> None:
        before_collection.append(list(requests))

    monkeypatch.setattr(native, "push_status", publish)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        picker_projects={"roster"}, roster_publisher=collect_roster,
        managed_mux_cache=cache, segment_cache=Segments(),
    ) == 1
    assert len(before_collection) == 1
    assert [request["mux_session"] for request in before_collection[0]] == ["wt-one"]


def test_mux_only_project_is_computed_by_the_scheduled_owner_without_external_demand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees.closure_descriptor import DESCRIPTOR_VERSION

    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: pytest.fail("projection computed Git"))

    class UnrenderedSegments(Segments):
        def get_cached(self, path: str) -> None:
            return None

    state: dict[str, Any] = {}
    computations: list[str] = []
    requests: list[dict[str, Any]] = []

    def collect_roster() -> None:
        computations.append(cfg.active_project() or "")
        state.update({
            "ok": True, "projection": {"quality": "current"}, "worktrees": [{
                "id": "one", "path": str(tmp_path), "state": "convo", "turn_count": 12,
                "closure": {
                    "version": DESCRIPTOR_VERSION, "label": "CONVO", "style": "convo",
                    "compact": "CONVO", "closure": {"final": False},
                    "action": {"disposition": "blocked"}, "claims": {"held": 0},
                    "follow_ups": {"open": 0},
                },
            }],
        })

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    context_done: set[str] = set()
    published: dict[tuple[str, str], str] = {}
    for _ in range(2):
        projects = managed_roster_projects(cache)
        assert projects == {"project"}
        assert run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", context_done,
            picker_projects=projects, roster_publisher=collect_roster,
            managed_mux_cache=cache, segment_cache=UnrenderedSegments(),
            state_snapshot=lambda project: state, published=published,
        ) == 1
    assert computations == ["project", "project"]
    assert "STATE(?)" in requests[0]["values"]["@aw_seg"]
    assert "CONVO 12\U0001f4ac" in requests[1]["values"]["@aw_seg"]
    assert managed_roster_projects(None) == set()


def test_native_projection_reads_computed_segment_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    monkeypatch.setattr(
        status_bar_cli, "_render_status_segment", lambda *a, **k: pytest.fail("Git renderer"),
    )
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: pytest.fail("Git classification"))
    requests: list[dict[str, Any]] = []

    class ComputedSegments(Segments):
        def get_cached(self, path: str) -> str:
            return "already-computed"

        def get(self, path: str) -> str:
            pytest.fail("projection requested computation")

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=cache, segment_cache=ComputedSegments(),
    ) == 1
    assert requests[0]["values"]["@aw_seg"] == "already-computed"


@pytest.mark.parametrize("content_state", ["dirty", "convo"])
def test_scheduled_producer_preserves_content_for_active_mux_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_state: str,
) -> None:
    from agent_worktrees import delegate_cli, reclaim, sessions, worktrees_producer
    from agent_worktrees.closure_descriptor import DESCRIPTOR_VERSION
    from agent_worktrees.mux_state_projection import render_projected_segment
    from agent_worktrees.worktrees_runtime import WorktreesRuntime

    record = UnloadedRecord(
        worktree_id="one", branch="worktree/one", worktree_path=str(tmp_path),
        repo="project", machine="host", platform="windows", title="content",
        started_at="", last_resumed_at="", resume_count=0, status="active",
        completed_at=None, sessions=[],
    )
    (tmp_path / ".git").mkdir()
    descriptor: dict[str, Any] = {
        "version": DESCRIPTOR_VERSION, "label": "ACTIVE", "style": "active",
        "compact": "ACTIVE", "closure": {"final": False},
        "action": {"disposition": "blocked"}, "claims": {"held": 0},
        "follow_ups": {"open": 0},
    }
    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor=str(tmp_path), remote="origin", default_branch="main"),
    ))
    monkeypatch.setattr(tracking, "list_records", lambda *a: [record])
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: True)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda *a: sessions.SessionContext())
    monkeypatch.setattr(sessions, "mux_status_many", lambda *a: {
        "one": sessions.MuxInfo(exists=True, clients=1),
    })
    monkeypatch.setattr(monitor, "_classify_records_live", lambda *a, **k: {
        "one": git_ops.WorktreeStateInfo(git_ops.WorktreeState.ACTIVE),
    })
    monkeypatch.setattr(monitor, "_worktree_to_dict", lambda *a, **k: {
        "id": "one", "title": "content", "state": "active", "closure": descriptor,
    })
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    monkeypatch.setattr(delegate_cli, "annotate_delegate_graph", lambda *a, **k: None)
    events: list[str] = []
    policy_checks: list[str] = []

    class DataOwner(Segments):
        depth = 0
        may_compute = True
        completed: str | None = None

        def run_observation(
            self, project: str, anchor: str | None, path: str | None,
            observe: Callable[[], _T],
        ) -> _T:
            assert self.depth == 0
            self.depth += 1
            try:
                return observe()
            finally:
                self.depth -= 1

        def get(self, path: str) -> str:
            assert self.may_compute and self.depth == 0
            events.append("content:" + content_state)
            self.completed = render_projected_segment({
                "state": content_state, "turn_count": 12, "ahead": 2,
                "closure": {
                    **descriptor, "label": content_state.upper(),
                    "compact": content_state.upper(), "style": content_state,
                },
            }, current=True)
            return self.completed

        def get_cached(self, path: str) -> str | None:
            return self.completed

        def pending_segment_for_path(self, path: str) -> str:
            return "pending"

        def retain_unclaimed_cleanup(
            self, project: str, path: str | None, error: push_timeout.ObservationCleanupError,
        ) -> None:
            pytest.fail("unexpected cleanup failure")

    owner = DataOwner()
    state: dict[str, Any] = {}
    runtime = WorktreesRuntime(tmp_path / "current.sqlite3")

    def publish_roster(project: str, rows: list[dict[str, Any]], **options: Any) -> None:
        events.append("roster")
        state.update({"ok": True, "projection": {"quality": "current"}, "worktrees": rows})

    monkeypatch.setattr(runtime, "publish", publish_roster)
    try:
        assert worktrees_producer.publish_active_project(
            runtime, cleanup_owner=owner, content_cache=owner,
            content_admitted=lambda: True, content_recheck=lambda: policy_checks.append("content"),
        ) == 1
        assert policy_checks == ["content"]
        assert events == ["roster", "content:" + content_state]
        assert state["worktrees"][0]["state"] == "active"
        owner.may_compute = False
        cache = mux_link.ManagedMuxCache()
        assert cache.apply_observation({
            "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
            "mux_session": "wt-one", "session_incarnation": "$7:100",
            "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })["applied"]
        monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
        set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
        monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
        monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
        requests: list[dict[str, Any]] = []

        def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
            requests.append(payload)
            return {"applied": True}

        monkeypatch.setattr(native, "push_status", publish)
        assert run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
            managed_mux_cache=cache, segment_cache=owner, state_snapshot=lambda project: state,
        ) == 1
        assert requests[0]["values"]["@aw_seg"] == owner.completed
        assert content_state.upper() in requests[0]["values"]["@aw_seg"]
        assert "ACTIVE" not in requests[0]["values"]["@aw_seg"]
        assert (
            "\u21912" if content_state == "dirty" else "12\U0001f4ac"
        ) in requests[0]["values"]["@aw_seg"]
        assert events == ["roster", "content:" + content_state]
    finally:
        runtime.shutdown()


def test_active_projection_keeps_completed_content_advisory_until_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import segment_render_observation, status_segment_cache
    from agent_worktrees.closure_descriptor import DESCRIPTOR_VERSION
    from agent_worktrees.mux_state_projection import render_projected_segment

    path = str(tmp_path)
    clock = [100.0]
    creation_nonce = ["first"]
    monkeypatch.setattr(status_segment_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: True)
    monkeypatch.setattr(cfg, "active_project", lambda: "project")
    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor=path),
    ))
    monkeypatch.setattr(monitor, "_find_record_for_path", lambda *a: SimpleNamespace(
        worktree_id="one", worktree_path=path, creation_nonce=creation_nonce[0],
    ))
    descriptor = {
        "version": DESCRIPTOR_VERSION, "label": "ACTIVE", "style": "active",
        "compact": "ACTIVE", "closure": {"final": False},
        "action": {"disposition": "blocked"}, "claims": {"held": 0},
        "follow_ups": {"open": 0},
    }
    content = ["dirty"]
    renders: list[str] = []

    def render(*args: Any, **kwargs: Any) -> str:
        renders.append(content[0])
        value = render_projected_segment({
            "state": content[0], "turn_count": 12, "closure": {
                **descriptor, "label": content[0].upper(),
                "compact": content[0].upper(), "style": content[0],
            },
        }, current=True)
        return segment_render_observation.record_render(
            segment_render_observation.render_subject(
                "project", path, "one", creation_nonce[0],
            ), value,
        )

    monkeypatch.setattr(monitor, "_render_status_segment", render)
    owner = status_segment_cache._StatusSegmentCache(ttl=60)
    owner.get(path)
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": path,
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: pytest.fail("projection Git"))
    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    for phase in ("fresh", "expired", "invalidated", "refreshed"):
        if phase == "expired":
            clock[0] += 61
            assert owner.get_cached(path) is None
        elif phase == "invalidated":
            owner.invalidate(path)
            assert owner.get_cached(path) is None
        elif phase == "refreshed":
            content[0] = "convo"
            owner.get(path)
        assert run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
            managed_mux_cache=cache, segment_cache=owner,
            state_snapshot=lambda project: {
                "ok": True, "projection": {"quality": "current"},
                "availability": {"status": "stale"},
                "worktrees": [{
                    "id": "one", "path": path, "state": "active", "closure": descriptor,
                    "creation_nonce": creation_nonce[0],
                }],
            },
        ) == 1
        segment = requests[-1]["values"]["@aw_seg"]
        assert "STATE(?)" not in segment and "ACTIVE" not in segment
        assert ("(stale)" in segment) is (phase in {"expired", "invalidated"})
        assert ("CONVO" if phase == "refreshed" else "DIRTY") in segment
    assert renders == ["dirty", "convo"]
    clock[0] += 61
    for rows in ([], [{
        "id": "one", "path": path, "state": "active",
        "closure": {**descriptor, "version": DESCRIPTOR_VERSION + 1},
    }]):
        assert run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
            managed_mux_cache=cache, segment_cache=owner,
            state_snapshot=lambda project: {
                "ok": True, "projection": {"quality": "current"}, "worktrees": rows,
            },
        ) == 1
        assert "STATE(?)" in requests[-1]["values"]["@aw_seg"]
    for fresh in (False, True):
        if fresh:
            owner.get(path)
        assert run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
            managed_mux_cache=cache, segment_cache=owner,
            state_snapshot=lambda project: {
                "ok": True, "projection": {"quality": "current"}, "worktrees": [{
                    "id": "one", "path": str(tmp_path / "moved"),
                    "state": "active", "closure": descriptor,
                }],
            },
        ) == 1
        segment = requests[-1]["values"]["@aw_seg"]
        assert "STATE(?)" in segment and "CONVO" not in segment
    creation_nonce[0] = "second"
    owner.invalidate(path)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=cache, segment_cache=owner,
        state_snapshot=lambda project: {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [{
                "id": "one", "path": path, "state": "active", "closure": descriptor,
                "creation_nonce": creation_nonce[0],
            }],
        },
    ) == 1
    segment = requests[-1]["values"]["@aw_seg"]
    assert "STATE(?)" in segment and "CONVO" not in segment


@pytest.mark.parametrize("replacement", ["worktree", "creation"])
def test_segment_cache_binds_advisory_to_the_record_actually_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    from dataclasses import replace
    from agent_worktrees import closure_descriptor, finalized_checkout, prune, sessions
    from agent_worktrees import status_segment_cache, tracking_session_registry

    path = str(tmp_path)
    first = UnloadedRecord(
        worktree_id="one", branch="worktree/one", worktree_path=path, repo="project",
        machine="fixture", platform="windows", started_at="", last_resumed_at="",
        resume_count=0, title="first", status="active", completed_at=None,
        creation_nonce="first",
    )
    second = replace(
        first, worktree_id="two" if replacement == "worktree" else "one",
        creation_nonce="second", title="second",
    )
    records = iter((first, second))
    lookups: list[str] = []

    def lookup(target: str) -> tracking.WorktreeRecord:
        lookups.append(target)
        return next(records)

    monkeypatch.setattr(monitor, "_find_record_for_path", lookup)
    monkeypatch.delattr(monitor, "_render_status_segment", raising=False)
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: False)
    monkeypatch.setattr(cfg, "active_project", lambda: "project")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace(
        default_repo=SimpleNamespace(remote="origin", default_branch="main", anchor=path),
    ))
    monkeypatch.setattr(status_bar_cli, "_detect_upstream_branch", lambda *a, **k: "main")
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k:
        git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.DIRTY),
    )
    monkeypatch.setattr(status_bar_cli, "_apply_tracking_override", lambda record, info: info)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda *a:
        SimpleNamespace(turn_count={}),
    )
    monkeypatch.setattr(status_bar_cli, "_sync_status_tag", lambda *a: "")
    monkeypatch.setattr(prune, "cleanup_disposition", lambda *a, **k: None)
    monkeypatch.setattr(tracking_session_registry, "is_repo_fetch_fresh", lambda *a: False)
    monkeypatch.setattr(prune, "cross_machine_claim_count", lambda *a: 0)
    monkeypatch.setattr(finalized_checkout, "matches", lambda *a: False)
    monkeypatch.setattr(closure_descriptor, "assemble_closure_descriptor", lambda *a, **k:
        SimpleNamespace(display=None, style="dirty", display_compact="DIRTY"),
    )
    monkeypatch.setattr(status_bar_cli, "_persist_segment_title", lambda *a: None)
    owner = status_segment_cache._StatusSegmentCache()
    value = owner.get(path)
    assert "second" in value and "DIRTY" in value
    assert len(lookups) == 2
    owner.invalidate(path)
    assert owner.get_advisory("project", path, "one", "first") is None
    assert owner.get_advisory(
        "project", path, second.worktree_id, "second",
    ) == value + " #[fg=colour244](stale)#[default]"


@pytest.mark.parametrize("second_present", [True, False])
@pytest.mark.parametrize("current", [True, False])
def test_native_projection_batches_inventory_and_computed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_present: bool, current: bool,
) -> None:
    from agent_worktrees import sessions
    from agent_worktrees.closure_descriptor import DESCRIPTOR_VERSION

    cache = mux_link.ManagedMuxCache()
    for index, name in enumerate(("one", "two"), start=7):
        assert cache.apply_observation({
            "project": "project", "worktree_id": name, "worktree_path": str(tmp_path / name),
            "mux_session": "wt-" + name, "session_incarnation": f"${index}:100",
            "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })["applied"]
    lists: list[bool] = []

    def inventory(*args: Any) -> dict[str, tuple[int, str]]:
        lists.append(True)
        return {
            "wt-one": (1, "$7:100"),
            **({"wt-two": (1, "$8:100")} if second_present else {}),
        }

    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, inventory)
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    monkeypatch.setattr(
        status_bar_cli, "_render_status_segment", lambda *a, **k: pytest.fail("Git renderer"),
    )
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: pytest.fail("Git classification"))
    monkeypatch.setattr(sessions, "has_mux_session", lambda *a, **k: pytest.fail("has-session"))
    monkeypatch.setattr(
        presentation, "observe_psmux", lambda *a, **k: pytest.fail("per-session probe"),
        raising=False,
    )

    class ComputedSegments(Segments):
        def get_cached(self, path: str) -> str | None:
            return "older-computed" if current else None

    reads: list[str] = []
    descriptor = {
        "version": DESCRIPTOR_VERSION, "label": "DIRTY", "style": "dirty",
        "compact": "DIRTY C2", "closure": {"final": False},
        "action": {"disposition": "blocked"}, "claims": {"held": 2},
        "follow_ups": {"open": 0},
    }

    def state_snapshot(project: str) -> dict[str, Any]:
        reads.append(project)
        return {
            "ok": True, "projection": {"quality": "current"},
            **({} if current else {"availability": {"status": "stale"}}),
            "worktrees": [{
                "id": name, "path": str(tmp_path / name), "title": name,
                "state": "dirty", "ahead": 2, "behind": 1, "closure": descriptor,
            } for name in ("one", "two")],
        }

    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=cache, segment_cache=ComputedSegments(),
        state_snapshot=state_snapshot,
    ) == (2 if second_present else 1)
    assert lists == [True]
    assert reads == ["project"]
    assert [request["mux_session"] for request in requests] == (
        ["wt-one", "wt-two"] if second_present else ["wt-one"]
    )
    for request in requests:
        segment = request["values"]["@aw_seg"]
        assert "DIRTY C2" in segment
        assert ("\u21912" in segment) is current
        assert ("DIRTY C2 ?" in segment) is (not current)


def test_missing_computed_state_remains_pending_without_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: pytest.fail("Git classification"))

    class UnrenderedSegments(Segments):
        def get_cached(self, path: str) -> None:
            return None

    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=cache, segment_cache=UnrenderedSegments(),
        state_snapshot=lambda project: {"ok": False, "error": {"code": "unready"}},
    ) == 1
    assert requests[0]["values"]["@aw_ctx"] == "context"
    assert "STATE(?)" in requests[0]["values"]["@aw_seg"]


def test_non_native_monitor_receives_state_only_segment_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: pytest.fail("Git classification"))
    monkeypatch.setattr(
        status_bar_cli, "_render_status_segment", lambda *a, **k: pytest.fail("Git renderer"),
    )

    def sweep(*args: Any, **options: Any) -> int:
        assert options["segment_cache"].get(str(tmp_path)) == "first"
        assert options["segment_cache"].owner is segments
        return 2

    segments = Segments()
    assert run_monitor_sweep(
        sweep, "tmux", "monitor", "fixture", set(), segment_cache=segments,
    ) == 2


@pytest.mark.parametrize("render_seconds", [0.0, 90.0])
def test_bulk_classification_yields_presentation_and_restores_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, render_seconds: float,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    monkeypatch.setattr(monitor, "_build_active_paths", lambda *a: set())
    clock = [0.0]
    progress = presentation.PresentationProgress(clock=lambda: clock[0])
    requests: list[dict[str, Any]] = []
    classified: list[str] = []
    records: list[tracking.WorktreeRecord] = [
        UnloadedRecord(
            worktree_id=name, branch="worktree/" + name, worktree_path=str(tmp_path / name),
            repo="roster", machine="host", platform="windows", title=name,
            started_at="", last_resumed_at="", resume_count=0, status="active",
            completed_at=None, sessions=[],
        )
        for name in ("first", "second")
    ]
    for record in records:
        Path(record.worktree_path).mkdir()
    info = git_ops.WorktreeStateInfo(git_ops.WorktreeState.WIP)

    class TimedSegments(Segments):
        def get_cached(self, path: str) -> str:
            clock[0] += render_seconds
            return self.value

    segments = TimedSegments()

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    def classify(path: str, *args: Any, **kwargs: Any) -> git_ops.WorktreeStateInfo:
        assert cfg.active_project() == "roster"
        count = len(requests)
        progress()
        assert len(requests) == count
        classified.append(Path(path).name)
        segments.value = "after-" + Path(path).name
        clock[0] += 31.0
        return info

    def collect_roster() -> None:
        segments.value = "during-roster"
        clock[0] += 31.0
        with progress_scope(progress):
            assert monitor._classify_records_live(
                records, repo=cfg.RepoConfig(
                    anchor=str(tmp_path), worktree_root=str(tmp_path), default_branch="main",
                ),
            ) == {record.worktree_id: info for record in records}

    monkeypatch.setattr(git_ops, "classify_worktree", classify)
    monkeypatch.setattr(native, "push_status", publish)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        progress=progress, picker_projects={"roster"}, roster_publisher=collect_roster,
        managed_mux_cache=cache, segment_cache=segments, published={},
    ) == 1
    assert classified == ["first", "second"]
    assert [request["values"]["@aw_seg"] for request in requests] == [
        "first", "during-roster", "after-first",
    ]
    assert cfg.active_project() == "roster"
    clock[0] += 1000.0
    progress()
    assert len(requests) == 3


@pytest.mark.parametrize("hold_phase", ["before-render", "during-render"])
def test_cooperative_presentation_does_not_wait_while_roster_holds_project_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_phase: str,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)

    class ProjectLock:
        depth = 0

        def __enter__(self) -> None:
            self.depth += 1

        def __exit__(self, *args: Any) -> None:
            self.depth -= 1

    project_lock = ProjectLock()
    waits: list[int] = []
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: waits.append(project_lock.depth))
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    clock = [0.0]
    held = [False]
    collecting = [False]
    progress = presentation.PresentationProgress(clock=lambda: clock[0])
    requests: list[dict[str, Any]] = []

    class PrioritySegments(Segments):
        def get_cached(self, path: str) -> str:
            if collecting[0] and hold_phase == "during-render":
                assert project_lock.depth == 2
                held[0] = True
            return "during" if collecting[0] else "first"

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    def collect_roster() -> None:
        assert project_lock.depth == 1
        collecting[0] = True
        held[0] = hold_phase == "before-render"
        clock[0] += 31.0
        progress()
        assert project_lock.depth == 1
        assert cfg.active_project() == "roster"

    monkeypatch.setattr(native, "push_status", publish)
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        progress=progress, picker_projects={"roster"}, roster_publisher=collect_roster,
        lifecycle_priority=SimpleNamespace(is_set=lambda: held[0]),
        project_lock=project_lock,
        managed_mux_cache=cache, segment_cache=PrioritySegments(), published={},
    ) == 1
    assert len(requests) == 1
    assert waits and set(waits) == {0}
    assert project_lock.depth == 0


@pytest.mark.parametrize("failure", ["native-unavailable", "governance-held"])
def test_cooperative_presentation_keeps_native_and_governance_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    collecting = [False]
    set_inventory(
        monkeypatch,
        lambda *a: None if collecting[0] and failure == "native-unavailable"
        else {"wt-one": (1, "$7:100")},
    )
    decision = {"status": "blocked", "reason": "held"}

    def recheck(*args: Any) -> None:
        if collecting[0] and failure == "governance-held":
            raise monitor._StatusMonitorGovernanceDeferred(decision)

    monkeypatch.setattr(monitor, "_status_monitor_recheck", recheck)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    clock = [0.0]
    progress = presentation.PresentationProgress(clock=lambda: clock[0])
    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    def collect_roster() -> None:
        collecting[0] = True
        clock[0] += 31.0
        progress()

    monkeypatch.setattr(native, "push_status", publish)
    options: dict[str, Any] = {
        "progress": progress, "picker_projects": {"roster"}, "roster_publisher": collect_roster,
        "managed_mux_cache": cache, "segment_cache": Segments(), "published": {},
    }
    if failure == "governance-held":
        with pytest.raises(monitor._StatusMonitorGovernanceDeferred) as held:
            run_monitor_sweep(
                monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(), **options,
            )
        assert held.value.result is decision
    else:
        assert run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(), **options,
        ) == 1
    assert len(requests) == 1
    clock[0] += 1000.0
    progress()
    assert len(requests) == 1


@pytest.mark.parametrize("native_present", [True, False])
@pytest.mark.parametrize("original_incarnation", ["legacy-launch", "$6:99"])
def test_legacy_mapping_uses_observed_native_discovery_without_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_present: bool,
    original_incarnation: str,
) -> None:
    cache = mux_link.ManagedMuxCache()
    original: dict[str, Any] = {
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": original_incarnation,
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    assert cache.apply_observation(original)["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(
        monkeypatch,
        lambda *a: {"wt-one": (1, "$7:100")} if native_present else None,
    )
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "new-context")
    from agent_remote_driver.mux_observation_types import Observation, Pane

    monkeypatch.setattr(native, "_last_session", None)
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: iter([(
        "project", SimpleNamespace(worktree_id="one", worktree_path=str(tmp_path)),
    )]))
    monkeypatch.setattr(native, "observe_psmux", lambda *a, **k: Observation(
        "present", incarnation="$7:100", clients=1,
        panes=(Pane("%1", False, True, str(tmp_path)),),
    ))
    requests: list[dict[str, Any]] = []

    def discover(payload: dict[str, Any]) -> dict[str, Any]:
        pytest.fail("native recovery must not depend on Manager")

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(mux_status_link, "push_discovery_via_daemon", discover)
    monkeypatch.setattr(native, "push_status", publish)
    ctx_done = {"wt-one"}
    published = {("wt-one", "@aw_ctx"): "new-context", ("wt-one", "@aw_seg"): "first"}
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done,
        managed_mux_cache=cache, segment_cache=Segments(),
        incarnations={"wt-one": original_incarnation}, published=published,
    ) == (1 if native_present else -1)
    if native_present:
        assert "mapping_revision" not in requests[0]
        assert requests[0]["session_incarnation"] == "$7:100"
        assert requests[0]["values"]["@aw_ctx"] == "new-context"
    else:
        assert requests == []
        retained = cache.get("project", "one")
        assert retained is not None and retained["session_incarnation"] == original_incarnation
        assert ctx_done == {"wt-one"}
        assert published == {("wt-one", "@aw_ctx"): "new-context", ("wt-one", "@aw_seg"): "first"}


@pytest.mark.parametrize("failure", ["context", "segment"])
def test_one_renderer_failure_preserves_other_sessions_and_later_sweeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    cache = mux_link.ManagedMuxCache()
    for index, name in enumerate(("one", "two"), start=7):
        assert cache.apply_observation({
            "project": "project", "worktree_id": name, "worktree_path": str(tmp_path / name),
            "mux_session": "wt-" + name, "session_incarnation": f"${index}:100",
            "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {
        "wt-one": (1, "$7:100"), "wt-two": (1, "$8:100"),
    })
    monkeypatch.setattr(monitor, "_monitor_mux_set", lambda *a: pytest.fail("native CLI"))
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    failed = True

    def context(path: str | None = None, plain: bool = False) -> str:
        if failed and path == str(tmp_path / "one") and failure == "context":
            raise RuntimeError("unavailable context")
        return "context"

    class RenderSegments(Segments):
        def get_cached(self, path: str) -> str:
            if failed and path == str(tmp_path / "one") and failure == "segment":
                raise RuntimeError("unavailable segment")
            return self.value

    monkeypatch.setattr(status_bar_cli, "_render_status_context", context)
    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    ctx_done: set[str] = set()
    options: dict[str, Any] = {
        "managed_mux_cache": cache, "segment_cache": RenderSegments(),
        "published": {}, "incarnations": {},
    }
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 2
    assert [request["mux_session"] for request in requests] == ["wt-two"]
    assert ctx_done == {"wt-two"}
    failed = False
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
    ) == 2
    assert [request["mux_session"] for request in requests] == ["wt-two", "wt-one"]
    assert ctx_done == {"wt-one", "wt-two"}


def test_governance_deferral_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})

    decision = {"status": "blocked", "reason": "held"}

    def defer(*args: Any) -> None:
        raise monitor._StatusMonitorGovernanceDeferred(decision)

    monkeypatch.setattr(monitor, "_status_monitor_recheck", defer)
    with pytest.raises(monitor._StatusMonitorGovernanceDeferred) as held:
        run_monitor_sweep(
            monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
            managed_mux_cache=cache, segment_cache=Segments(),
        )
    assert held.value.result is decision


def test_cleanup_failure_stays_with_its_existing_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = mux_link.ManagedMuxCache()
    assert cache.apply_observation({
        "project": "project", "worktree_id": "one", "worktree_path": str(tmp_path),
        "mux_session": "wt-one", "session_incarnation": "$7:100",
        "panes": [], "attached_clients": 1, "live": True, "mapping_revision": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })["applied"]
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "registry")
    set_inventory(monkeypatch, lambda *a: {"wt-one": (1, "$7:100")})
    monkeypatch.setattr(monitor, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(monitor, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "context")
    error = push_timeout.ObservationCleanupError("retained")
    monkeypatch.setattr(error, "try_settle", lambda: pytest.fail("not the cleanup owner"))
    retained: list[push_timeout.ObservationCleanupError] = []

    class PendingSegments(Segments):
        def get_cached(self, path: str) -> str:
            if not retained:
                raise error
            return "cleanup pending"

        def retain_unclaimed_cleanup(
            self, project: str, path: str, failure: push_timeout.ObservationCleanupError,
        ) -> None:
            retained.append(failure)

        def pending_segment_for_path(self, path: str) -> str:
            return "cleanup pending"

    requests: list[dict[str, Any]] = []

    def publish(payload: dict[str, Any], **kwargs: Any) -> dict[str, bool]:
        requests.append(payload)
        return {"applied": True}

    monkeypatch.setattr(native, "push_status", publish)
    segments = PendingSegments()
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=cache, segment_cache=segments,
    ) == 1
    assert retained == [error]
    assert requests == []
    assert run_monitor_sweep(
        monitor._monitor_sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=cache, segment_cache=segments,
    ) == 1
    assert requests[0]["values"]["@aw_seg"] == "cleanup pending"
