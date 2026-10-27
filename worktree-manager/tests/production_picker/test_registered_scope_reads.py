"""All reads use admitted identities, bounded requests and explicit partial evidence."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from test_picker_tui import _fixture_source
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.engine import PickerScreen
from worktree_manager.production_picker.picker_tui.engine import PickerApp
from worktree_manager.production_picker.picker_tui.engine_pivot_actions import PickerScreenPivotActionsMixin


def pivot(*, machine=True, scope="machine", stream=False, subscribe=False):
    return pivots.parse_manifest({
        "label": "Fixture tasks", "scope": scope,
        "list": ["fixture", *(["--machine", "{machine}"] if machine else [])],
        "entry": {"id": "id", "title": "title"},
        "stream": stream, "subscribe": subscribe,
    }, name="fixture", source_path="fixture")


def engine(runtime, reg):
    result = PickerScreen(_fixture_source(), live=False)
    result.setup_sync_for_tests()
    result.pivots = [{"kind": "registered", "label": reg.label, "pivot": reg}]
    result.htab = 0
    result.machine_idx = 0
    result.machines = [
        ("All", None, None, True),
        ("Display A Win", "host-a", "Win", True),
        ("Display A WSL", "host-a", "WSL", True),
        ("Display B Win", "host-b", "Win", False),
        ("Provider", None, None, True),
    ]
    result.source_tabs = [
        {}, {"machine": "host-a", "physical_machine": "Display A", "source_kind": "machine-ssh"},
        {"machine": "host-a", "physical_machine": "Display A", "source_kind": "machine-ssh"},
        {"machine": "host-b", "physical_machine": "Display B", "source_kind": "machine-ssh"},
        {"source_kind": "container"},
    ]
    result.machine_state = lambda index: pytest.fail("Worktrees read status is not scope admission")
    result._machine_key_map = lambda: {}
    result._pivot_runtime = lambda _reg: runtime
    return result


def test_all_ensures_unvisited_canonical_hosts_even_when_worktree_reads_failed():
    calls = []
    states = {
        "host-a": ("ready", [{"id": "same", "title": "A"}], ""),
        "host-b": ("ready", [{"id": "same", "title": "B"}], ""),
    }
    runtime = SimpleNamespace(ensure_many=lambda scopes: calls.append(scopes), get=states.__getitem__)
    screen = engine(runtime, pivot())
    screen._machine_key_map = lambda: pytest.fail("canonical descriptors need no further discovery")
    state, rows, error = screen._task_state()
    assert state == "ready" and not error
    assert calls == [("host-a", "host-b")]
    assert [row["_manager_scope"] for row in rows] == ["host-a", "host-b"]
    assert len({row["_manager_row_key"] for row in rows}) == 2
    context = PickerScreenPivotActionsMixin._task_action_ctx(screen, screen._reg_pivot(), rows[1])
    assert context["machine"] == "host-b" and context["task_id"] == "same"
    assert "_manager_scope" not in states["host-a"][1][0]


@pytest.mark.parametrize("scope,machine", [("account", True), ("machine", False)])
def test_global_or_unscoped_provider_runs_once_without_inventing_machine_flags(scope, machine):
    calls = []
    runtime = SimpleNamespace(
        ensure_many=lambda scopes: calls.append(scopes),
        get=lambda scope: ("ready", [{"id": "one"}], ""),
    )
    screen = engine(runtime, pivot(machine=machine, scope=scope))
    assert screen._task_state()[0] == "ready"
    assert calls == [("",)]


def test_distinct_declared_guest_identity_is_not_collapsed_to_physical_display():
    calls = []
    runtime = SimpleNamespace(
        ensure_many=lambda scopes: calls.append(scopes),
        get=lambda scope: ("ready", [], ""),
    )
    screen = engine(runtime, pivot())
    screen.machines[2] = ("Display A WSL", "host-a-wsl", "WSL", True)
    screen.source_tabs[2]["machine"] = "host-a-wsl"
    screen._task_state()
    assert calls == [("host-a", "host-a-wsl", "host-b")]


def test_all_partial_failure_retains_rows_and_scoped_focus():
    states = {
        "host-a": ("ready", [{"id": "a"}], ""),
        "host-b": ("ready", [{"id": "b"}], ""),
    }
    runtime = SimpleNamespace(ensure_many=lambda scopes: None, get=states.__getitem__)
    screen = engine(runtime, pivot())
    screen._task_rows()
    screen.sel = ("T", 1)
    states["host-a"] = ("error", [], "declared transport unavailable")
    state, rows, error = screen._task_state()
    assert state == "error" and rows[0]["id"] == "b" and "host-a" in error
    screen._task_rows()
    assert screen.sel == ("T", 0)
    summary = screen._task_summary()
    assert "available/cached" in summary["banner_text"]
    assert summary["_manager_scopes"][0]["state"] == "error"


def test_provider_rows_cannot_supply_manager_scope_custody():
    runtime = SimpleNamespace(
        ensure_many=lambda scopes: None,
        get=lambda scope: ("ready", [{"id": "one", "_manager_scope": "foreign"}], ""),
    )
    screen = engine(runtime, pivot())
    assert [row["_manager_scope"] for row in screen._task_state()[1]] == ["host-a", "host-b"]


def test_aggregate_scope_is_visible_in_rows_and_action_header():
    from worktree_manager.production_picker.picker_tui.engine_dialogs import TaskMenuScreen
    runtime = SimpleNamespace(
        ensure_many=lambda scopes: None,
        get=lambda scope: ("ready", [{"id": "one", "title": "Example"}], ""),
    )
    screen = engine(runtime, pivot())
    row = screen._task_state()[1][1]
    reg = screen._reg_pivot()
    assert "source: host-b" in screen.tasks_view._column_subtitle(reg, row, 80).plain
    assert "source: host-b" in TaskMenuScreen(reg, row, [])._header().plain


def test_actions_from_a_retained_failed_scope_refuse_until_fresh_read():
    runtime = SimpleNamespace(get=lambda scope: ("error", [{"id": "one"}], "transport unavailable"))
    screen = engine(runtime, pivot())
    screen._internal_pivot_action = lambda *args: pytest.fail("must not execute stale action")
    action = SimpleNamespace(card=None)
    PickerScreenPivotActionsMixin._run_task_action(
        screen, screen._reg_pivot(), action, {"id": "one", "_manager_scope": "host-b"},
    )
    assert "press r to retry" in screen.debug


@pytest.mark.parametrize("select_machine,scope", [(True, "machine"), (False, "machine"), (True, "account")])
def test_individual_and_shared_rows_retain_read_custody_after_menu_open(select_machine, scope):
    state = {"value": "ready"}
    runtime = SimpleNamespace(
        ensure_many=lambda scopes: None,
        get=lambda key: (state["value"], [{"id": "selected"}], "failed" if state["value"] == "error" else ""),
    )
    reg = pivot(machine=select_machine, scope=scope)
    screen = engine(runtime, reg)
    screen.machine_idx = 3
    retained = screen._task_state()[1][0]
    assert retained["_manager_scope"] == ("host-b" if select_machine and scope == "machine" else "")
    state["value"] = "error"
    screen._internal_pivot_action = lambda *args: pytest.fail("retained row cannot execute after source failure")
    screen._run_task_action(reg, SimpleNamespace(card=None), retained)
    assert "press r to retry" in screen.debug

def test_r_refreshes_only_active_registered_machine_not_worktree_loader():
    refreshed = []
    runtime = SimpleNamespace(
        repoll=refreshed.append, ensure=lambda scope: None,
        get=lambda scope: ("ready", [], ""),
    )
    screen = engine(runtime, pivot())
    screen.machine_idx = 3
    screen._start_setup_reload_worker = lambda: pytest.fail("unrelated source reload")
    screen._dispatch_key("r")
    assert refreshed == ["host-b"]
    refreshed.clear()
    screen.machine_idx = 0
    screen._dispatch_key("r")
    assert refreshed == ["host-a", "host-b"]


def test_automatic_polling_preserves_healthy_held_channels(monkeypatch):
    from worktree_manager.production_picker.picker_tui import engine as engine_module

    reg = pivot(stream=True, subscribe=True)
    runtime = tasks.RegisteredPivotRuntime(reg)
    runtime._subscribe_live = {"host-a": True, "host-b": True}
    runtime._schedule_list = lambda *args, **kwargs: pytest.fail("healthy subscription restarted")
    runtime.refresh_read = lambda *args: pytest.fail("automatic poll forced an explicit reload")
    screen = engine(runtime, reg)
    screen._last_pivot_poll = 0
    monkeypatch.setattr(engine_module, "POLL_SECS", .001)
    for _ in range(3):
        screen._last_pivot_poll = 0
        screen._maybe_repoll_pivot()
    assert runtime._subscribe_live == {"host-a": True, "host-b": True}


def test_all_reload_returns_before_child_retirement_and_coalesces_replacements(monkeypatch):
    runtime = tasks.RegisteredPivotRuntime(pivot(stream=True, subscribe=True))
    screen = engine(runtime, runtime.pivot)
    release = threading.Event()
    entered, completed = set(), []
    input_thread = threading.get_ident()
    runtime._procs = [SimpleNamespace(_picker_scope=scope) for scope in ("host-a", "host-b")]
    originals = tuple(runtime._procs)
    runtime._cache = {
        scope: ("ready", [{"id": "old"}], "") for scope in ("host-a", "host-b")
    }

    def retire(proc):
        assert threading.get_ident() != input_thread or runtime._closed.is_set()
        entered.add(proc._picker_scope)
        assert release.wait(3)
        with runtime._procs_lock:
            if proc in runtime._procs:
                runtime._procs.remove(proc)

    def replacement(machine, generation):
        completed.append(machine)
        assert release.is_set()
        assert not any(proc._picker_scope == machine for proc in runtime._procs)
        runtime._finish(machine, ("ready", [{"id": "fresh"}], ""), {})

    monkeypatch.setattr(tasks, "_kill_proc_tree", retire)
    runtime._run_list = replacement
    try:
        started = time.monotonic()
        screen._dispatch_key("r")
        assert time.monotonic() - started < 1
        wait(lambda: len(entered) == 2)
        assert len(runtime._scope_retirements) == 2
        for _ in range(5):
            screen._dispatch_key("r")
        assert not completed and tuple(runtime._procs) == originals
        assert all(runtime.get(scope)[1] == [{"id": "old"}] for scope in entered)
        release.set()
        wait(lambda: len(completed) == 2)
        wait(lambda: not runtime._scope_retirements and not runtime._inflight)
        assert sorted(completed) == ["host-a", "host-b"]
    finally:
        release.set()
        runtime.close()


def test_reload_worker_start_failure_remains_explicit(monkeypatch):
    from worktree_manager.production_picker.picker_tui import pivot_requests

    runtime = tasks.RegisteredPivotRuntime(pivot(stream=True, subscribe=True))
    runtime._procs = [SimpleNamespace(_picker_scope="a")]

    def failed():
        raise RuntimeError("fixture retirement worker unavailable")

    monkeypatch.setattr(pivot_requests, "threading", SimpleNamespace(
        Thread=lambda **kwargs: SimpleNamespace(start=failed),
    ))
    runtime.refresh_read("a")
    assert runtime.get("a")[0] == "error"
    assert "retirement worker unavailable" in runtime.get("a")[2]
    assert not runtime._scope_retirements and not runtime._refresh_pending


def test_spawn_that_finishes_after_scope_reload_retires_only_its_captured_child(monkeypatch):
    runtime = tasks.RegisteredPivotRuntime(pivot(stream=True, subscribe=True))
    generation = (runtime._gen, 0, object())
    runtime._active_requests["a"] = generation
    runtime._request_context.machine = "a"
    runtime._request_context.generation = generation
    captured = SimpleNamespace()
    other = SimpleNamespace(_picker_scope="b")
    runtime._procs.append(other)
    killed = []

    def spawn(*args, **kwargs):
        runtime._scope_generations["a"] = 1
        return captured, None

    monkeypatch.setattr(tasks, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(tasks, "_kill_proc_tree", killed.append)
    assert runtime._spawn_stream(["fixture"]) is captured
    assert killed == [captured]
    assert other in runtime._procs


def test_legacy_configuration_read_owns_its_children_without_changing_mutation_transport(monkeypatch):
    calls = []
    result = SimpleNamespace(returncode=0, stdout="fixture status", stderr="")
    monkeypatch.setattr(tasks, "run_caller_owned", lambda argv, **kwargs: (
        calls.append(("read", argv, kwargs)) or result
    ))
    monkeypatch.setattr(tasks.subprocess, "run", lambda argv, **kwargs: (
        calls.append(("mutation", argv, kwargs)) or result
    ))
    action = SimpleNamespace(run=("fixture",), label="Fixture configuration")
    assert tasks.run_config_section(action, {}) == (True, "fixture status")
    assert calls[0][0] == "read" and calls[0][2]["timeout"] == tasks.ACTION_TIMEOUT
    assert tasks.run_config_section(action, {}, fields={}) == (True, "fixture status")
    assert calls[1][0] == "mutation"


def test_legacy_configuration_timeout_explains_the_read_not_its_launcher_argv(monkeypatch):
    import subprocess

    def expired(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(tasks, "run_caller_owned", expired)
    action = SimpleNamespace(run=("fixture",), label="Fixture configuration")
    ok, message = tasks.run_config_section(action, {})
    assert not ok and "Fixture configuration read timed out" in message
    assert "Command [" not in message


def wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "provider request did not settle"


def test_startup_fanout_is_bounded_and_cached_failures_do_not_retry_per_frame():
    runtime = tasks.RegisteredPivotRuntime(pivot())
    entered, release = [], threading.Event()
    lock = threading.Lock()

    def execute(context):
        with lock:
            entered.append(context["machine"])
        assert release.wait(3)
        if context["machine"] == "bad":
            raise ValueError("malformed provider shape")
        return "ready", [{"id": context["machine"]}], "", {}

    runtime._exec_list = execute
    try:
        runtime.ensure_many(["a", "b", "c", "d", "e", "bad"])
        wait(lambda: len(entered) == 4)
        assert runtime.get("e")[0] == "loading"
        release.set()
        wait(lambda: not runtime._inflight)
        assert runtime.get("bad")[0] == "error"
        assert "malformed provider shape" in runtime.get("bad")[2]
        for _ in range(20):
            runtime.ensure_many(["a", "b", "c", "d", "e", "bad"])
        assert len(entered) == 6
    finally:
        release.set()
        runtime.close()


def test_scope_invalidation_does_not_drop_another_hosts_inflight_result():
    runtime = tasks.RegisteredPivotRuntime(pivot())
    release = threading.Event()
    entered = set()

    def execute(context):
        entered.add(context["machine"])
        assert release.wait(3)
        return "ready", [{"id": context["machine"]}], "", {}

    runtime._exec_list = execute
    try:
        runtime.ensure_many(["a", "b"])
        wait(lambda: len(entered) == 2)
        runtime.invalidate("a")
        release.set()
        wait(lambda: not runtime._inflight)
        assert runtime.get("a")[0] == "idle"
        assert runtime.get("b")[0] == "ready"
    finally:
        release.set()
        runtime.close()


def test_invalidation_reserves_scope_before_retirement_capture(monkeypatch):
    runtime = tasks.RegisteredPivotRuntime(pivot(stream=True, subscribe=True))
    old = SimpleNamespace(_picker_scope="a")
    runtime._procs = [old]
    runtime._cache["a"] = ("ready", [{"id": "old"}], "")
    starts, retired = [], []
    stop = runtime._stop_scope_read
    monkeypatch.setattr(runtime, "_start_pending_lists", lambda: starts.append(True))
    monkeypatch.setattr(tasks, "_kill_proc_tree", retired.append)

    def interleave(machine, *, refetch):
        runtime.ensure(machine)
        assert not starts and not runtime._pending_requests
        stop(machine, refetch=refetch)

    monkeypatch.setattr(runtime, "_stop_scope_read", interleave)
    runtime.invalidate("a")
    wait(lambda: not runtime._scope_retirements)
    assert retired == [old]
    runtime.ensure("a")
    assert starts == [True] and runtime._pending_requests[0][0] == "a"


def test_invalidation_between_drop_publication_and_reconnect_cannot_reserve_stale_work(monkeypatch):
    runtime = tasks.RegisteredPivotRuntime(pivot(stream=True, subscribe=True))
    generation = (runtime._gen, 0, object())
    runtime._active_requests["a"] = generation
    runtime._inflight.add("a")
    runtime._request_context.generation = generation
    finish = runtime._finish

    def publish_then_invalidate(machine, result, summary):
        finish(machine, result, summary)
        runtime.invalidate(machine)

    monkeypatch.setattr(runtime, "_finish", publish_then_invalidate)
    runtime._handle_subscribe_drop("a", {}, ("error", [], "dropped"), {}, had_rows=False)
    assert not runtime._inflight and not runtime._reconnecting_scopes
    assert not runtime._subscribe_retries
    monkeypatch.setattr(runtime, "_finish", finish)
    runtime._exec_list = lambda context: ("ready", [{"id": "fresh"}], "", {})
    runtime._run_list = lambda machine, generation: runtime._finish(
        machine, ("ready", [{"id": "fresh"}], ""), {},
    )
    runtime.ensure("a")
    wait(lambda: runtime.get("a")[0] == "ready")
    runtime.close()


def test_removed_task_cannot_open_another_scopes_action_menu(monkeypatch):
    from dataclasses import replace
    states = {
        "host-a": ("ready", [{"id": "survives"}], ""),
        "host-b": ("ready", [{"id": "selected"}], ""),
    }
    runtime = SimpleNamespace(ensure_many=lambda scopes: None, get=states.__getitem__)
    screen = engine(runtime, replace(pivot(), actions=(SimpleNamespace(),)))
    screen._task_rows()
    screen.sel = ("T", 1)
    states["host-b"] = ("ready", [], "")
    opened = []
    monkeypatch.setattr(PickerScreen, "app", property(lambda self: SimpleNamespace(
        push_screen=lambda *args: opened.append(args),
    )))
    screen._open_task_menu()
    assert not opened and screen.sel == ("M", 0)


def test_all_row_result_refreshes_only_the_origin_scope_even_after_navigation():
    invalidated, ensured = [], []
    runtime = SimpleNamespace(
        get=lambda scope: ("ready", [], ""), invalidate=invalidated.append,
        ensure=ensured.append, run_resolved=lambda *args, **kwargs: (True, "evidence"),
    )
    screen = engine(runtime, pivot())
    action = SimpleNamespace(
        internal=None, form=None, card=None, progress=False,
        result_view="text", run=["fixture"], label="Inspect",
    )

    def command(label, work, refreshed, result_screen):
        assert work() == (True, "evidence")
        screen.machine_idx = 1
        refreshed()

    screen._run_command_result = command
    screen._run_task_action(
        screen._reg_pivot(), action, {"id": "selected", "_manager_scope": "host-b"},
    )
    assert invalidated == ensured == ["host-b"]


def test_unscoped_worker_prefetch_join_and_visible_inventory_share_one_cache(monkeypatch):
    from dataclasses import replace
    from worktree_manager.production_picker.picker_tui import engine as engine_module
    from worktree_manager.production_picker.picker_tui.pivot_manifest import WorkerSpec
    rows = [{"id": "container", "worktree_id": "worktree"}]
    ensured, polled = [], []
    runtime = SimpleNamespace(
        ensure=ensured.append, repoll=polled.append,
        get=lambda scope: ("ready", rows, "") if scope == "" else ("idle", [], ""),
    )
    reg = replace(pivot(machine=False), worker=WorkerSpec(worktree_field="worktree_id", label_field="id"))
    screen = engine(runtime, reg)
    screen.machine_idx = 3
    monkeypatch.setattr(engine_module, "POLL_SECS", 1)
    screen._maybe_refresh_worker_pivots()
    assert ensured == polled == [""]
    screen._pivot_runtimes = {reg.name: runtime}
    assert [row["id"] for row in screen._task_state()[1]] == ["container"]
    assert pivots.find_supervised_workers(screen.pivots, screen._pivot_runtimes, "host-b", "worktree")[0]["id"] == "container"


def test_prior_request_cleanup_cannot_release_a_new_refresh_in_the_same_scope_epoch():
    runtime = tasks.RegisteredPivotRuntime(pivot())
    first_published = threading.Event()
    release_first_cleanup = threading.Event()
    second_entered = threading.Event()
    release_second = threading.Event()
    calls, threads = [], []
    original = runtime._run_list

    def execute(context):
        calls.append(context["machine"])
        number = len(calls)
        if number == 2:
            second_entered.set()
            assert release_second.wait(3)
        return "ready", [{"id": str(number)}], "", {}

    def delay_cleanup(machine, generation):
        threads.append(threading.current_thread())
        first = len(threads) == 1
        original(machine, generation)
        if first:
            first_published.set()
            assert release_first_cleanup.wait(3)

    runtime._exec_list, runtime._run_list = execute, delay_cleanup
    try:
        runtime.ensure("a")
        wait(first_published.is_set)
        runtime.repoll("a")
        wait(second_entered.is_set)
        current = runtime._active_requests["a"]
        release_first_cleanup.set()
        threads[0].join(timeout=3)
        assert not threads[0].is_alive()
        assert runtime._active_requests["a"] == current
        assert "a" in runtime._inflight
        runtime.repoll("a")
        assert len(calls) == 2
        release_second.set()
        wait(lambda: not runtime._inflight)
        assert runtime.get("a")[1] == [{"id": "2"}]
    finally:
        release_first_cleanup.set()
        release_second.set()
        runtime.close()


def test_reserved_worker_does_not_start_provider_after_view_closes():
    runtime = tasks.RegisteredPivotRuntime(pivot())
    generation = (runtime._gen, 0, object())
    runtime._active_requests["a"] = generation
    runtime._inflight.add("a")
    runtime._exec_list = lambda context: pytest.fail("closed view must not start provider")
    runtime.close()
    runtime._run_scheduled_list("a", generation)
    assert "a" not in runtime._inflight


def test_worker_start_failure_settles_every_queued_scope(monkeypatch):
    from worktree_manager.production_picker.picker_tui import pivot_requests
    runtime = tasks.RegisteredPivotRuntime(pivot())
    def failed():
        raise RuntimeError("fixture worker admission refused")
    monkeypatch.setattr(pivot_requests, "threading", SimpleNamespace(
        Thread=lambda **kwargs: SimpleNamespace(start=failed),
    ))
    runtime.ensure_many(["a", "b", "c", "d", "e", "f"])
    assert not runtime._inflight and not runtime._pending_requests
    assert all(runtime.get(scope)[0] == "error" for scope in ("a", "b", "c", "d", "e", "f"))


def test_failed_refresh_keeps_prior_rows_and_explicit_r_can_recover():
    runtime = tasks.RegisteredPivotRuntime(pivot())
    runtime._cache["a"] = ("ready", [{"id": "retained"}], "")
    runtime._exec_list = lambda context: ("error", [], "provider missing", {})
    runtime.repoll("a")
    wait(lambda: not runtime._inflight)
    assert runtime.get("a") == ("error", [{"id": "retained"}], "provider missing")
    runtime._exec_list = lambda context: ("ready", [{"id": "fresh"}], "", {})
    runtime.repoll("a")
    wait(lambda: not runtime._inflight)
    assert runtime.get("a")[1] == [{"id": "fresh"}]


def test_one_shot_timeout_retires_owned_descendants_and_reports_reason(tmp_path, monkeypatch):
    command = (
        "import subprocess,sys,time; from agent_procutil import no_window_kwargs; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],**no_window_kwargs()); "
        "time.sleep(60)"
    )
    reg = pivots.parse_manifest({
        "label": "Fixture timeout", "list": [sys.executable, "-c", command, "x" * 500],
        "entry": {"id": "id", "title": "title"},
    }, name="timeout", source_path="fixture")
    monkeypatch.setattr(tasks, "LIST_TIMEOUT", 1.0)
    started = time.monotonic()
    state, rows, error, _summary = tasks.RegisteredPivotRuntime(reg)._exec_list({})
    assert state == "error" and not rows
    assert "timed out after 1s" in error and "press r" in error
    assert "x" * 100 not in error
    assert time.monotonic() - started < 6


@pytest.mark.parametrize("stream", [False, True])
def test_unexpected_parse_error_settles_cached_terminal_state(stream):
    runtime = tasks.RegisteredPivotRuntime(pivot(stream=stream))
    def invalid(*args):
        raise ValueError("invalid provider entry")
    if stream:
        runtime._run_list_stream = invalid
    else:
        runtime._exec_list = invalid
    runtime.ensure("a")
    wait(lambda: not runtime._inflight)
    assert runtime.get("a")[0] == "error"
    assert "invalid provider entry" in runtime.get("a")[2]


@pytest.mark.parametrize("frame", ["begin", "row", "done"])
def test_held_stream_deadline_requires_initial_commit_not_just_begin(frame, tmp_path, monkeypatch):
    import json
    timers = []
    received = threading.Event()

    class ControlledTimer:
        def __init__(self, interval, callback):
            self.interval, self.callback = interval, callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    parse = tasks._parse_ndjson_line

    def received_frame(raw):
        result = parse(raw)
        if result is not None:
            received.set()
        return result

    monkeypatch.setattr(tasks.threading, "Timer", ControlledTimer)
    monkeypatch.setattr(tasks, "_parse_ndjson_line", received_frame)
    message = {"type": frame}
    if frame == "row":
        message["entry"] = {"id": "one", "title": "Committed"}
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import json,time\n"
        f"print({json.dumps(message)!r}, flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    reg = pivots.parse_manifest({
        "label": "Fixture stream", "list": [sys.executable, str(provider)],
        "entry": {"id": "id", "title": "title"}, "stream": True, "subscribe": True,
    }, name="stream", source_path="fixture")
    monkeypatch.setattr(tasks, "STREAM_TIMEOUT", .5)
    runtime = tasks.RegisteredPivotRuntime(reg)
    try:
        runtime.ensure("a")
        wait(lambda: received.is_set() or runtime.get("a")[0] == "error", timeout=20)
        assert received.is_set(), runtime.get("a")
        assert len(timers) == 1 and timers[0].interval == .5
        if frame == "begin":
            timers[0].callback()
            wait(lambda: runtime.get("a")[0] == "error", timeout=4)
            assert "no completed initial read" in runtime.get("a")[2]
            assert not runtime._subscribe_retries
        else:
            wait(lambda: runtime.get("a")[0] == "ready")
            assert timers[0].cancelled
            # A timer callback already admitted before cancellation must not kill a committed read.
            timers[0].callback()
            assert runtime.get("a")[0] == "ready"
            assert runtime._procs and runtime._procs[0].poll() is None
    finally:
        runtime.close()


def test_real_held_stream_deadline_stops_a_provider_without_any_publication(tmp_path, monkeypatch):
    provider = tmp_path / "provider.py"
    provider.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    reg = pivots.parse_manifest({
        "label": "Fixture stalled", "list": [sys.executable, str(provider)],
        "entry": {"id": "id", "title": "title"}, "stream": True, "subscribe": True,
    }, name="stalled", source_path="fixture")
    monkeypatch.setattr(tasks, "STREAM_TIMEOUT", .5)
    runtime = tasks.RegisteredPivotRuntime(reg)
    try:
        runtime.ensure("a")
        wait(lambda: runtime.get("a")[0] == "error", timeout=4)
        assert "no completed initial read" in runtime.get("a")[2]
        assert not runtime._subscribe_retries and not runtime._procs
    finally:
        runtime.close()


def test_held_stream_first_commits_release_bounded_startup_slots(tmp_path):
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import json,sys,time\n"
        "print(json.dumps({'type':'row','entry':{'id':sys.argv[1]}}), flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    reg = pivots.parse_manifest({
        "label": "Fixture held", "list": [sys.executable, str(provider), "{machine}"],
        "entry": {"id": "id", "title": "title"}, "stream": True, "subscribe": True,
    }, name="held", source_path="fixture")
    runtime = tasks.RegisteredPivotRuntime(reg)
    scopes = ["a", "b", "c", "d", "e", "f"]
    try:
        runtime.ensure_many(scopes)
        wait(lambda: all(runtime.get(scope)[0] == "ready" for scope in scopes))
        assert len(runtime._procs) == 6
        assert not runtime._starting_scopes
    finally:
        runtime.close()


@pytest.mark.parametrize("operation", ["refresh_read", "invalidate"])
def test_explicit_source_reload_replaces_only_its_owned_held_channel(tmp_path, operation):
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import json,pathlib,sys,time\n"
        "marker=pathlib.Path(sys.argv[2])/sys.argv[1]\n"
        "attempt=int(marker.read_text())+1 if marker.exists() else 1\n"
        "marker.write_text(str(attempt))\n"
        "print(json.dumps({'type':'row','entry':{'id':f'{sys.argv[1]}-{attempt}'}}),flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    reg = pivots.parse_manifest({
        "label": "Fixture held", "list": [sys.executable, str(provider), "{machine}", str(tmp_path)],
        "entry": {"id": "id", "title": "title"}, "stream": True, "subscribe": True,
    }, name="held", source_path="fixture")
    runtime = tasks.RegisteredPivotRuntime(reg)
    try:
        runtime.ensure_many(["a", "b"])
        wait(lambda: all(runtime.get(scope)[0] == "ready" for scope in ("a", "b")))
        other = next(proc for proc in runtime._procs if proc._picker_scope == "b")
        getattr(runtime, operation)("a")
        if operation == "invalidate":
            wait(lambda: "a" not in runtime._inflight)
            runtime.ensure("a")
        wait(lambda: runtime.get("a")[1] == [{"id": "a-2"}])
        assert runtime.get("b")[1] == [{"id": "b-1"}]
        assert other.poll() is None
        assert len(runtime._procs) == 2
    finally:
        runtime.close()


def test_cancelled_reconnect_cannot_clear_the_explicit_reload_owner():
    runtime = tasks.RegisteredPivotRuntime(pivot(stream=True, subscribe=True))
    backoff_entered, release_backoff = threading.Event(), threading.Event()
    second_entered, release_second = threading.Event(), threading.Event()
    closed = threading.Event()
    calls, backoff_threads = [], []

    def backoff(timeout):
        backoff_threads.append(threading.current_thread())
        backoff_entered.set()
        assert release_backoff.wait(3)
        return closed.is_set()

    runtime._closed = SimpleNamespace(is_set=closed.is_set, set=closed.set, wait=backoff)

    def read(machine, generation):
        calls.append(machine)
        if len(calls) == 1:
            runtime._handle_subscribe_drop(
                machine, {"machine": machine}, ("error", [], "fixture disconnected"), {}, had_rows=False,
            )
        else:
            second_entered.set()
            assert release_second.wait(3)

    runtime._run_list = read
    try:
        runtime.ensure("a")
        wait(backoff_entered.is_set)
        runtime.refresh_read("a")
        wait(second_entered.is_set)
        current = runtime._active_requests["a"]
        release_backoff.set()
        backoff_threads[0].join(timeout=3)
        assert not backoff_threads[0].is_alive()
        assert runtime._active_requests["a"] == current and "a" in runtime._inflight
        assert len(calls) == 2
    finally:
        release_backoff.set()
        release_second.set()
        runtime.close()


def test_worktree_source_failure_is_not_a_blank_success_or_machine_offline():
    from worktree_manager.production_picker.picker_tui.source_read_notices import failed_worktree_reads
    screen = engine(SimpleNamespace(), pivot())
    screen.live = True
    screen.source_tabs[1]["source_id"] = "machine-ssh:host-a:win"
    screen.machine_state = lambda index: "failed" if index == 1 else "ready"
    screen.loader = SimpleNamespace(
        error_for_source=lambda identity: "initial current-read deadline exceeded",
    )
    assert failed_worktree_reads(screen) == [
        ("Display A Win", "machine-ssh:host-a:win", "initial current-read deadline exceeded"),
    ]
    screen.source_tabs[1]["local"] = True
    assert failed_worktree_reads(screen) == [
        ("Display A Win", "local execution (no SSH) / machine-ssh:host-a:win",
         "initial current-read deadline exceeded"),
    ]


def test_terminal_registered_error_footer_does_not_claim_global_loading():
    runtime = SimpleNamespace(
        ensure_many=lambda scopes: None,
        get=lambda scope: ("error", [], "provider unavailable"),
    )
    screen = engine(runtime, pivot())
    screen.debug = "loading"
    assert "read failed or incomplete" in screen.footer(180).plain
    assert "· loading " not in screen.footer(180).plain


def test_source_failure_and_recovery_repaint_native_empty_body(monkeypatch):
    from textual.widgets import OptionList
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    status = {"state": "ready", "reason": ""}

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 30)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            screen.live = True
            screen.data = []
            screen.machine_idx = 1
            screen.machine_state = lambda index: status["state"]
            screen.loader = SimpleNamespace(
                error=lambda machine, environment: status["reason"],
                counts=lambda: (0, 0, int(status["state"] == "failed")),
            )
            screen.refresh()
            await pilot.pause()
            native = screen.query_one("#nf-body-data", OptionList)
            def body():
                return "\n".join(
                    str(native.get_option_at_index(index).prompt)
                    for index in range(native.option_count)
                )
            assert "fixture initial current-read deadline" not in body()
            status.update(state="failed", reason="fixture initial current-read deadline")
            screen.refresh()
            await pilot.pause()
            assert "fixture initial current-read deadline" in body()
            assert "Reachability/liveness unknown" in body()
            status.update(state="ready", reason="")
            screen.refresh()
            await pilot.pause()
            assert "fixture initial current-read deadline" not in body()
            screen.live = False
            screen.loader = None
    asyncio.run(run())
