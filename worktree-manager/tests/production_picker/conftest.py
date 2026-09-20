"""Test harness for the Manager-owned production Picker corpus."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import urllib.error

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
def _disable_manager_update_check():
    """Mounting a real PickerScreen (``app.run_test()``) triggers
    ``_poll_manager_update_state`` in ``_finish_mount``, which -- unlike the
    engine's own (file-only) update_state check -- makes a REAL network
    call the first time ``should_check()`` sees no/stale cache. Every test
    in this module must be network-free and deterministic (a capture test
    diffing two mounts byte-for-byte is not the place for a live GitHub
    fetch racing itself), so this pauses the check the same way an operator
    would via ``WORKTREE_NO_UPDATE=1``."""
    key = "WORKTREE_NO_UPDATE"
    prior = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


class _NetworkBlockedError(urllib.error.URLError):
    """Raised when a test in this suite tries to make a real outbound
    network call. Mock the call instead (e.g. monkeypatch
    ``self_install.fetch_remote_version``/``urllib.request.urlopen``, or
    inject a fake ``websocket.create_connection`` -- see
    ``test_ahp_provider.py``).

    Deliberately an :class:`urllib.error.URLError` (an :class:`OSError`
    subclass), not a plain ``RuntimeError``: production code that already
    tolerates a REAL network failure gracefully (e.g.
    ``discovery._from_remote``'s ``except (urllib.error.URLError,
    TimeoutError, ValueError, OSError)`` clause, falling back to an authored
    catalog) must keep doing so identically when this fixture blocks the
    attempt instead of a real DNS/connection failure -- a mismatched
    exception type broke that fallback the first time this guard was tried
    (see the Journal), turning an expected graceful degradation into a hard
    test failure unrelated to the network block itself."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@pytest.fixture(autouse=True)
def _block_real_network():
    """Hard backstop alongside ``_disable_manager_update_check`` above: even
    if a future change bypasses the ``WORKTREE_NO_UPDATE`` env-var gate (or
    adds a new network call this module doesn't yet know about), mounting a
    real ``PickerScreen`` in this test suite must never be able to reach the
    network for real. Patches the exact entry points this codebase's
    production code actually uses for outbound network I/O --
    ``urllib.request.urlopen`` (``self_install.fetch_remote_version``,
    ``discovery._from_remote``) and ``websocket.create_connection``
    (``ahp_provider``, already dependency-injected/mocked by every existing
    AHP test) -- rather than the low-level ``socket`` module, which is much
    easier to reason about and does not risk touching unrelated local IPC
    this test suite may rely on. Real local ``git``
    (``test_e2e_delivery.py``'s explicit, clearly-scoped e2e fetch against a
    *local* path) is unaffected either way.

    Deliberately does its own manual save/restore instead of taking the
    shared ``monkeypatch`` fixture: several other fixtures/tests in this
    module restore an env var (notably ``AGENT_WORKTREES_PLUGINS_DIR``, in
    ``_isolate_pivots`` below) via plain ``os.environ`` mutation in a
    ``finally`` block. Being the *first* fixture in this file to request
    ``monkeypatch`` would make pytest instantiate that shared object earlier
    than it otherwise would, which inverts its teardown order relative to
    those later, non-monkeypatch fixtures -- since monkeypatch then tears
    down *after* them, its own restore of an env var a test changed via
    ``monkeypatch.setenv`` silently clobbers ``_isolate_pivots``'s own
    cleanup, leaking a stale path into every later test in the session. This
    was observed breaking ``test_plugin_contracts.py`` when run as part of
    the full suite; see the Journal."""
    import urllib.request

    def _blocked_urlopen(*a, **kw):
        raise _NetworkBlockedError(
            "test tried to call urllib.request.urlopen for real")

    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _blocked_urlopen

    try:
        import websocket
    except ImportError:
        websocket = None

    original_create_connection = None
    if websocket is not None:
        def _blocked_create_connection(*a, **kw):
            raise _NetworkBlockedError(
                "test tried to call websocket.create_connection for real")

        original_create_connection = websocket.create_connection
        websocket.create_connection = _blocked_create_connection

    try:
        yield
    finally:
        urllib.request.urlopen = original_urlopen
        if websocket is not None:
            websocket.create_connection = original_create_connection


@pytest.fixture(autouse=True)
def _isolate_agent_worktrees_home(tmp_path_factory):
    fake_home = tmp_path_factory.mktemp("aw-home")
    (fake_home / ".agent-worktrees").mkdir(parents=True, exist_ok=True)
    saved_userprofile = os.environ.get("USERPROFILE")
    saved_home = os.environ.get("HOME")
    saved_agent_home = os.environ.get("AGENT_HOME")
    saved_wm_root = os.environ.get("WORKTREE_MANAGER_ROOT")
    saved_path_home = pathlib.Path.__dict__.get("home")

    os.environ["USERPROFILE"] = str(fake_home)
    os.environ["HOME"] = str(fake_home)
    os.environ.pop("AGENT_HOME", None)
    # self_install.default_root() (manager_update_check's status-file root,
    # among others) checks WORKTREE_MANAGER_ROOT BEFORE USERPROFILE/HOME --
    # left set from a real interactive shell (this machine's own
    # worktree-manager launch sets it), it silently escapes the isolated
    # fake home above and reads/writes the REAL ~/.worktree-manager, most
    # visibly a genuinely stale real update-check.json racing
    # test_capture_is_deterministic's two mounts against each other
    # (whichever one's queued _poll_manager_update_state callback happens to
    # run first sees it, the other doesn't -- a real state leak, not just a
    # network one). Redirect it alongside the fake home so every test is
    # fully sandboxed from this machine's actual installed state.
    os.environ["WORKTREE_MANAGER_ROOT"] = str(fake_home / ".worktree-manager")
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
            ("WORKTREE_MANAGER_ROOT", saved_wm_root),
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
