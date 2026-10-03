"""Unit tests for ``tasks.resolve_dynamic_options`` (Phase B item 3) -- the
bounded, never-raising subprocess helper a ``create_action`` field's
``options_command`` is resolved through, right before its modal opens."""

from __future__ import annotations

import sys

from worktree_manager.production_picker.picker_tui import tasks


def test_resolve_dynamic_options_success():
    argv = [sys.executable, "-c", "import json; print(json.dumps(['a', 'b']))"]
    assert tasks.resolve_dynamic_options(argv) == ["a", "b"]


def test_resolve_dynamic_options_empty_argv():
    assert tasks.resolve_dynamic_options([]) == []


def test_resolve_dynamic_options_nonzero_exit():
    argv = [sys.executable, "-c", "import sys; sys.exit(1)"]
    assert tasks.resolve_dynamic_options(argv) == []


def test_resolve_dynamic_options_not_found():
    assert tasks.resolve_dynamic_options(["definitely-not-a-real-command-xyz"]) == []


def test_resolve_dynamic_options_non_json_stdout():
    argv = [sys.executable, "-c", "print('not json')"]
    assert tasks.resolve_dynamic_options(argv) == []


def test_resolve_dynamic_options_non_array_json():
    argv = [sys.executable, "-c", "import json; print(json.dumps({'a': 1}))"]
    assert tasks.resolve_dynamic_options(argv) == []


def test_resolve_dynamic_options_non_string_items():
    argv = [sys.executable, "-c", "import json; print(json.dumps(['a', 1]))"]
    assert tasks.resolve_dynamic_options(argv) == []


def test_resolve_dynamic_options_timeout(monkeypatch):
    import subprocess

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(tasks.subprocess, "run", _boom)
    assert tasks.resolve_dynamic_options(["irrelevant"]) == []
