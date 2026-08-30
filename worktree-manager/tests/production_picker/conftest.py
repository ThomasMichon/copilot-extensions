"""Test harness for the Manager-owned production Picker corpus."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from worktree_manager.production_picker._engine_runtime import ensure_engine_runtime


ensure_engine_runtime()


@pytest.fixture(autouse=True)
def _disable_resident_monitor_processes():
    key = "AGENT_WORKTREES_STATUS_MONITOR"
    prior = os.environ.get(key)
    os.environ[key] = "0"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


@pytest.fixture(autouse=True)
def _isolate_agent_worktrees_home(tmp_path_factory):
    fake_home = tmp_path_factory.mktemp("aw-home")
    (fake_home / ".agent-worktrees").mkdir(parents=True, exist_ok=True)
    saved_userprofile = os.environ.get("USERPROFILE")
    saved_home = os.environ.get("HOME")
    saved_agent_home = os.environ.get("AGENT_HOME")
    saved_path_home = pathlib.Path.__dict__.get("home")

    os.environ["USERPROFILE"] = str(fake_home)
    os.environ["HOME"] = str(fake_home)
    os.environ.pop("AGENT_HOME", None)
    pathlib.Path.home = classmethod(lambda cls: fake_home)
    try:
        yield fake_home
    finally:
        if saved_path_home is not None:
            pathlib.Path.home = saved_path_home
        for key, value in (
            ("USERPROFILE", saved_userprofile),
            ("HOME", saved_home),
            ("AGENT_HOME", saved_agent_home),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _isolate_pivots(tmp_path_factory):
    empty = tmp_path_factory.mktemp("empty-pivots")
    empty_plugins = tmp_path_factory.mktemp("empty-plugins")
    saved = os.environ.get("AGENT_WORKTREES_PIVOTS_DIR")
    saved_plugins = os.environ.get("AGENT_WORKTREES_PLUGINS_DIR")
    os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = str(empty)
    os.environ["AGENT_WORKTREES_PLUGINS_DIR"] = str(empty_plugins)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("AGENT_WORKTREES_PIVOTS_DIR", None)
        else:
            os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = saved
        if saved_plugins is None:
            os.environ.pop("AGENT_WORKTREES_PLUGINS_DIR", None)
        else:
            os.environ["AGENT_WORKTREES_PLUGINS_DIR"] = saved_plugins


@pytest.fixture(autouse=True)
def _reset_active_project():
    from agent_worktrees import config as engine_config

    saved = os.environ.get("WORKTREE_PROJECT")
    engine_config.set_active_project(None)
    os.environ.pop("WORKTREE_PROJECT", None)
    try:
        yield
    finally:
        engine_config.set_active_project(None)
        if saved is None:
            os.environ.pop("WORKTREE_PROJECT", None)
        else:
            os.environ["WORKTREE_PROJECT"] = saved


_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010


def pytest_configure(config):
    if sys.platform != "win32":
        return
    original = subprocess.Popen.__init__
    if getattr(original, "_wm_picker_headless", False):
        return

    def _headless_init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        if not flags & _CREATE_NEW_CONSOLE:
            kwargs["creationflags"] = flags | _CREATE_NO_WINDOW
        return original(self, *args, **kwargs)

    _headless_init._wm_picker_headless = True
    _headless_init._wm_picker_original = original
    subprocess.Popen.__init__ = _headless_init


def pytest_unconfigure(config):
    if sys.platform != "win32":
        return
    current = subprocess.Popen.__init__
    original = getattr(current, "_wm_picker_original", None)
    if original is not None:
        subprocess.Popen.__init__ = original
