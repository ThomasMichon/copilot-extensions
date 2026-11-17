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
    current = ["project"]
    stub("config", active_project=lambda: current[0], set_active_project=lambda value: current.__setitem__(0, value))
    stub("config_cache", project_scope=lambda *args: nullcontext())
    stub("push_timeout", ObservationCleanupError=type("CleanupError", (Exception,), {}),
         ObservationCleanupPending=type("CleanupPending", (Exception,), {}))
    stub("status_bar_cli", _render_status_context=lambda *a, **k: "context",
         _DESCRIPTOR_STYLE_BG={"dirty": "colour001"}, _SEGMENT_TITLE_MAX=48)
    stub("closure_descriptor", interpret_descriptor_payload=lambda payload: payload or {
        "supported": False, "style": "unknown", "compact": "STATE(?)",
    })
    stub("tracking", find_worktree_id_by_cwd=lambda *args: "one")
    entry = {
        "project": "project", "worktree_id": "one", "worktree_path": "/synthetic/one",
        "mux_session": "wt-one", "session_incarnation": "$7:100",
    }
    stub("status_monitor_runtime", _monitor_managed_session_union=lambda *a: (
        {"wt-one": entry, "wt-absent": {**entry, "mux_session": "wt-absent"}}, set(), [],
    ))
    stub("mux_status_link")
    stub("__main__", _status_monitor_recheck=lambda *a: None,
         _StatusMonitorGovernanceDeferred=type("GovernanceDeferred", (Exception,), {}))
    scope = load("observation_progress")
    load("monitor_projection_cache")
    content = load("status_monitor_content")
    formatter = load("mux_state_projection")
    projects = load("status_monitor_roster_demand")
    presentation = load("status_monitor_presentation")
    return SimpleNamespace(
        scope=scope, presentation=presentation, formatter=formatter, package=package,
        projects=projects,
        content=content,
    )


class CompletedSegments:
    def observation_blocked(self, project: str, path: str | None = None) -> bool:
        return False

    def get_cached(self, path: str) -> str | None:
        return "completed"

    def get(self, path: str) -> str:
        pytest.fail("projection entered the compute-on-miss getter")


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
        events.append("write:" + options["session_name"])
        return {"context_published": True}

    api.package.mux_status_link.publish_managed_session_status = publish
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
        picker_projects=api.projects.managed_roster_projects(object()),
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
    api.content.refresh_registered_content(
        {"untracked": None}, owner, governance_deferred=governance, admitted=lambda: False,
    )
    assert attempted == ["cleanup", "pending", "ready"]
    with pytest.raises(governance):
        api.content.refresh_registered_content(
            {"governance": None, "later": None}, owner, governance_deferred=governance,
        )
    assert attempted == ["cleanup", "pending", "ready", "governance"]
    assert api.package.config.active_project() == "project"


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
