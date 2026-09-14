"""Regression tests for the pivot-action subprocess environment.

A registered pivot's ``list``/action command is a *different* plugin's CLI
(e.g. the picker shelling out to ``agent-dispatch``). The picker's own process
carries its own plugin-identity env vars (``COPILOT_PLUGIN_ROOT``,
``COPILOT_EXTENSIONS_CONTEXT``, ...); inheriting them unchanged makes the
child CLI's own installation-context self-check see a foreign plugin root and
reject the call with a spurious "payload context mismatch" -- even on a
freshly launched picker, since the mismatch comes from the picker's own
identity, not stale state. See ``tasks._child_process_env`` / its module
docstring.
"""

from __future__ import annotations

import sys

from agent_worktrees.picker_tui import tasks


class _Action:
    def __init__(self, run: tuple[str, ...]) -> None:
        self.run = run


def test_child_process_env_strips_plugin_identity_vars(monkeypatch):
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", r"C:\some\other\plugin")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")
    monkeypatch.setenv("COPILOT_PLUGIN_INSTALL_STAGED", "1")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", r"C:\staged")
    monkeypatch.setenv("PYTHONPATH", r"C:\other\pythonpath")
    monkeypatch.setenv("A_HARMLESS_VAR", "keep-me")

    env = tasks._child_process_env()

    for key in tasks._CHILD_ENV_UNSET:
        assert key not in env
    assert env["A_HARMLESS_VAR"] == "keep-me"


def _print_env_script(var: str) -> tuple[str, ...]:
    """An ``action.run``-shaped argv for a tiny Python child that prints
    whether ``var`` is present in its own environment -- an end-to-end probe
    of the *actual* subprocess environment, not just the constructed dict."""
    return (
        sys.executable,
        "-c",
        f"import os; print('SET' if os.environ.get({var!r}) else 'UNSET')",
    )


def test_run_config_section_does_not_leak_plugin_root(monkeypatch):
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", r"C:\some\other\plugin")
    action = _Action(_print_env_script("COPILOT_PLUGIN_ROOT"))

    ok, msg = tasks.run_config_section(action, {})

    assert ok is True
    assert msg.strip() == "UNSET"


def test_run_worktree_action_does_not_leak_plugin_root(monkeypatch):
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", r"C:\some\other\plugin")
    action = _Action(_print_env_script("COPILOT_PLUGIN_ROOT"))

    ok, msg = tasks.run_worktree_action(action, {})

    assert ok is True
    assert msg.strip() == "UNSET"


def test_spawn_stream_does_not_leak_extensions_context(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-receipt")
    runtime = tasks.RegisteredPivotRuntime.__new__(tasks.RegisteredPivotRuntime)
    runtime._closed = __import__("threading").Event()
    runtime._procs = []
    runtime._procs_lock = __import__("threading").Lock()

    argv = list(_print_env_script("COPILOT_EXTENSIONS_CONTEXT"))
    proc = runtime._spawn_stream(argv)
    try:
        out, _ = proc.communicate(timeout=10)
    finally:
        runtime.close()
    assert out.strip() == "UNSET"
