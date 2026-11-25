"""Actor-free projection ordering; native, storage and Git owners are boundaries."""

from __future__ import annotations

from contextlib import nullcontext
from collections.abc import Callable
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    name = "promotion_mux_projection"
    package = ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)

    def stub(child: str, **fields: Any) -> ModuleType:
        module = ModuleType(f"{name}.{child}")
        module.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        setattr(package, child, module)
        return module

    def load(child: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(f"{name}.{child}", SOURCE / f"{child}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        setattr(package, child, module)
        spec.loader.exec_module(module)
        return module

    driver = ModuleType("agent_remote_driver")
    driver.__path__ = []
    mux_creation = ModuleType("agent_remote_driver.mux_creation")
    mux_creation.__dict__["is_psmux"] = lambda value: value == "psmux.exe"
    monkeypatch.setitem(sys.modules, driver.__name__, driver)
    monkeypatch.setitem(sys.modules, mux_creation.__name__, mux_creation)
    for child, field in (("mux_observation", "observe_psmux"), ("mux_status", "apply_psmux_status")):
        module = ModuleType("agent_remote_driver." + child)
        setattr(module, field, lambda *a, **k: pytest.fail("unselected native boundary"))
        monkeypatch.setitem(sys.modules, module.__name__, module)
    current = ["project"]
    stub("config", active_project=lambda: current[0], set_active_project=lambda value: current.__setitem__(0, value))
    stub("config_cache", project_scope=lambda *args: nullcontext())
    stub("push_timeout", ObservationCleanupError=type("CleanupError", (Exception,), {}),
         ObservationCleanupPending=type("CleanupPending", (Exception,), {}))
    stub("status_bar_cli", _render_status_context=lambda *a, **k: "context",
         _find_record_for_path=lambda path: None,
         _render_status_segment=lambda path, **options: "cached:" + path,
         _DESCRIPTOR_STYLE_BG={"dirty": "colour001"}, _SEGMENT_TITLE_MAX=48)
    stub("git_ops", _windows_observation=lambda: False)
    stub("closure_descriptor", interpret_descriptor_payload=lambda payload: payload or {
        "supported": False, "style": "unknown", "compact": "STATE(?)",
    })
    stub("tracking", find_worktree_id_by_cwd=lambda *args: "one")
    stub("sessions", mux_session_name=lambda value: "wt-" + value)
    stub("status_monitor_discovery", _tracked_worktrees=lambda: iter(()),
         _valid_native_incarnation=lambda value: value == "$7:100")
    entry = {
        "project": "project", "worktree_id": "one", "worktree_path": "/synthetic/one",
        "mux_session": "wt-one", "session_incarnation": "$7:100",
    }
    stub("status_monitor_runtime", _monitor_managed_session_union=lambda *a: (
        {"wt-one": entry, "wt-absent": {**entry, "mux_session": "wt-absent"}}, set(), [],
    ))
    stub("mux_status_link")
    stub("__main__", _status_monitor_recheck=lambda *a: None,
         _self_override=lambda name, default: default,
         _StatusMonitorGovernanceDeferred=type("GovernanceDeferred", (Exception,), {}))
    scope = load("observation_progress")
    load("monitor_projection_cache")
    content = load("status_monitor_content")
    load("segment_render_observation")
    cache = load("status_segment_cache")
    formatter = load("mux_state_projection")
    projects = load("status_monitor_roster_demand")
    native = load("status_monitor_native")
    presentation = load("status_monitor_presentation")
    return SimpleNamespace(
        scope=scope, presentation=presentation, formatter=formatter, package=package,
        projects=projects,
        content=content,
        cache=cache,
        native=native,
    )


class CompletedSegments:
    def observation_blocked(self, project: str, path: str | None = None) -> bool:
        return False

    def get_cached(self, path: str) -> str | None:
        return "completed"

    def get(self, path: str) -> str:
        pytest.fail("projection entered the compute-on-miss getter")


@pytest.mark.parametrize("target", ["current", "unknown", "replaced", "foreign-path", "ambiguous"])
def test_native_attribution_uses_record_and_observation_boundaries(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    record = SimpleNamespace(worktree_id="one", worktree_path="/synthetic/one")
    records = [("project", record)] * (2 if target == "ambiguous" else 1)
    api.package.status_monitor_discovery._tracked_worktrees = lambda: iter(records)
    monkeypatch.setattr(api.native, "Path", lambda path: SimpleNamespace(is_dir=lambda: True))
    monkeypatch.setattr(api.native, "observe_psmux", lambda name: SimpleNamespace(
        status="unknown" if target == "unknown" else "present",
        incarnation="$8:200" if target == "replaced" else "$7:100",
        panes=[SimpleNamespace(
            dead=False, path="/synthetic/foreign" if target == "foreign-path" else "/synthetic/one",
        )],
    ))
    entries = api.native.discover_entries({"wt-one": (1, "$7:100")}, set())
    assert set(entries) == ({"wt-one"} if target == "current" else set())
    if entries:
        assert entries["wt-one"]["worktree_path"] == "/synthetic/one"
        assert entries["wt-one"]["session_incarnation"] == "$7:100"


@pytest.mark.parametrize("applied", [True, False])
def test_native_publisher_commits_only_confirmed_presentation(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, applied: bool,
) -> None:
    calls: list[dict[str, str]] = []
    checks: list[str] = []

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        assert (session, incarnation) == ("wt-one", "$7:100")
        calls.append(values)
        return applied

    monkeypatch.setattr(api.native, "apply_psmux_status", apply)
    published: dict[tuple[str, str], str] = {}
    options = {
        "entry": {"mux_session": "wt-one", "session_incarnation": "$7:100"},
        "values": {"@aw_ctx": "context", "@aw_seg": "computed", "@aw_updater": "not-an-owner"},
        "published": published, "before_publish": lambda: checks.append("policy"),
    }
    result = api.native.publish_session_status(**options)
    assert result == {"applied": applied, "context_published": applied}
    assert len(calls) == 1 and checks == ["policy"]
    assert calls[0]["status-left"] == "#{@aw_ctx} " and "@aw_updater" not in calls[0]
    assert published == ({
        ("wt-one", "@aw_ctx"): "context", ("wt-one", "@aw_seg"): "computed",
    } if applied else {})
    if applied:
        assert api.native.publish_session_status(**options) == {
            "applied": True, "context_published": False,
        }
        assert len(calls) == 1 and checks == ["policy"]


def test_native_recovery_retains_the_last_accepted_incarnation(
    api: SimpleNamespace,
) -> None:
    entry = {
        "project": "project", "worktree_id": "one", "worktree_path": "/synthetic/one",
        "mux_session": "wt-one", "session_incarnation": "$7:100",
    }
    api.package.status_monitor_runtime._monitor_managed_session_union = lambda *a: (
        {"wt-one": {**entry, "session_incarnation": "legacy-launch"}}, set(), [],
    )
    api.package.status_monitor_runtime._monitor_update_session_incarnations = (
        lambda incarnations, *a: incarnations.update({"wt-one": "$7:100"})
    )
    api.native.discover_entries = lambda *a: {"wt-one": entry}
    calls: list[dict[str, str]] = []

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        assert (session, incarnation) == ("wt-one", "$7:100")
        calls.append(values)
        return True

    api.native.apply_psmux_status = apply
    collect = api.scope.capture_inventory(lambda mux: {"wt-one": (1, "$7:100")})

    def sweep(*args: Any, **options: Any) -> int:
        assert collect(args[0]) is not None
        options["catalog_observer"]({"wt-one"})
        return 1

    ctx_done: set[str] = set()
    options: dict[str, Any] = {
        "incarnations": {}, "published": {}, "segment_cache": CompletedSegments(),
    }
    for _ in range(2):
        assert api.presentation.run_monitor_sweep(
            sweep, "psmux.exe", "monitor", "fixture", ctx_done, **options,
        ) == 1
    assert len(calls) == 1 and ctx_done == {"wt-one"}


def test_native_projection_precedes_roster_and_reuses_completed_inventory(api: SimpleNamespace) -> None:
    events: list[str] = []
    inventories: list[str] = []
    snapshots: list[str] = []

    def collect(mux_bin: str) -> dict[str, tuple[int, str]]:
        inventories.append(mux_bin)
        return {"wt-one": (2, "$7:100")}

    observed_collect: Callable[[str], dict[str, tuple[int, str]] | None] = api.scope.capture_inventory(collect)

    def publish(**options: Any) -> dict[str, bool]:
        assert options["values"]["@aw_seg"] == "completed"
        events.append("write:" + options["entry"]["mux_session"])
        return {"context_published": True}

    api.native.publish_session_status = publish
    content_values: list[str] = []

    class ProducingSegments(CompletedSegments):
        may_compute = True
        completed: str | None = None

        def get(self, path: str) -> str:
            assert self.may_compute
            content_values.append(path)
            self.completed = "completed"
            return self.completed

        def get_cached(self, path: str) -> str | None:
            return self.completed

    segments = ProducingSegments()
    api.content.refresh_content_segments(
        "project", {"/synthetic/one"}, segments, governance_deferred=RuntimeError,
    )
    segments.may_compute = False

    def sweep(*args: Any, **options: Any) -> int:
        assert options["picker_projects"] == {"project"}
        live = observed_collect(args[0])
        assert live is not None
        options["catalog_observer"](set(live))
        events.append("roster")
        return len(live)

    def snapshot(project: str) -> dict[str, Any]:
        snapshots.append(project)
        return {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [{
                "id": "one", "path": "/synthetic/one", "state": "active",
                "closure": {"supported": True, "style": "active", "compact": "ACTIVE"},
            }],
        }

    assert api.presentation.run_monitor_sweep(
        sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=object(), segment_cache=segments,
        catalog_observer=lambda names: events.append("catalog"),
        state_snapshot=snapshot,
        picker_projects=api.projects.scheduled_roster_projects(set(), set(), set(), object()),
    ) == 1
    assert events == ["catalog", "write:wt-one", "roster"]
    assert inventories == ["psmux.exe"] and snapshots == ["project"]
    assert content_values == ["/synthetic/one"]


def test_tmux_uses_read_only_view_of_the_original_owner(api: SimpleNamespace) -> None:
    segments = CompletedSegments()
    registered: dict[str, str | None] = {}
    api.package.config.set_active_project(None)

    def sweep(*args: Any, **options: Any) -> int:
        view = options["segment_cache"]
        assert view.owner is segments
        assert view.get("/synthetic/one") == "completed"
        assert view.get_cached("/synthetic/one") == "completed"
        return 1

    assert api.presentation.run_monitor_sweep(
        sweep, "tmux", "monitor", "fixture", set(), segment_cache=segments,
        content_paths=registered,
    ) == 1
    assert registered == {"/synthetic/one": None}
    api.package.config.set_active_project("project")
    path_reads: list[str] = []
    snapshots: list[str] = []

    class Row(dict[str, Any]):
        def get(self, key: str, default: Any = None) -> Any:
            if key == "path":
                path_reads.append(self["id"])
            return super().get(key, default)

    class Unrendered(CompletedSegments):
        def get_cached(self, path: str) -> None:
            return None

    def snapshot(project: str) -> dict[str, Any]:
        snapshots.append(project)
        return {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [
                Row(id="one", path="/synthetic/one", title="unique"),
                Row(id="two", path="/synthetic/two", title="ambiguous"),
                Row(id="alias", path="/synthetic/two/", title="alias"),
            ],
        }

    def indexed_sweep(*args: Any, **options: Any) -> int:
        view = options["segment_cache"]
        assert view.get("/synthetic/one").startswith("unique ")
        assert view.get("/synthetic/one/").startswith("unique ")
        assert view.get("/synthetic/./one").startswith("unique ")
        assert not view.get("/synthetic/two").startswith(("ambiguous ", "alias "))
        assert not view.get("/synthetic/missing").startswith("unique ")
        assert view.get("/synthetic/one").startswith("unique ")
        return 1

    assert api.presentation.run_monitor_sweep(
        indexed_sweep, "tmux", "monitor", "fixture", set(),
        segment_cache=Unrendered(), state_snapshot=snapshot,
    ) == 1
    assert snapshots == ["project"] and path_reads == ["one", "two", "alias"]
    real_cache = api.cache._StatusSegmentCache()
    warm_paths = ["/warm/one", "/warm/two", "/warm/three"]
    for path in warm_paths:
        assert real_cache.get(path) == "cached:" + path
    real_cache.project_for_observation_path = lambda path: pytest.fail("warm cache path scan")

    def cached_sweep(*args: Any, **options: Any) -> int:
        for path in warm_paths:
            assert options["segment_cache"].get(path) == "cached:" + path
        return len(warm_paths)

    assert api.presentation.run_monitor_sweep(
        cached_sweep, "tmux", "monitor", "fixture", set(), segment_cache=real_cache,
        state_snapshot=lambda project: {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [
                {"id": path, "path": path, "state": "active"} for path in warm_paths
            ],
        },
    ) == len(warm_paths)


def test_active_projection_retains_subject_bound_advisory_content(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(api.cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    path = "/synthetic/one"
    api.package.status_bar_cli._find_record_for_path = lambda value: SimpleNamespace(
        worktree_path=path, worktree_id="one",
    )
    content = ["DIRTY"]
    renders: list[str] = []

    def render(*args: Any, **kwargs: Any) -> str:
        renders.append(content[0])
        record = api.package.status_bar_cli._find_record_for_path(path)
        observation = api.package.segment_render_observation
        rendered = observation.record_render(observation.render_subject(
            "project", record.worktree_path, getattr(record, "worktree_id", None),
            getattr(record, "creation_nonce", ""),
        ), content[0])
        assert isinstance(rendered, str)
        return rendered

    api.package.status_bar_cli._render_status_segment = render
    owner = api.cache._StatusSegmentCache(ttl=60)
    assert owner.get(path) == "DIRTY"
    collect = api.scope.capture_inventory(lambda mux: {"wt-one": (1, "$7:100")})
    writes: list[str] = []

    def publish(**options: Any) -> dict[str, bool]:
        writes.append(options["values"]["@aw_seg"])
        return {"context_published": True}

    api.native.publish_session_status = publish

    def sweep(*args: Any, **options: Any) -> int:
        assert collect(args[0]) is not None
        options["catalog_observer"]({"wt-one"})
        return 1

    for phase in ("fresh", "expired", "invalidated", "refreshed"):
        if phase == "expired":
            clock[0] += 61
            assert owner.get_cached(path) is None
        elif phase == "invalidated":
            owner.invalidate(path)
            assert owner.get_cached(path) is None
        elif phase == "refreshed":
            content[0] = "CONVO"
            owner.get(path)
        assert api.presentation.run_monitor_sweep(
            sweep, "psmux.exe", "monitor", "fixture", set(),
            managed_mux_cache=object(), segment_cache=owner,
            state_snapshot=lambda project: {
                "ok": True, "projection": {"quality": "current"},
                "availability": {"status": "stale"}, "worktrees": [{
                    "id": "one", "path": path, "state": "active",
                    "closure": {"supported": True, "style": "active", "compact": "ACTIVE"},
                }],
            },
        ) == 1
        assert "STATE(?)" not in writes[-1] and "ACTIVE" not in writes[-1]
        assert ("(stale)" in writes[-1]) is (phase in {"expired", "invalidated"})
        assert ("CONVO" if phase == "refreshed" else "DIRTY") in writes[-1]
    assert renders == ["DIRTY", "CONVO"]
    assert owner.get_advisory("other", path, "one") is None
    assert owner.get_advisory("project", path, "other") is None
    assert owner.get_advisory("project", "/synthetic/other", "one") is None
    assert api.presentation.run_monitor_sweep(
        sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=object(), segment_cache=owner,
        state_snapshot=lambda project: {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [{
                "id": "one", "path": "/synthetic/moved", "state": "active",
                "closure": {"supported": True, "style": "active", "compact": "ACTIVE"},
            }],
        },
    ) == 1
    assert "STATE(?)" in writes[-1] and "CONVO" not in writes[-1]
    api.package.status_bar_cli._find_record_for_path = lambda value: SimpleNamespace(
        worktree_path=path, worktree_id="one", creation_nonce="first",
    )
    owner.invalidate(path)
    assert owner.get(path) == "CONVO"
    owner.invalidate(path)
    assert api.presentation.run_monitor_sweep(
        sweep, "psmux.exe", "monitor", "fixture", set(),
        managed_mux_cache=object(), segment_cache=owner,
        state_snapshot=lambda project: {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [{
                "id": "one", "path": path, "state": "active", "creation_nonce": "second",
                "closure": {"supported": True, "style": "active", "compact": "ACTIVE"},
            }],
        },
    ) == 1
    assert "STATE(?)" in writes[-1] and "CONVO" not in writes[-1]
    api.package.status_bar_cli._find_record_for_path = lambda value: SimpleNamespace(
        worktree_path=path,
    )
    owner.invalidate(path)
    assert owner.get(path) == "CONVO"
    assert owner.get_advisory("project", path, "one") is None
    api.package.status_bar_cli._find_record_for_path = lambda value: SimpleNamespace(
        worktree_path=path, worktree_id="one",
    )
    owner.invalidate(path)
    assert owner.get(path) == "CONVO"
    ticket = owner.begin_observation("project", "/synthetic", path)
    assert "(stale)" in owner.get_advisory("project", path, "one")
    owner.retain_cleanup(ticket, api.package.push_timeout.ObservationCleanupError("held"))
    assert owner.get_advisory("project", path, "one") is None
    owner.close_observation_admission()
    assert owner.get_advisory("project", path, "one") is None


@pytest.mark.parametrize("evidence", ["missing", "value", "path", "project", "multiple"])
def test_segment_advisory_requires_one_matching_renderer_result(
    api: SimpleNamespace, evidence: str,
) -> None:
    observation = api.package.segment_render_observation
    path = "/synthetic/one"
    api.package.status_bar_cli._find_record_for_path = lambda value: SimpleNamespace(
        worktree_path=path, worktree_id="old", creation_nonce="old",
    )

    def render(*args: Any, **kwargs: Any) -> str:
        if evidence != "missing":
            subject = observation.render_subject(
                "other" if evidence == "project" else "project",
                "/synthetic/other" if evidence == "path" else path, "new", "new",
            )
            observation.record_render(subject, "different" if evidence == "value" else "DIRTY")
            if evidence == "multiple":
                observation.record_render(subject, "DIRTY")
        return "DIRTY"

    api.package.status_bar_cli._render_status_segment = render
    owner = api.cache._StatusSegmentCache()
    assert owner.get(path) == "DIRTY"
    assert owner.get_cached(path) == "DIRTY"
    owner.invalidate(path)
    assert owner.get_advisory("project", path, "old", "old") is None
    assert owner.get_advisory("project", path, "new", "new") is None


def test_render_capture_restores_outer_subject_after_failure(api: SimpleNamespace) -> None:
    observation = api.package.segment_render_observation
    subject = observation.render_subject("project", "/synthetic/one", "one", "")
    with observation.capture_render() as outer:
        with pytest.raises(RuntimeError, match="failed"):
            with observation.capture_render() as inner:
                observation.record_render(None, "unknown")
                raise RuntimeError("failed")
        assert inner.completed is None
        assert observation.record_render(subject, "DIRTY") == "DIRTY"
        assert outer.completed.subject == subject
    assert observation.record_render(subject, "unscoped") == "unscoped"
    assert outer.completed.value == "DIRTY"
    for fields in (("", "/path", "one", ""), ("project", None, "one", ""),
                   ("project", "/path", None, ""), ("project", "/path", "one", None)):
        assert observation.render_subject(*fields) is None


def test_content_producer_retains_cleanup_and_propagates_governance(api: SimpleNamespace) -> None:
    attempted: list[str] = []
    retained: list[tuple[str, str | None, Any]] = []
    error = api.package.push_timeout.ObservationCleanupError("held")
    error.try_settle = lambda: pytest.fail("not the cleanup owner")
    governance = api.package.__main__._StatusMonitorGovernanceDeferred

    class ProducingSegments(CompletedSegments):
        def get(self, path: str) -> str:
            attempted.append(path)
            if path == "cleanup":
                raise error
            if path == "pending":
                raise api.package.push_timeout.ObservationCleanupPending("pending")
            if path == "governance":
                raise governance("held")
            return "completed"

        def retain_unclaimed_cleanup(
            self, project: str, path: str | None, failure: Any,
        ) -> None:
            retained.append((project, path, failure))

    owner = ProducingSegments()
    api.content.refresh_content_segments(
        "project", {"cleanup", "pending", "ready"}, owner, governance_deferred=governance,
    )
    assert attempted == ["cleanup", "pending", "ready"]
    assert retained == [("project", "cleanup", error)]
    api.content.refresh_monitor_content(
        {"untracked": None}, owner, lifecycle_priority=SimpleNamespace(is_set=lambda: True),
        recheck=lambda *a: None, governance_deferred=governance,
    )
    assert attempted == ["cleanup", "pending", "ready"]
    with pytest.raises(governance):
        api.content.refresh_monitor_content(
            {"governance": None, "later": None}, owner,
            recheck=lambda *a: None, governance_deferred=governance,
        )
    assert attempted == ["cleanup", "pending", "ready", "governance"]
    assert api.package.config.active_project() == "project"
    held = [False]
    policy_attempts: list[str] = []
    policy_checks: list[str] = []

    class ChangingPolicy(CompletedSegments):
        def get(self, path: str) -> str:
            policy_attempts.append(path)
            held[0] = True
            return "completed"

    api.content.refresh_content_segments(
        "project", {"one", "two"}, ChangingPolicy(), governance_deferred=governance,
        admitted=lambda: not held[0], before_observation=lambda: policy_checks.append("checked"),
    )
    assert policy_attempts == ["one"] and policy_checks == ["checked"]
    held[0] = False
    policy_attempts.clear()
    policy_checks.clear()

    def recheck() -> None:
        policy_checks.append("checked")
        if held[0]:
            raise governance("held")

    with pytest.raises(governance):
        api.content.refresh_content_segments(
            "project", {"one", "two"}, ChangingPolicy(), governance_deferred=governance,
            before_observation=recheck,
        )
    assert policy_attempts == ["one"] and policy_checks == ["checked", "checked"]


def test_cooperative_progress_uses_completion_based_cadence_and_no_reentry(api: SimpleNamespace) -> None:
    clock = [0.0]
    events: list[float] = []
    progress = api.presentation.PresentationProgress(clock=lambda: clock[0])

    def present() -> None:
        events.append(clock[0])
        progress()
        clock[0] += 90.0

    with progress.bind(present):
        progress()
        clock[0] += 29.0
        progress()
        clock[0] += 1.0
        progress()
    clock[0] += 1000.0
    progress()
    assert events == [0.0, 120.0]


def test_observation_scopes_restore_outer_owners_after_failure(api: SimpleNamespace) -> None:
    events: list[str] = []
    values = [{"wt-one": (1, "$7:100")}, None]

    def collect(mux_bin: str) -> dict[str, tuple[int, str]] | None:
        return values.pop(0)

    observed_collect: Callable[[str], dict[str, tuple[int, str]] | None] = api.scope.capture_inventory(collect)

    with api.scope.inventory_scope() as inventory, api.scope.progress_scope(lambda: events.append("outer")):
        observed_collect("psmux.exe")
        with pytest.raises(RuntimeError, match="failed"):
            with api.scope.inventory_scope(), api.scope.progress_scope(lambda: events.append("inner")):
                observed_collect("psmux.exe")
                api.scope.observation_boundary()
                raise RuntimeError("failed")
        api.scope.observation_boundary()
        assert inventory.completed == {"wt-one": (1, "$7:100")}
    api.scope.observation_boundary()
    assert events == ["inner", "outer"]


def test_projected_formatter_marks_advisory_state_without_inventing_counts(api: SimpleNamespace) -> None:
    row = {
        "title": "title#\n", "state": "dirty", "ahead": 2, "behind": True,
        "closure": {"supported": True, "style": "dirty", "compact": "DIRTY"},
    }
    current = api.formatter.render_projected_segment(row, current=True)
    stale = api.formatter.render_projected_segment(row, current=False)
    assert current.startswith("title## ") and "\n" not in current
    assert "\u21912" in current and "\u2193" not in current
    assert "DIRTY ?" in stale and "\u2191" not in stale
    assert "STATE(?)" in api.formatter.render_projected_segment(None, current=False)
    convo = {
        **row, "state": "convo", "turn_count": 12,
        "closure": {"supported": True, "style": "convo", "compact": "CONVO"},
    }
    assert "CONVO 12\U0001f4ac" in api.formatter.render_projected_segment(convo, current=True)
    advisory = api.formatter.render_projected_segment(convo, current=False)
    assert "CONVO ?" in advisory and "\U0001f4ac" not in advisory
    for count in (True, -1, None):
        assert "\U0001f4ac" not in api.formatter.render_projected_segment(
            {**convo, "turn_count": count}, current=True,
        )
