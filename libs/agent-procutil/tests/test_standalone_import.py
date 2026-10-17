"""Minimal bundles and lifecycle loaders need only the short-lived helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import agent_procutil


@pytest.mark.parametrize("registered", [False, True], ids=["lifecycle-loader", "minimal-package"])
def test_short_lived_helpers_import_without_daemon_modules(tmp_path, monkeypatch, registered):
    package = tmp_path / "agent_procutil"
    package.mkdir()
    source = package / "__init__.py"
    source.write_bytes(Path(agent_procutil.__file__).read_bytes())
    name = "_standalone_procutil_fixture"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    if registered:
        monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert isinstance(module.no_window_flags(), int)
    assert callable(module.spawn_sync_in_kill_on_close_job)
    assert callable(module.spawn_daemon)
