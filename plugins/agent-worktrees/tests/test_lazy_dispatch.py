"""Validation for the agent-cli-lazy-dispatch effort's Phase 1 fast path.

Covers the Phase 1 Validation Plan items not yet exercised at the time of
landing (ThomasMichon/copilot-extensions#3309/#3312/#3313):

- The `_LAZY_DISPATCH_TABLE` (command -> (module, handler_attr)) cannot go
  stale relative to the real argparse registrations: regenerate it via the
  same introspection method documented in the table's own comment and diff.
- A fast-tracked command's dispatch never calls `build_parser()` (the whole
  point of the fast path); a non-fast-tracked command still does.
- `--help` for a fast-tracked command produces output identical to what the
  full `build_parser()` would have produced for that same subcommand.

Phase 1b (decoupling the `_core()` cross-module helper cluster) adds:

- `_CLUSTER_FREE_MODULES` cannot go stale relative to the real `_core()`
  call-shapes in every dispatch-table module: regenerate it via the
  AST-based scanner in `_core_cluster_scan.py` and diff.
- A cluster-free fast-tracked command genuinely never triggers
  `_ensure_cluster_loaded()`; a not-yet-decoupled one still does (both must
  hold, or the differentiation this Phase adds is untested/vacuous).
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m

sys.path.insert(0, str(Path(__file__).parent))
import _core_cluster_scan  # noqa: E402 -- must follow the sys.path insert above

# Every module build_parser() delegates a subparser to (see build_parser()'s
# own `*.add_parsers(sub)` / `pane_lifecycle.register_cli(sub)` call list).
_ADD_PARSERS_MODULES = [
    "resolve_cli", "finalize_cli", "pr_state_cli", "status_cli", "status_bar_cli",
    "status_updater_cli", "status_monitor_cli", "status_monitor_runtime",
    "pane_lifecycle", "handoff_cli", "list_cli", "claims_cli", "follow_ups_cli",
    "session_metadata_cli", "cleanup_gc_cli", "reap_cli", "reclaim_cli",
    "worktree_ops_cli", "picker_profiles_cli", "maintenance_cli",
    "installation_cli", "update_cli", "context_cli", "services_cli",
    "repos_cli", "related_cli", "git_cli", "pr_cli", "session_binding_cli",
    "session_inspection_cli", "session_tracking_cli", "worktree_status_audit",
]


def _regenerate_lazy_dispatch_table() -> dict[str, tuple[str, str]]:
    """Reproduce _LAZY_DISPATCH_TABLE via the method its own comment documents."""
    m._load_full_command_surface()  # populate COMMAND_MAP for the cross-check
    table: dict[str, str] = {}
    for modname in _ADD_PARSERS_MODULES:
        module = importlib.import_module(f"agent_worktrees.{modname}")
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        if modname == "pane_lifecycle":
            module.register_cli(sub)
        else:
            module.add_parsers(sub)
        for command in sub.choices:
            table[command] = modname

    final: dict[str, tuple[str, str]] = {}
    for command, modname in table.items():
        handler = m.COMMAND_MAP.get(command)
        if handler is None:
            continue
        handler_module = handler.__module__.rsplit(".", 1)[-1]
        if handler_module != modname:
            continue
        final[command] = (modname, handler.__name__)
    return final


def test_lazy_dispatch_table_matches_regenerated_scan():
    """_LAZY_DISPATCH_TABLE must not silently drift from the real registrations.

    Regenerates the table via the exact introspection method documented in
    _LAZY_DISPATCH_TABLE's own comment and diffs it against the checked-in
    version. A mismatch here means either a module gained/lost a command (or
    changed its handler), or the checked-in table was hand-edited out of
    sync -- both should fail loudly rather than silently under-cover a
    command that could otherwise be safely fast-tracked, or (worse)
    fast-track one that no longer resolves the way the table claims.
    """
    regenerated = _regenerate_lazy_dispatch_table()
    assert regenerated == m._LAZY_DISPATCH_TABLE


def test_fast_tracked_command_never_calls_build_parser(monkeypatch):
    """The whole point of the fast path: never build the ~110-entry tree."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_anchor_for_project", lambda name: None)
    calls = []
    real_build_parser = m.build_parser

    def _spy():
        calls.append(1)
        return real_build_parser()

    monkeypatch.setattr(m, "build_parser", _spy)
    handler_calls = []
    monkeypatch.setitem(m.COMMAND_MAP, "status", lambda args: handler_calls.append(1) or 0)

    rc = m.main(["--project", "demo", "status", "--json"])

    assert rc == 0
    assert handler_calls == [1], "the mocked handler must actually have run"
    assert calls == [], "a fast-tracked command must not call build_parser()"


def test_non_fast_tracked_command_still_calls_build_parser(monkeypatch):
    """A command NOT in _LAZY_DISPATCH_TABLE still uses the full, safe path."""
    assert "resolve" not in m._LAZY_DISPATCH_TABLE  # __main__-native handler

    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m, "_anchor_for_project", lambda name: None)
    calls = []
    real_build_parser = m.build_parser

    def _spy():
        calls.append(1)
        return real_build_parser()

    monkeypatch.setattr(m, "build_parser", _spy)
    handler_calls = []
    monkeypatch.setitem(m.COMMAND_MAP, "resolve", lambda args: handler_calls.append(1) or 0)

    rc = m.main(["--project", "demo", "resolve", "--dry-run"])

    assert rc == 0
    assert handler_calls == [1], "the mocked handler must actually have run"
    assert calls == [1], "a non-fast-tracked command must still build the full parser"


@pytest.mark.parametrize("command", ["get", "status", "list", "history-digest"])
def test_fast_path_help_matches_full_parser_help(command, capsys):
    """A fast-tracked subcommand's own --help text must be byte-identical
    whether produced via the minimal single-command parser or the full
    ~110-entry build_parser() tree -- argparse subparsers are independent, so
    this should hold structurally, not just by coincidence."""
    module_name, _ = m._LAZY_DISPATCH_TABLE[command]

    with pytest.raises(SystemExit):
        m._dispatch_lazy(command, [command, "--help"])
    fast_help = capsys.readouterr().out

    full_parser = m.build_parser()
    with pytest.raises(SystemExit):
        full_parser.parse_args([command, "--help"])
    full_help = capsys.readouterr().out

    assert fast_help == full_help


def test_cluster_free_modules_matches_regenerated_scan():
    """`_CLUSTER_FREE_MODULES` must not silently drift from the real
    `_core()` call-shapes in every dispatch-table module.

    Regenerates the set via the AST-based scanner (`_core_cluster_scan.py`,
    which walks the direct `_core().attr` chain, the assigned `core =
    _core(); ... core.attr` shape, and each module's own `_core_helper()`
    idiom, then classifies each accessed name against __main__.py's own
    module-level bindings -- and separately checks whether any resolved
    __main__-native function transitively touches a name bound only inside
    `_load_full_command_surface()`) and diffs it against the checked-in
    version. Mirrors `_LAZY_DISPATCH_TABLE`'s own drift-check test above; see
    this file's module docstring for why a regex-only scan isn't
    trustworthy for this, and `session_inspection_cli`'s exclusion (Journal,
    2026-09-23) for why single-level static analysis alone isn't either.
    """
    candidates = frozenset(modname for modname, _ in m._LAZY_DISPATCH_TABLE.values())
    regenerated = _core_cluster_scan.compute_cluster_free_modules(candidates)
    assert regenerated == m._CLUSTER_FREE_MODULES


@pytest.mark.parametrize("command", ["status", "claims", "reclaim"])
def test_cluster_free_command_skips_cluster_load(command, monkeypatch):
    """A `_CLUSTER_FREE_MODULES` command must genuinely never trigger
    `_ensure_cluster_loaded()` -- the actual mechanism Phase 1b adds, not
    just the classification `_CLUSTER_FREE_MODULES` itself lists."""
    module_name, _ = m._LAZY_DISPATCH_TABLE[command]
    assert module_name in m._CLUSTER_FREE_MODULES

    calls = []
    monkeypatch.setattr(m, "_ensure_cluster_loaded", lambda: calls.append(1))
    monkeypatch.setattr(m, "_load_full_command_surface", lambda: calls.append(1))

    with pytest.raises(SystemExit):
        m._dispatch_lazy(command, [command, "--help"])

    assert calls == [], f"{command!r} ({module_name}) must not load the cluster"


def _deferred_only_global_names() -> frozenset[str]:
    """Every name bound ONLY inside `_load_full_command_surface()` -- i.e.
    absent from `__main__`'s module namespace until that function runs at
    least once. Used to force a genuinely fresh "surface never loaded"
    state for a single test, since flipping `_FULL_SURFACE_LOADED`/
    `_CLUSTER_LOADED` back to `False` alone doesn't undo the fact that an
    EARLIER test in this same process already called
    `_load_full_command_surface()` for real and left these names bound in
    `vars(m)` -- Copilot review correctly flagged that this made the
    original version of the test below unable to actually prove anything
    (a handler that incorrectly depends on the cluster could still pass
    because the deferred globals were already sitting there from an earlier
    test's real load, not because this change's own gating logic worked).
    """
    import ast

    main_text = _core_cluster_scan.MAIN_PATH.read_text(encoding="utf-8")
    main_tree = ast.parse(main_text, filename=str(_core_cluster_scan.MAIN_PATH))
    func_start = func_end = -1
    for node in ast.walk(main_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_full_command_surface":
            func_start, func_end = node.lineno, node.end_lineno
    _cheap, heavy_owned, heavy_native = _core_cluster_scan.classify_main_names(main_tree, func_start, func_end)
    return frozenset(heavy_owned.keys()) | frozenset(heavy_native)


@pytest.mark.parametrize(
    "command, argv",
    [
        ("status", ["status", "--json"]),
        ("claims", ["claims", "--json"]),
        ("status-context", ["status-context"]),
        ("status-segment", ["status-segment", "--json"]),
        ("profiles", ["profiles", "get", "--json"]),
        ("follow-ups", ["follow-ups", "--json"]),
        ("session-role", ["session-role", "--json"]),
        ("history-digest", ["history-digest"]),
        ("pr-status", ["pr-status", "--json"]),
        ("pre-launch", ["pre-launch"]),
        ("reconcile-plugins", ["reconcile-plugins", "--peek"]),
        ("worktree-status-audit", ["worktree-status-audit", "--sample", "1", "--no-log", "--seed", "1"]),
        ("machine-context", ["machine-context"]),
        ("list-sessions", ["list-sessions"]),
        ("reconcile-sessions", ["reconcile-sessions"]),
    ],
)
def test_cluster_free_command_handler_body_runs_without_cluster(command, argv, monkeypatch, capsys):
    """Actually RUN each cluster-free command's real handler (not just
    `--help`, which only builds/prints the parser and never executes the
    handler body at all -- Copilot review correctly flagged that the
    original version of this test suite never caught this), in a state
    that genuinely simulates the cluster never having loaded in this
    process -- not just a state where the loader functions are stubbed out
    while the deferred globals they'd populate are already sitting in
    `vars(m)` from an earlier test's real load (Copilot review's second,
    equally valid finding: `test_lazy_dispatch_table_matches_regenerated_
    scan` above calls `_load_full_command_surface()` for real, so a naive
    version of this test could pass for the wrong reason).

    A prior version of this change also marked `session_inspection_cli`
    cluster-free; its `session-lifecycle` handler transitively referenced
    `cmd_register_session`/`_aw_runtime_home` -- names bound only inside
    `_load_full_command_surface()` -- and raised a bare `NameError` at
    runtime once the blanket cluster load was skipped. That class of bug is
    invisible to a scanner that only inspects the CLI submodule's own
    `_core()` accesses (see `_core_cluster_scan.find_transitively_unsafe_
    functions`); the only fully reliable check is running the real handler.
    A `RuntimeError` about no active project being resolved is expected and
    fine here (`_dispatch_lazy` bypasses `main()`'s own project-resolution
    step) -- what this asserts is the ABSENCE of `NameError`/`AttributeError`
    and that the cluster never loads.
    """
    module_name, _ = m._LAZY_DISPATCH_TABLE[command]
    assert module_name in m._CLUSTER_FREE_MODULES

    # Force a genuinely fresh "never loaded" state: strip every deferred-only
    # global an earlier test's real _load_full_command_surface() call may
    # have already bound, and reset both loaded-flags. monkeypatch restores
    # all of this automatically at teardown.
    for name in _deferred_only_global_names():
        monkeypatch.delitem(m.__dict__, name, raising=False)
    monkeypatch.setattr(m, "_FULL_SURFACE_LOADED", False, raising=False)
    monkeypatch.setattr(m, "_CLUSTER_LOADED", False, raising=False)

    calls = []
    monkeypatch.setattr(m, "_ensure_cluster_loaded", lambda: calls.append(1))
    monkeypatch.setattr(m, "_load_full_command_surface", lambda: calls.append(1))

    if command == "worktree-status-audit":
        # Copilot review: this generic smoke test must not start a real
        # detached status-monitor process when none is already live --
        # isolate it exactly like the dedicated monitor-enabled test does.
        from agent_worktrees import status_monitor_runtime

        monkeypatch.setattr(status_monitor_runtime.subprocess, "Popen", lambda *a, **k: None)

    try:
        rc = m._dispatch_lazy(command, argv)
        assert isinstance(rc, int)
        # Some handlers (notably session lifecycle-style ones) swallow an
        # internal exception into a diagnostic string rather than letting it
        # propagate -- catch that class of miss too, not just a raised
        # NameError/AttributeError.
        out = capsys.readouterr().out
        assert "NameError" not in out and "is not defined" not in out, (
            f"{command!r} ({module_name}) swallowed a NameError into its own "
            "output instead of raising it"
        )
    except RuntimeError as exc:
        assert "No active project could be resolved" in str(exc)
    except (NameError, AttributeError):
        raise AssertionError(
            f"{command!r} ({module_name}) hit a transitive dependency on a "
            "deferred global that _CLUSTER_FREE_MODULES incorrectly assumed safe"
        )

    assert calls == [], f"{command!r} ({module_name}) must not load the cluster"


def test_worktree_status_audit_monitor_enabled_path_skips_cluster(monkeypatch):
    """Copilot review on #3473: `worktree_status_audit`'s handler reaches
    `status_monitor_runtime._ensure_status_monitor()`, whose OWN body (when
    the resident monitor is enabled, the default) calls
    `_spawn_detached()` -> `_background_environment()`/`_runtime_superseded()`
    -- both owned by `status_updater_cli`. The handler-body test above
    exercises this with `--no-log` but doesn't force the monitor-enabled
    branch specifically; this test does, forcing `_status_monitor_enabled`
    True and letting the real `_ensure_status_monitor()` run end-to-end."""
    from agent_worktrees import status_monitor_runtime

    module_name, _ = m._LAZY_DISPATCH_TABLE["worktree-status-audit"]
    assert module_name in m._CLUSTER_FREE_MODULES

    for name in _deferred_only_global_names():
        monkeypatch.delitem(m.__dict__, name, raising=False)
    monkeypatch.setattr(m, "_FULL_SURFACE_LOADED", False, raising=False)
    monkeypatch.setattr(m, "_CLUSTER_LOADED", False, raising=False)

    calls = []
    monkeypatch.setattr(m, "_ensure_cluster_loaded", lambda: calls.append(1))
    monkeypatch.setattr(m, "_load_full_command_surface", lambda: calls.append(1))
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    # Exercise _spawn_detached()'s own body (the env=/kwargs= construction
    # that reaches _background_environment()/windowless_daemon_kwargs())
    # without actually spawning a real background process -- Copilot review
    # correctly flagged that this test would otherwise start a genuine
    # detached status-monitor and mutate host state when no monitor is
    # already live.
    monkeypatch.setattr(status_monitor_runtime.subprocess, "Popen", lambda *a, **k: None)

    result = status_monitor_runtime._ensure_status_monitor()
    assert isinstance(result, bool)
    assert calls == [], "the monitor-enabled path must not load the cluster"


@pytest.mark.parametrize("command", ["cleanup", "reap-sessions", "session-lifecycle"])
def test_not_yet_decoupled_command_still_loads_cluster(command, monkeypatch):
    """A command whose module is NOT in `_CLUSTER_FREE_MODULES` must still
    load the cluster -- the safe, unchanged Phase 1 behavior. Proves the
    differentiation `_dispatch_lazy()` now makes is real (not vacuously
    always-skip), and that a not-yet-decoupled command takes on zero
    incremental risk from Phase 1b. `session-lifecycle` is a deliberate
    regression guard: it was briefly (incorrectly) cluster-free earlier in
    this change -- see the previous test's own docstring. `cleanup`/
    `reap-sessions` replace Stage B's `get`/`worktree-lineage` examples,
    which Stage C decoupled (`context_cli`/`session_tracking_cli`) -- both
    still transitively depend on a deferred-only global reached through a
    `cleanup_gc_cli`/`reap_cli` function itself (see the effort's Journal)."""
    module_name, _ = m._LAZY_DISPATCH_TABLE[command]
    assert module_name not in m._CLUSTER_FREE_MODULES

    calls = []
    monkeypatch.setattr(m, "_ensure_cluster_loaded", lambda: calls.append(1))

    with pytest.raises(SystemExit):
        m._dispatch_lazy(command, [command, "--help"])

    assert calls == [1], f"{command!r} ({module_name}) must still load the cluster"
