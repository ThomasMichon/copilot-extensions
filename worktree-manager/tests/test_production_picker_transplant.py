"""Guards for the wholesale production Picker transplant."""

from __future__ import annotations

import importlib.util
import json
import binascii
import zlib
from pathlib import Path

import pytest

from worktree_manager import __main__ as entrypoint
from worktree_manager import launcher
from worktree_manager.production_picker import runner
from worktree_manager import agent_worktrees_runtime as engine_runtime

from _installation_context_fixtures import namespaced_fixture, patch_profile


def _write_policy(profile: Path, *, enabled: bool) -> None:
    policy_dir = profile / ".copilot-extensions"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "installation-mode.json").write_text(
        json.dumps({
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": enabled},
        }),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _no_relocated_launch_script_by_default(monkeypatch):
    """Default every ``_run_launch`` test to the pre-relocation ``launcher.py``
    path (Phase 3b Sub-slice 2a Step 2 added a relocated launch-session script
    this checkout genuinely ships at ``worktree-manager/bin/``; without this
    default, any test that doesn't mock subprocess spawning would try to run
    that real script). Tests that specifically cover the relocated-script
    delegation override this explicitly."""
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: None)


def test_transplanted_picker_sources_match_production_copy():
    root = Path(__file__).resolve().parents[2]
    source = root / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    transplanted = (
        root / "worktree-manager" / "src" / "worktree_manager" / "production_picker"
    )
    relative_paths = [
        Path("picker.py"),
        *(
            path.relative_to(source)
            for path in sorted((source / "picker_tui").glob("*.py"))
            # These are Manager-owned process-boundary adapters, not source
            # copies. All other transplanted modules remain byte-identical.
            if path.name not in {
                "data_local.py",
                "data_ssh.py",
                "engine.py",
                "maintenance.py",
                "pivots.py",
                # derive.py now imports agent_worktrees.reciprocal_state
                # (relocated out of agent-worktrees' picker_tui/, 2026-09-16,
                # worktree-manager-control-plane Phase 3/6 Step 1.5 -- that
                # module is load-bearing for agent-worktrees' non-TUI CLI and
                # can't live inside a package Worktree Manager also vendors
                # byte-for-byte). Worktree Manager keeps its own local
                # picker_tui/reciprocal.py sibling instead, so derive.py's
                # import line necessarily diverges between the two copies.
                "derive.py",
                # __init__.py's launch call now imports
                # agent_worktrees.launch_trace for the same Step 1.5 reason
                # (append_launch_event moved out of picker_tui/frame_health.py
                # on the agent-worktrees side; Worktree Manager keeps its own
                # local frame_health.append_launch_event untouched).
                "__init__.py",
                # frame_health.py's own _timestamp/_launch_trace_path helpers
                # now import from agent_worktrees.launch_trace instead of
                # defining them locally (same Step 1.5 relocation); Worktree
                # Manager's copy keeps its original self-contained version.
                "frame_health.py",
                # profiles_io.py now imports agent_worktrees.roster (roster.py
                # relocated out of picker_tui/ for the same Step 1.5 reason --
                # local_host() is called from agent-worktrees' standalone
                # cmd_profiles CLI command, not just the Picker's Profiles
                # view). Worktree Manager keeps its own local
                # picker_tui/roster.py sibling instead.
                "profiles_io.py",
            }
        ),
    ]

    assert relative_paths
    for relative in relative_paths:
        assert (transplanted / relative).read_bytes() == (source / relative).read_bytes()
    assert "WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE" in (
        transplanted / "picker_tui" / "pivot_registry_scan.py"
    ).read_text(encoding="utf-8")


def test_production_runner_activates_project_and_uses_transplanted_ui(monkeypatch):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    runner.context.reset()
    monkeypatch.setattr(
        runner.engine_group_b,
        "picker_bootstrap",
        lambda project: calls.append(("bootstrap", project)) or {
            "version": 1,
            "project": "resolved-demo",
            "should_switch_cwd": True,
            "cwd": "C:/resolved-demo",
            "default_live": False,
        },
    )
    monkeypatch.setattr(
        runner.engine_group_b,
        "repair_stale_anchor",
        lambda project: calls.append(("heal", project)),
    )
    monkeypatch.setattr(runner.os, "chdir", lambda path: calls.append(("chdir", path)))
    monkeypatch.setattr(
        runner.housekeeping,
        "reap_orphan_mux_sessions",
        lambda **kwargs: calls.append(("reap", kwargs.get("only_owned"))),
    )
    monkeypatch.setattr(
        runner.housekeeping,
        "sweep_managed_on_exit",
        lambda: calls.append(("managed",)),
    )
    monkeypatch.setattr(
        runner.housekeeping,
        "sweep_launcher_shells_on_exit",
        lambda: calls.append(("shells",)),
    )
    monkeypatch.setattr(
        runner.housekeeping,
        "sweep_finished_sessions_on_cadence",
        lambda: calls.append(("finished",)),
    )
    monkeypatch.setattr(
        runner.housekeeping,
        "start_picker_monitor_root",
        lambda: calls.append(("monitor", runner.context.project())) or None,
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live: calls.append(("picker", live, runner.context.project()))
        or {"action": "new"},
    )
    monkeypatch.setattr(runner.threading, "Thread", ImmediateThread)

    try:
        assert runner.run("demo") == {"action": "new"}
        assert calls == [
            ("bootstrap", "demo"),
            ("chdir", "C:/resolved-demo"),
            ("heal", "resolved-demo"),
            ("reap", True),
            ("managed",),
            ("shells",),
            ("finished",),
            ("monitor", "resolved-demo"),
            ("picker", False, "resolved-demo"),
        ]
        assert runner.context.project() == "resolved-demo"
    finally:
        runner.context.reset()


def test_prepare_does_not_block_first_paint_on_background_anchor_repair(monkeypatch):
    """``_prepare`` must return before the background repair finishes."""
    import threading

    heal_started = threading.Event()
    release_heal = threading.Event()

    runner.context.reset()
    monkeypatch.setattr(
        runner.engine_group_b,
        "picker_bootstrap",
        lambda project: {
            "version": 1,
            "project": "resolved-demo",
            "should_switch_cwd": False,
            "cwd": None,
            "default_live": True,
        },
    )

    def _repair(project):
        assert project == "resolved-demo"
        heal_started.set()
        assert release_heal.wait(timeout=5), "heal worker never released"

    monkeypatch.setattr(runner.engine_group_b, "repair_stale_anchor", _repair)

    try:
        default_live = runner._prepare("demo")
        assert default_live is True
        assert runner.context.project() == "resolved-demo"
        assert heal_started.wait(timeout=5), (
            "background anchor-heal worker never started")
    finally:
        release_heal.set()
        runner.context.reset()


def test_prepare_requires_picker_bootstrap_support(monkeypatch):
    from worktree_manager import engine_client

    runner.context.reset()

    def _boom(project):
        raise engine_client.EngineFeatureUnavailable("older engine")

    monkeypatch.setattr(runner.engine_group_b, "picker_bootstrap", _boom)

    try:
        with pytest.raises(RuntimeError, match="picker-bootstrap --json"):
            runner._prepare("demo")
    finally:
        runner.context.reset()


def test_engine_runtime_prefers_explicit_context_over_checkout(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    install, _python = namespaced_fixture(home, windows=engine_runtime.os.name == "nt")
    slot = install.parent / "versions" / "1.2.3"
    source = (
        slot / "Lib" / "site-packages"
        if engine_runtime.os.name == "nt"
        else slot / "lib" / "python3.10" / "site-packages"
    )
    (source / "agent_worktrees").mkdir(parents=True)
    _write_policy(home, enabled=True)
    patch_profile(monkeypatch, engine_runtime.agent_plugin_runtime, home)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    monkeypatch.delenv(engine_runtime.ENGINE_SOURCE_ENV, raising=False)
    monkeypatch.setattr(engine_runtime, "_checkout_source", lambda: tmp_path / "checkout")

    assert engine_runtime._active_runtime_source() == source


def test_engine_runtime_falls_back_to_last_known_good_slot(monkeypatch, tmp_path):
    """A stale/missing current-version marker must not blank out the source
    when last-known-good still names a real, importable, COMPLETE slot --
    the same marker/fallback walk engine_client's resolver already does."""
    root = tmp_path / "legacy" / ".agent-worktrees"
    good_slot = root / "versions" / "1.2.2"
    good_source = (
        good_slot / "Lib" / "site-packages"
        if engine_runtime.os.name == "nt"
        else good_slot / "lib" / "python3.10" / "site-packages"
    )
    (good_source / "agent_worktrees").mkdir(parents=True)
    (good_slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    python = good_slot / ("Scripts/python.exe" if engine_runtime.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    (root / "current-version").write_text("missing", encoding="utf-8")
    (root / "last-known-good").write_text("1.2.2", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "1.2.2"},
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "legacy"))

    assert engine_runtime._active_runtime_source() == good_source


def test_engine_runtime_skips_incomplete_slot_for_picker_source(monkeypatch, tmp_path):
    """A current slot that exists but has not finished installing (no
    ``.install-complete.json``) must not be importable by the Picker, even
    though its ``agent_worktrees`` package directory is already present --
    matching the command resolver's own completion-marker requirement."""
    root = tmp_path / "legacy" / ".agent-worktrees"
    incomplete_slot = root / "versions" / "1.2.3"
    incomplete_source = (
        incomplete_slot / "Lib" / "site-packages"
        if engine_runtime.os.name == "nt"
        else incomplete_slot / "lib" / "python3.10" / "site-packages"
    )
    (incomplete_source / "agent_worktrees").mkdir(parents=True)
    # Deliberately no .install-complete.json under incomplete_slot.
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "legacy"))

    assert engine_runtime._active_runtime_source() is None


def test_engine_runtime_ignores_context_when_cells_policy_disabled(monkeypatch, tmp_path):
    """The 'same config' invariant: a context that names agent-worktrees is
    never trusted on its own. When the shared installation-mode policy
    (mirrored across every agent-* plugin's own bootstrap) has marketplace
    cells off, the context is ignored exactly as if it were absent."""
    home = tmp_path / "home"
    home.mkdir()
    install, _python = namespaced_fixture(home, windows=engine_runtime.os.name == "nt")
    # No policy file written -- absent policy means disabled by default.
    patch_profile(monkeypatch, engine_runtime.agent_plugin_runtime, home)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    monkeypatch.delenv(engine_runtime.ENGINE_SOURCE_ENV, raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "no-legacy-here"))

    assert engine_runtime._active_runtime_source() is None


def test_engine_runtime_rejects_foreign_explicit_context(monkeypatch, tmp_path):
    install = tmp_path / "install.json"
    install.write_text(json.dumps({"pluginId": "agent-bridge"}), encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))

    with pytest.raises(engine_runtime.EngineRuntimeError, match="does not own agent-worktrees"):
        engine_runtime.ensure_engine_runtime()


def test_engine_runtime_falls_back_to_canonical_libs_for_uv_editable_lib(monkeypatch, tmp_path):
    """A lib converted to the `uv`-editable canonical-reference form
    (vendor-pointer-generalization effort, Phase 1 -- e.g.
    lazy-cli-dispatch) has NO local copy under
    plugins/agent-worktrees/libs/<lib> in a dev checkout at all --
    ensure_engine_runtime() must fall back to the monorepo's own canonical
    libs/<lib>/src (the same live source `uv`'s own [tool.uv.sources]
    reference already resolves to), not silently omit it from sys.path
    (regression: ModuleNotFoundError: No module named 'lazy_cli_dispatch',
    caught by CI's own worktree-manager suite)."""
    checkout = tmp_path / "checkout"
    plugin_src = checkout / "plugins/agent-worktrees/src"
    (plugin_src / "agent_worktrees").mkdir(parents=True)
    # lazy-cli-dispatch has no local copy under the plugin's own libs/ --
    # only the monorepo's own canonical libs/lazy-cli-dispatch/src.
    canonical_lib_src = checkout / "libs/lazy-cli-dispatch/src"
    (canonical_lib_src / "lazy_cli_dispatch").mkdir(parents=True)
    (canonical_lib_src / "lazy_cli_dispatch/__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    # A different lib from `ensure_engine_runtime()`'s own checked list DOES
    # have a local copy in this synthetic checkout -- confirms the fallback
    # only ever applies when the local copy is genuinely absent, never
    # overriding one that already exists. This is deliberately a fabricated
    # scenario (a `tmp_path` fixture, not the real repo): `config-migrate`
    # is itself a `uv`-editable canonical reference for the REAL
    # `agent-worktrees` today (vendor-pointer-generalization effort), but a
    # materialized release layout carries a real local copy again, and this
    # test exercises that "local copy present" branch regardless of which
    # form the real, live `dev` checkout currently happens to use --
    # picking any real repo lib name here would otherwise make this test's
    # own comment go stale every time that lib's conversion status changes.
    local_lib_src = plugin_src.parent / "libs/config-migrate/src"
    (local_lib_src / "config_migrate").mkdir(parents=True)

    monkeypatch.delenv(engine_runtime.ENGINE_SOURCE_ENV, raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(engine_runtime, "_checkout_source", lambda: plugin_src)
    monkeypatch.setattr(engine_runtime.sys, "path", [])

    engine_runtime.ensure_engine_runtime()

    assert str(canonical_lib_src) in engine_runtime.sys.path
    assert str(local_lib_src) in engine_runtime.sys.path


def test_engine_runtime_installed_slot_never_uses_the_checkout_fallback(monkeypatch, tmp_path):
    """An installed runtime (a site-packages slot) has no monorepo
    libs/+plugins/ ancestor at all -- the canonical-libs fallback must
    never fire there (it always carries a real local copy of every engine
    lib, materialized at promotion time; attempting the fallback would be
    a silent no-op at best, but must never be attempted regardless)."""
    home = tmp_path / "home"
    home.mkdir()
    install, _python = namespaced_fixture(home, windows=engine_runtime.os.name == "nt")
    slot = install.parent / "versions" / "1.2.3"
    source = (
        slot / "Lib" / "site-packages"
        if engine_runtime.os.name == "nt"
        else slot / "lib" / "python3.10" / "site-packages"
    )
    (source / "agent_worktrees").mkdir(parents=True)
    _write_policy(home, enabled=True)
    patch_profile(monkeypatch, engine_runtime.agent_plugin_runtime, home)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    monkeypatch.delenv(engine_runtime.ENGINE_SOURCE_ENV, raising=False)
    monkeypatch.setattr(engine_runtime, "_checkout_source", lambda: tmp_path / "no-checkout-here")
    monkeypatch.setattr(engine_runtime.sys, "path", [])

    engine_runtime.ensure_engine_runtime()

    assert not any("lazy-cli-dispatch" in p or "lazy_cli_dispatch" in p
                   for p in engine_runtime.sys.path)


def test_production_runner_mock_skips_mutating_startup(monkeypatch):
    calls = []

    runner.context.reset()
    monkeypatch.setattr(
        runner.engine_group_b,
        "picker_bootstrap",
        lambda project: calls.append(("bootstrap", project)) or {
            "version": 1,
            "project": "resolved-demo",
            "should_switch_cwd": False,
            "cwd": None,
            "default_live": True,
        },
    )
    monkeypatch.setattr(
        runner.engine_group_b,
        "repair_stale_anchor",
        lambda project: calls.append(("heal", project)),
    )
    monkeypatch.setattr(
        runner,
        "_start_housekeeping",
        lambda: calls.append(("housekeeping",)),
    )
    monkeypatch.setattr(
        runner.housekeeping,
        "start_picker_monitor_root",
        lambda: calls.append(("monitor",)),
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live, mock_mode: calls.append(
            ("picker", live, mock_mode, runner.context.project())
        ) or None,
    )

    try:
        assert runner.run("demo", mock_mode=True, local=True) is None
        assert calls == [
            ("bootstrap", "demo"),
            ("picker", False, True, "resolved-demo"),
        ]
    finally:
        runner.context.reset()


def test_production_capture_uses_read_only_prepare(monkeypatch):
    from worktree_manager.production_picker.picker_tui import capture as picker_capture

    calls = []
    monkeypatch.setattr(
        runner,
        "_prepare",
        lambda project, *, heal: calls.append((project, heal)) or True,
    )
    monkeypatch.setattr(
        picker_capture,
        "capture",
        lambda source, **kwargs: {
            "text": "GRID\n",
            "ansi": "ANSI\n",
            "svg": "<svg />",
        },
    )

    assert runner.capture("demo")["text"] == "GRID\n"
    assert calls == [("demo", False)]


def test_manager_acts_on_production_picker_new_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"no_mux": False, "seed_prompt": "fix the flaky test"},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 23,
    )

    assert entrypoint._run_production_picker("demo") == 23
    assert requests[0].project == "demo"
    assert requests[0].mode == "new"
    assert requests[0].worktree_id is None
    assert requests[0].no_mux is False
    assert requests[0].seed_prompt == "fix the flaky test"


def test_manager_acts_on_production_picker_new_decision_with_no_seed_prompt(
    monkeypatch,
):
    """An absent/blank seed_prompt (Bare, or a skipped/blank Launch) must
    translate to None, not an empty string, through to LaunchRequest. This
    only verifies that normalization -- `_resolve_for()`'s own forwarding of
    `seed_prompt` to `engine_client.resolve_launch_plan()`'s `seed` kwarg is
    covered separately, by
    `test_resolve_for_forwards_seed_prompt_to_resolve_launch_plan`."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"no_mux": False, "seed_prompt": ""},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 23,
    )

    assert entrypoint._run_production_picker("demo") == 23
    assert requests[0].seed_prompt is None


def test_manager_acts_on_production_picker_resume_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume me",
            "is_local": True,
            "options": {"bare_resume": True, "no_mux": True, "ahp": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "bare-resume"
    assert requests[0].no_mux is True
    assert requests[0].seed_prompt is None


def test_manager_acts_on_production_picker_resume_decision_with_seed_prompt(
    monkeypatch,
):
    """resume-prompt-durable-seed-and-mux-fix Phase 2 ("Resume prompt…"): a
    resume decision's own ``options["seed_prompt"]`` (set by
    ``_resume_decision``, same shape as a "New worktree…" decision's) must
    thread through to ``LaunchRequest.seed_prompt`` for ``action: "resume"``
    too -- not only ``action: "new"`` (covered separately by
    ``test_manager_acts_on_production_picker_new_decision``).
    """
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume me",
            "is_local": True,
            "options": {"seed_prompt": "sync, then status"},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "resume"
    assert requests[0].seed_prompt == "sync, then status"


def test_manager_acts_on_production_picker_refresh_decision(monkeypatch):
    """The "Update available" gesture (`action: refresh`) must (1) thread the
    resolved project through to `_cmd_update`, mirroring every other decision
    branch here, and (2) loop back to reopen the very same Picker afterward
    instead of ending the whole `worktree-manager` process -- a prior bug
    (#worktree-manager-update-loop) `return`ed `_cmd_update`'s own exit code
    from here, silently exiting the process on "Update available" instead of
    reloading the Manager."""
    run_calls = []

    def fake_run(project):
        run_calls.append(project)
        if len(run_calls) == 1:
            return {"action": "refresh"}
        return None  # operator closed the picker on the reopened run

    monkeypatch.setattr(runner, "run", fake_run)
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_cmd_update",
        lambda rest: calls.append(rest) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert calls == [["--project", "demo"]]
    # The Picker was reopened (a second `runner.run` call) rather than the
    # function returning `_cmd_update`'s own exit code straight away.
    assert run_calls == ["demo", "demo"]


def test_manager_acts_on_production_picker_manager_update_decision(monkeypatch):
    """The Manager's OWN update button (`action: manager-update`, zone
    "MUP") runs the same update command (which self-updates the Manager
    first) and likewise reopens the Picker rather than exiting."""
    run_calls = []

    def fake_run(project):
        run_calls.append(project)
        if len(run_calls) == 1:
            return {"action": "manager-update"}
        return None

    monkeypatch.setattr(runner, "run", fake_run)
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_cmd_update",
        lambda rest: calls.append(rest) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert calls == [["--project", "demo"]]
    assert run_calls == ["demo", "demo"]


def test_production_picker_restores_priority_before_dispatch_and_reboosts_on_refresh(
    monkeypatch,
):
    """The priority boost must bracket ONLY the interactive TUI session:
    raise -> runner.run -> restore -> dispatch, in that exact order, so a
    dispatched launch (which spawns the real workload as this process's
    child) never inherits the boost. On a refresh/manager-update loop-back,
    the Picker must be re-boosted before its second `runner.run` call too."""
    from worktree_manager import process_priority

    events: list[str] = []
    monkeypatch.setattr(
        process_priority,
        "raise_current_process_priority",
        lambda: events.append("raise"),
    )
    monkeypatch.setattr(
        process_priority,
        "restore_normal_process_priority",
        lambda: events.append("restore"),
    )

    run_calls = []

    def fake_run(project):
        events.append("run")
        run_calls.append(project)
        if len(run_calls) == 1:
            return {"action": "refresh"}
        return None

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(
        entrypoint,
        "_cmd_update",
        lambda rest: events.append("dispatch") or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert events == ["raise", "run", "restore", "dispatch", "raise", "run", "restore"]


def test_manager_acts_on_production_picker_open_venue_decision(monkeypatch):
    """picker-venue-pivots Phase 3: the "open-venue" decision hands the
    provider/venue straight to ``launcher.open_venue`` and returns its exit
    code -- the Codespaces/Containers pivots' Open action."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "open-venue",
            "provider": "agent-codespaces",
            "venue": "my-codespace",
        },
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "open_venue",
        lambda provider, venue: calls.append((provider, venue)) or 42,
    )

    assert entrypoint._run_production_picker("demo") == 42
    assert calls == [("agent-codespaces", "my-codespace")]


def test_manager_open_venue_decision_missing_fields_delegates_to_launcher(monkeypatch):
    """A missing provider/venue is still handed to `launcher.open_venue` as
    `""` -- that function (not this dispatch) owns validating/reporting it,
    per its own docstring."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {"action": "open-venue", "venue": "my-codespace"},
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "open_venue",
        lambda provider, venue: calls.append((provider, venue)) or 1,
    )

    assert entrypoint._run_production_picker("demo") == 1
    assert calls == [("", "my-codespace")]


def test_manager_restores_local_session_then_uses_common_launch_gate(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "title": "Restore me",
            "is_local": True,
        },
    )
    from worktree_manager import engine_client

    calls = []
    monkeypatch.setattr(
        engine_client, "project_binstub_command", lambda project: Path("demo.cmd")
    )
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda project, args, **kwargs: (
            calls.append(("remux", project, args, kwargs))
            or {"ok": True, "action": "reclaimed", "requires_resume": True}
        ),
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert calls[0][0:3] == (
        "remux",
        "demo",
        ["remux", "--worktree-id", "demo-1234", "--yes", "--json"],
    )
    assert requests[0].project == "demo"
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "resume"


def test_manager_does_not_launch_when_restore_prep_fails(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "is_local": True,
        },
    )
    from worktree_manager import engine_client

    monkeypatch.setattr(
        engine_client, "project_binstub_command", lambda project: Path("demo.cmd")
    )
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda *args, **kwargs: {"ok": False, "reason": "ambiguous owner"},
    )
    monkeypatch.setattr(
        engine_client,
        "run_project_passthrough",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("launch must not run")
        ),
    )

    assert entrypoint._run_production_picker("demo") == 1


def test_manager_does_not_launch_unverified_live_adoption(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "is_local": True,
        },
    )
    from worktree_manager import engine_client

    monkeypatch.setattr(
        engine_client, "project_binstub_command", lambda project: Path("demo.cmd")
    )
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda *args, **kwargs: {
            "ok": True,
            "action": "adopted",
            "verified": False,
        },
    )
    monkeypatch.setattr(
        engine_client,
        "run_project_passthrough",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("launch must not run")
        ),
    )

    assert entrypoint._run_production_picker("demo") == 1


def test_manager_restores_remote_session_then_resumes(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "title": "Restore remotely",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
        },
    )
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    monkeypatch.setattr(
        data_ssh,
        "remote_op_argv",
        lambda *args, **kwargs: ["ssh", "example", "demo remux"],
    )
    monkeypatch.setattr(
        maintenance,
        "_ssh_json",
        lambda argv: {"ok": True, "verified": True},
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].machine == "Example"
    assert requests[0].environment == "WSL"


def test_manager_reports_remote_restore_transport_failure(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
        },
    )
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    monkeypatch.setattr(
        data_ssh,
        "remote_op_argv",
        lambda *args, **kwargs: ["ssh", "example", "demo remux"],
    )
    monkeypatch.setattr(
        maintenance,
        "_ssh_json",
        lambda argv: (_ for _ in ()).throw(FileNotFoundError("ssh missing")),
    )

    assert entrypoint._run_production_picker("demo") == 1


def test_manager_rejects_crafted_remote_ahp_picker_decision(monkeypatch, capsys):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume remotely",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
            "options": {"bare_resume": True, "no_mux": True, "ahp": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 1
    assert requests == []
    assert "same-machine" in capsys.readouterr().out


def test_manager_acts_on_base_repo_production_picker_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"anchor": True, "no_mux": False},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].mode == "base"
    assert requests[0].worktree_id is None


def test_run_launch_executes_remote_plan(monkeypatch):
    plan = type("Plan", (), {"action": "remote", "exit_code": 0})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    calls = []
    from worktree_manager import launcher

    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 17,
    )
    request = type("Request", (), {"no_mux": True})()

    assert entrypoint._run_launch(request) == 17
    assert calls == [(plan, False)]


def test_run_launch_rejects_crafted_remote_ahp_before_launch(
    monkeypatch,
):
    from worktree_manager import engine_client, launcher

    plan = type("Plan", (), {"action": "remote", "exit_code": 0})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("remote launch must not inspect local execution-leg state")
        ),
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": "Example",
            "environment": "WSL",
            "no_mux": False,
            "ahp": True,
        },
    )()

    assert entrypoint._run_launch(request) == 1
    assert calls == []


def test_run_launch_selects_ahp_after_plan_resolution(monkeypatch, tmp_path):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
            "work_dir": str(tmp_path),
        },
    )()
    hosted = object()
    attachment = object()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {"execution_leg": None},
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda project, worktree_id, work_dir: (
            attachment
            if (project, worktree_id, work_dir)
            == ("demo", "demo-1234", str(tmp_path))
            else None
        ),
    )
    monkeypatch.setattr(ahp_provider, "attach_plan", lambda value, found: hosted)
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": True,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(hosted, True)]


def test_run_launch_selects_ahp_without_mux_when_both_requested(
    monkeypatch, tmp_path
):
    """AHP (backend) and mux (presentation) stay independent: selecting the
    hosted backend while also requesting No Mux must still use AHP, but pass
    ``want_mux=False`` to the launcher."""
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
            "work_dir": str(tmp_path),
        },
    )()
    hosted = object()
    attachment = object()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {"execution_leg": None},
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda project, worktree_id, work_dir: (
            attachment
            if (project, worktree_id, work_dir)
            == ("demo", "demo-1234", str(tmp_path))
            else None
        ),
    )
    monkeypatch.setattr(ahp_provider, "attach_plan", lambda value, found: hosted)
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "mode": "resume",
            "machine": None,
            "no_mux": True,
            "ahp": True,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(hosted, False)]


def test_run_launch_delegates_to_relocated_script_on_windows(monkeypatch, tmp_path):
    """The relocated launch-session script is the canonical mux implementation
    (DQ9); when present, _run_launch delegates the whole local, non-AHP
    launch to it instead of the never-wired launcher.compose_launch path."""
    import subprocess

    from worktree_manager import ahp_provider, launcher

    plan = type(
        "Plan",
        (),
        {"action": "exec", "exit_code": 0, "worktree_id": "resolved-id-1234"},
    )()
    script = tmp_path / "launch-session.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not fall back to launcher.py when the "
                           "relocated script is available")
        ),
    )
    calls = []

    class _FakeProc:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **_kwargs):
        calls.append(argv)
        return _FakeProc()

    monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", fake_popen)
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": None,
            "mode": "new",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:4] == ["pwsh.exe", "-NoProfile", "-NoLogo", "-File"]
    assert argv[4] == str(script)
    # A "new" request must resolve to the ALREADY-created plan.worktree_id
    # (--worktree-id), never re-issuing --new (which would create a SECOND
    # worktree by re-triggering creation inside the script's own resolve).
    assert "--new" not in argv
    assert argv[-2:] == ["--worktree-id", "resolved-id-1234"]


def test_run_launch_relocated_script_uses_base_flag_for_anchor_mode(
    monkeypatch, tmp_path
):
    """``mode == "base"`` (anchor-repo launch) must pass ``--base`` and never
    ``--worktree-id``, even though ``plan.worktree_id`` may be unset."""
    from worktree_manager import ahp_provider, launcher

    plan = type(
        "Plan",
        (),
        {"action": "exec", "exit_code": 0, "worktree_id": None},
    )()
    script = tmp_path / "launch-session.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not fall back to launcher.py")
        ),
    )
    calls = []

    class _FakeProc:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **_kwargs):
        calls.append(argv)
        return _FakeProc()

    monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", fake_popen)
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": None,
            "mode": "base",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert len(calls) == 1
    argv = calls[0]
    assert "--base" in argv
    assert "--worktree-id" not in argv


def test_seed_already_claimed_in_trusts_only_explicit_metadata():
    """A configured launch/profile argument can legitimately produce a
    ``cmd`` that ALSO ends in ``--interactive <value>`` (e.g. a profile
    that already supplies the flag) even when no seed was ever claimed --
    so the trailing pair alone is not sufficient provenance. Only the
    engine's own explicit ``seed_claimed`` metadata authorizes treating it
    as the claimed seed."""
    from worktree_manager.relocated_launch import _seed_already_claimed_in

    claimed_plan = type(
        "Plan", (), {
            "seed_claimed": True,
            "cmd": ["copilot", "--resume=sess1", "--interactive", "the real seed"],
        },
    )()
    assert _seed_already_claimed_in(claimed_plan) == "the real seed"

    # Same trailing shape, but seed_claimed is False -- a configured
    # argument coincidentally ending the same way must NOT be mistaken for
    # a claimed seed.
    unclaimed_plan = type(
        "Plan", (), {
            "seed_claimed": False,
            "cmd": ["copilot", "--resume=sess1", "--interactive",
                    "configured, not a seed"],
        },
    )()
    assert _seed_already_claimed_in(unclaimed_plan) is None

    # An older engine predating this field: absent entirely, defaults False.
    no_metadata_plan = type(
        "Plan", (), {"cmd": ["copilot", "--resume=sess1", "--interactive", "x"]},
    )()
    assert _seed_already_claimed_in(no_metadata_plan) is None

    no_seed_plan = type(
        "Plan", (), {"seed_claimed": True, "cmd": ["copilot", "--resume=sess1"]},
    )()
    assert _seed_already_claimed_in(no_seed_plan) is None

    empty_plan = type("Plan", (), {"seed_claimed": True, "cmd": []})()
    assert _seed_already_claimed_in(empty_plan) is None


def test_run_launch_relocated_script_forwards_already_claimed_seed(
    monkeypatch, tmp_path,
):
    """The relocated script re-resolves the launch plan ITSELF rather than
    reusing ``plan.cmd`` -- so a seed the earlier ``_resolve_for`` call
    already claimed (persisted ``pending_seed`` cleared) and embedded as
    ``--interactive <seed>`` into that now-discarded ``plan.cmd`` must be
    recovered and forwarded as ``--seed`` to this re-invocation, or it is
    silently lost (claimed once, delivered never)."""
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec", "exit_code": 0, "worktree_id": "resolved-id-1234",
            "cmd": ["copilot", "--resume=sess1", "--interactive", "do the thing"],
            "seed_claimed": True,
        },
    )()
    script = tmp_path / "launch-session.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not fall back to launcher.py")
        ),
    )
    calls = []

    class _FakeProc:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **_kwargs):
        calls.append(argv)
        return _FakeProc()

    monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", fake_popen)
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "resolved-id-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
            "seed_prompt": None,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert len(calls) == 1
    argv = calls[0]
    assert argv[-2:] == ["--seed", "do the thing"]


def test_run_launch_relocated_script_bare_resume_never_forwards_seed(
    monkeypatch, tmp_path,
):
    """Bare resume never carries a seed through to the relocated script,
    even if ``plan.cmd`` somehow carried one -- matching the engine's own
    rejection of --seed alongside --bare-resume."""
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec", "exit_code": 0, "worktree_id": "resolved-id-1234",
            "cmd": ["copilot"],
        },
    )()
    script = tmp_path / "launch-session.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must not fall back to launcher.py")
        ),
    )
    calls = []

    class _FakeProc:
        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **_kwargs):
        calls.append(argv)
        return _FakeProc()

    monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", fake_popen)
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "resolved-id-1234",
            "mode": "bare-resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
            "seed_prompt": None,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert len(calls) == 1
    argv = calls[0]
    assert "--seed" not in argv
    assert "--bare-resume" in argv


def test_run_launch_relocated_script_sets_no_mux_env(monkeypatch, tmp_path):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {"action": "exec", "exit_code": 0, "worktree_id": "demo-1234"},
    )()
    script = tmp_path / "launch-session.ps1"
    script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(launcher, "launch", lambda *_a, **_k: 1 / 0)
    monkeypatch.delenv("WORKTREE_NO_MUX", raising=False)

    class _FakeProc:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", lambda *_a, **_k: _FakeProc())
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": True,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert entrypoint.os.environ.get("WORKTREE_NO_MUX") == "1"
    monkeypatch.delenv("WORKTREE_NO_MUX", raising=False)


def test_run_launch_relocated_script_posix_execs_bash(monkeypatch, tmp_path):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {"action": "exec", "exit_code": 0, "worktree_id": "demo-1234"},
    )()
    script = tmp_path / "launch-session.sh"
    script.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(launcher, "launch", lambda *_a, **_k: 1 / 0)
    calls = []

    def fake_execvp(file, argv):
        calls.append((file, argv))
        raise SystemExit(0)

    monkeypatch.setattr(entrypoint, "_is_windows", lambda: False)
    monkeypatch.setattr(entrypoint.os, "execvp", fake_execvp)
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "bare-resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    with pytest.raises(SystemExit):
        entrypoint._run_launch(request)
    assert calls[0][0] == "bash"
    assert calls[0][1][0] == "bash"
    assert str(script) in calls[0][1]
    assert "--bare-resume" in calls[0][1]


class TestRunLaunchNewWindow:
    """Phase 9 (#5210): ``new_window=True`` opens the SAME relocated
    launch-session script in a new, visible window instead of blocking/
    exec-replacing the caller's own process -- so the script's mux-daemon
    registration always runs, and the caller (the live Picker TUI) is never
    torn down or blocked. Reuses the identical argv every other local launch
    builds (proving registration parity); refuses, rather than silently
    falling back to a blocking/exec path, whenever the request can't be
    satisfied through the relocated script (remote, AHP, or script absent)."""

    def _plan(self, *, action="exec", worktree_id="demo-1234"):
        return type(
            "Plan", (), {"action": action, "exit_code": 0, "worktree_id": worktree_id},
        )()

    def _request(self, **overrides):
        base = {
            "project": "demo", "worktree_id": "demo-1234", "mode": "resume",
            "machine": None, "no_mux": False, "ahp": False, "new_window": True,
        }
        base.update(overrides)
        return type("Request", (), base)()

    @pytest.fixture(autouse=True)
    def _no_persisted_execution_leg(self, monkeypatch):
        """Every request below is local + mode="resume"/"bare-resume" with a
        concrete worktree_id, so `_run_launch` always probes the persisted
        execution leg first; keep that probe a no-op (older-engine shape)
        unless a test overrides it, matching the sibling non-new-window
        tests above (e.g. `test_run_launch_relocated_script_sets_no_mux_env`)."""
        from worktree_manager import engine_client

        monkeypatch.setattr(
            engine_client, "execution_leg_get",
            lambda *_a, **_k: (_ for _ in ()).throw(
                engine_client.EngineFeatureUnavailable("older engine")),
        )

    def test_spawns_detached_new_window_instead_of_blocking(self, monkeypatch, tmp_path):
        from worktree_manager import ahp_provider, new_window_spawn

        script = tmp_path / "launch-session.ps1"
        script.write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (self._plan(), 0))
        monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
        monkeypatch.setattr(
            ahp_provider, "ensure_session",
            lambda *_a: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
        )
        monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)

        def boom_popen(*_a, **_k):
            raise AssertionError("new_window must never block this process with Popen().wait()")

        monkeypatch.setattr(entrypoint.subprocess, "Popen", boom_popen)
        monkeypatch.setattr(entrypoint.os, "execvp", lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("new_window must never exec-replace this process")))
        calls = []

        def fake_spawn(argv, *, title, env=None):
            calls.append((argv, title, env))
            return {"spawner": "wt.exe", "pid": 4242}

        monkeypatch.setattr(new_window_spawn, "spawn_detached_new_window", fake_spawn)

        assert entrypoint._run_launch(self._request()) == 0
        assert len(calls) == 1
        argv, title, env = calls[0]
        assert argv[:4] == ["pwsh.exe", "-NoProfile", "-NoLogo", "-File"]
        assert argv[4] == str(script)
        assert title == "demo-1234"
        assert env is None  # no --no-mux: inherit this process's environment unchanged

    def test_new_window_argv_matches_the_ordinary_launch_argv(self, monkeypatch, tmp_path):
        """The whole point of Phase 9: "new window" must run the IDENTICAL
        script invocation an ordinary (non-new-window) local launch would,
        so the script's `Invoke-ManagedMuxRegister`/mux-daemon registration
        call always fires the same way regardless of which window modifier
        was chosen."""
        from worktree_manager import ahp_provider, new_window_spawn

        script = tmp_path / "launch-session.ps1"
        script.write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (self._plan(), 0))
        monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
        monkeypatch.setattr(
            ahp_provider, "ensure_session",
            lambda *_a: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
        )
        monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)

        ordinary_calls = []

        class _FakeProc:
            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(
            entrypoint.subprocess, "Popen",
            lambda argv, **_k: ordinary_calls.append(argv) or _FakeProc(),
        )
        assert entrypoint._run_launch(self._request(new_window=False)) == 0

        new_window_calls = []
        monkeypatch.setattr(
            new_window_spawn, "spawn_detached_new_window",
            lambda argv, *, title, env=None: new_window_calls.append(argv) or {"spawner": "x", "pid": 1},
        )
        assert entrypoint._run_launch(self._request(new_window=True)) == 0

        assert ordinary_calls == new_window_calls

    def test_new_window_passes_no_mux_as_an_explicit_child_env_not_a_global_mutation(
        self, monkeypatch, tmp_path,
    ):
        """A `--no-mux` launch bypasses mux entirely (PSMux/TMux is never
        invoked -- see `launch-session.ps1`), so composing it with
        `new_window` must still run the SAME script (which itself skips
        registration for a no-mux launch). The `WORKTREE_NO_MUX=1` override
        must reach the spawned child via an explicit `env` argument, NEVER
        by mutating `os.environ` -- that would never be undone and would
        leak into every later or concurrent spawn in this long-lived Picker
        process (unlike the ordinary Popen/execvp paths, which block or
        replace this process and so have no "later spawn" to leak into)."""
        from worktree_manager import ahp_provider, new_window_spawn

        script = tmp_path / "launch-session.ps1"
        script.write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (self._plan(), 0))
        monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: script)
        monkeypatch.setattr(
            ahp_provider, "ensure_session",
            lambda *_a: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
        )
        monkeypatch.setattr(entrypoint, "_is_windows", lambda: True)
        monkeypatch.delenv("WORKTREE_NO_MUX", raising=False)
        calls = []
        monkeypatch.setattr(
            new_window_spawn, "spawn_detached_new_window",
            lambda argv, *, title, env=None: calls.append((argv, env)) or {"spawner": "x", "pid": 1},
        )

        assert entrypoint._run_launch(self._request(new_window=True, no_mux=True)) == 0
        # The override reached the spawn call as an explicit env...
        assert len(calls) == 1
        _argv, env = calls[0]
        assert env is not None
        assert env.get("WORKTREE_NO_MUX") == "1"
        # ...and os.environ itself was never touched.
        assert "WORKTREE_NO_MUX" not in entrypoint.os.environ
        monkeypatch.delenv("WORKTREE_NO_MUX", raising=False)

    def test_new_window_rejects_remote_plan(self, monkeypatch):
        plan = self._plan(action="remote")
        monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
        rc = entrypoint._run_launch(self._request(machine="dev6", environment="prod"))
        assert rc == 1

    def test_new_window_rejects_ahp(self, monkeypatch):
        monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (self._plan(), 0))
        rc = entrypoint._run_launch(self._request(ahp=True))
        assert rc == 1

    def test_new_window_rejects_missing_relocated_script(self, monkeypatch):
        from worktree_manager import ahp_provider

        monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (self._plan(), 0))
        monkeypatch.setattr(entrypoint, "_relocated_launch_script", lambda: None)
        monkeypatch.setattr(
            ahp_provider, "ensure_session",
            lambda *_a: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
        )
        rc = entrypoint._run_launch(self._request())
        assert rc == 1


def test_run_launch_default_never_calls_ahp(monkeypatch):
    from worktree_manager import ahp_provider, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(launcher, "launch", lambda *_args, **_kwargs: 0)
    request = type("Request", (), {"no_mux": False, "ahp": False})()

    assert entrypoint._run_launch(request) == 0


@pytest.mark.parametrize("state", ["active", "unknown"])
def test_run_launch_forces_persisted_active_or_unknown_ahp_binding(
    monkeypatch,
    tmp_path,
    state,
):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
            "work_dir": str(tmp_path),
        },
    )()
    attachment = object()
    hosted = object()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "ahp",
                "state": state,
                "binding_revision": 3,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args, **_kwargs: attachment,
    )
    monkeypatch.setattr(
        ahp_provider,
        "attach_plan",
        lambda value, found: hosted
        if (value, found) == (plan, attachment)
        else None,
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(hosted, True)]


def test_run_launch_allows_direct_after_disposed_binding(monkeypatch):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "ahp",
                "state": "disposed",
                "binding_revision": 4,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("disposed binding does not force AHP")
        ),
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(plan, True)]


@pytest.mark.parametrize("mode", ["resume", "bare-resume"])
def test_run_launch_preserves_direct_resume_when_execution_leg_is_unsupported(
    monkeypatch,
    mode,
):
    from worktree_manager import engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": mode,
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(plan, True)]


def test_run_launch_fails_closed_for_unknown_unavailable_provider(
    monkeypatch,
    capsys,
):
    from worktree_manager import engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "future-host",
                "state": "unknown",
                "binding_revision": 9,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not launch direct")
        ),
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 1
    assert "requires unsupported execution-leg provider future-host" in capsys.readouterr().out


def test_run_launch_rejects_bare_resume_for_active_ahp_binding(
    monkeypatch,
    capsys,
):
    from worktree_manager import engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "ahp",
                "state": "active",
                "binding_revision": 3,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bare resume must fail closed")
        ),
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "bare-resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 1
    assert "bare resume is incompatible" in capsys.readouterr().out


def test_production_picker_disposal_decision_calls_manager_owned_provider(
    monkeypatch,
    tmp_path,
):
    from worktree_manager import ahp_provider

    monkeypatch.setattr(
        runner,
        "run",
        lambda _project: {
            "action": "dispose-hosted-session",
            "worktree_id": "demo-1234",
            "is_local": True,
        },
    )
    plan = type("Plan", (), {"work_dir": str(tmp_path)})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    calls = []
    monkeypatch.setattr(
        ahp_provider,
        "dispose_worktree_session",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert calls[0][0] == ("demo", "demo-1234", str(tmp_path))


def test_resolve_for_reports_remote_plan_unavailable_on_older_engine(monkeypatch, capsys):
    from worktree_manager import engine_client

    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "bare-resume",
            "machine": "Example",
            "environment": "WSL",
            "no_mux": True,
        },
    )()
    monkeypatch.setattr(
        engine_client,
        "resolve_launch_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )

    plan, code = entrypoint._resolve_for(request)
    assert plan is None
    assert code == 1
    assert "could not resolve a launch plan: older engine" in capsys.readouterr().out


def test_resolve_for_forwards_seed_prompt_to_resolve_launch_plan(monkeypatch):
    """`_resolve_for()` must actually thread `LaunchRequest.seed_prompt`
    through to `engine_client.resolve_launch_plan()`'s own `seed` kwarg --
    not just normalize it onto the `LaunchRequest` (that's the OTHER,
    already-covered half, in
    `test_manager_acts_on_production_picker_new_decision`). Calling
    `resolve_launch_plan` directly (as those tests do) would still pass if
    this forwarding were later dropped from `_resolve_for` itself."""
    from worktree_manager import engine_client

    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": None,
            "mode": "new",
            "machine": None,
            "environment": None,
            "no_mux": False,
            "seed_prompt": "fix the flaky test",
        },
    )()
    received = {}

    def _fake_resolve(project, **kwargs):
        received.update(kwargs)
        return type("Plan", (), {"action": "none", "exit_code": 0})()

    monkeypatch.setattr(engine_client, "resolve_launch_plan", _fake_resolve)

    entrypoint._resolve_for(request)
    assert received.get("seed") == "fix the flaky test"


def test_resolve_for_forwards_none_seed_when_request_has_no_seed_prompt(monkeypatch):
    from worktree_manager import engine_client

    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": None,
            "mode": "new",
            "machine": None,
            "environment": None,
            "no_mux": False,
        },
    )()
    received = {}

    def _fake_resolve(project, **kwargs):
        received.update(kwargs)
        return type("Plan", (), {"action": "none", "exit_code": 0})()

    monkeypatch.setattr(engine_client, "resolve_launch_plan", _fake_resolve)

    entrypoint._resolve_for(request)
    # Explicit `in` check: `.get("seed")` would also pass if `_resolve_for`
    # silently stopped passing `seed` at all (dict.get returns None for a
    # missing key too) -- this must prove the kwarg was passed as None, not
    # merely absent.
    assert "seed" in received
    assert received["seed"] is None


def test_normal_picker_command_uses_production_transplant(monkeypatch):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(entrypoint, "build_projects", lambda: [])
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run_production_picker",
        lambda project: calls.append(project) or 19,
    )

    assert entrypoint._cmd_picker(["demo"]) == 19
    assert calls == ["demo"]


def test_picker_mock_uses_production_transplant_without_acting(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo", "repo": None})()],
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "run",
        lambda project, **kwargs: calls.append((project, kwargs))
        or {"action": "new"},
    )
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: (_ for _ in ()).throw(
            AssertionError("mock mode must not launch")
        ),
    )

    assert entrypoint._cmd_picker(["mock", "demo", "--local", "--json"]) == 0
    assert calls == [("demo", {"mock_mode": True, "local": True})]
    assert json.loads(capsys.readouterr().out) == {
        "mock": True,
        "decision": {"action": "new"},
    }


def test_picker_screenshot_uses_production_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo", "repo": None})()],
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "capture",
        lambda project, **kwargs: calls.append((project, kwargs))
        or {"text": "GRID\n", "ansi": "ANSI\n", "svg": "<svg />"},
    )
    out = tmp_path / "picker.txt"

    assert entrypoint._cmd_picker([
        "screenshot",
        "demo",
        "--format",
        "text",
        "--pivot",
        "Tasks",
        "--wait",
        "1.5",
        "--out",
        str(out),
    ]) == 0
    assert calls == [(
        "demo",
        {"live": False, "pivot": "Tasks", "wait_pivot": 1.5},
    )]
    assert out.read_text(encoding="utf-8") == "GRID\n"


def test_picker_screenshot_keeps_relative_output_at_caller_cwd(monkeypatch, tmp_path):
    caller = tmp_path / "caller"
    project = tmp_path / "project"
    caller.mkdir()
    project.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo", "repo": None})()],
    )

    def capture(*args, **kwargs):
        monkeypatch.chdir(project)
        return {"text": "GRID\n", "ansi": "ANSI\n", "svg": "<svg />"}

    monkeypatch.setattr(runner, "capture", capture)

    assert entrypoint._cmd_picker([
        "screenshot",
        "demo",
        "--format",
        "text",
        "--out",
        "picker.txt",
    ]) == 0
    assert (caller / "picker.txt").read_text(encoding="utf-8") == "GRID\n"
    assert not (project / "picker.txt").exists()


def test_legacy_screenshot_flag_uses_production_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo", "repo": None})()],
    )
    monkeypatch.setattr(
        runner,
        "capture",
        lambda project, **kwargs: {
            "text": "GRID\n",
            "ansi": "ANSI\n",
            "svg": "<svg>production</svg>",
        },
    )
    out = tmp_path / "picker.svg"

    assert entrypoint._cmd_picker(["demo", "--screenshot", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "<svg>production</svg>"


def test_picker_validation_assets_are_manager_owned():
    root = Path(__file__).resolve().parents[2]
    manager = root / "worktree-manager"
    corpus = manager / "tests" / "production_picker"
    snapshots = manager / "scripts" / "picker-snapshot"

    assert (corpus / "test_picker_tui.py").is_file()
    assert (corpus / "goldens" / "picker" / "worktrees_list.txt").is_file()
    assert (manager / "scripts" / "preview-picker.ps1").is_file()
    assert (manager / "scripts" / "preview-picker.sh").is_file()
    assert (manager / "scripts" / "picker-shot.py").is_file()
    assert (snapshots / "render.py").is_file()
    assert (snapshots / "svg2png.mjs").is_file()
    assert "worktree_manager.production_picker" in (
        snapshots / "render.py"
    ).read_text(encoding="utf-8")


def _picker_shot_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "picker-shot.py"
    spec = importlib.util.spec_from_file_location("manager_picker_shot", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + crc.to_bytes(4, "big")
    )


def _test_png(width: int, height: int) -> bytes:
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, 6, 0, 0, 0])
    )
    pixels = b"".join(b"\x00" + bytes(width * 4) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(pixels))
        + _png_chunk(b"IEND", b"")
    )


def test_png_validator_accepts_expected_signature_and_dimensions(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2))

    module._validate_png(str(png), expected_size=(4, 2))


def test_png_validator_rejects_wrong_dimensions(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2))

    with pytest.raises(RuntimeError, match="expected 8x4"):
        module._validate_png(str(png), expected_size=(8, 4))


def test_png_validator_rejects_truncated_png(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2)[:24])

    with pytest.raises(RuntimeError, match="invalid PNG"):
        module._validate_png(str(png), expected_size=(4, 2))
