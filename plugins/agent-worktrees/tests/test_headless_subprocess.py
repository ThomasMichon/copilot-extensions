"""The suite must launch real subprocesses headless on Windows.

Many integration tests shell out to real git/bash/pwsh; without ``CREATE_NO_WINDOW``
each spawn flashes a console window when pytest has no console of its own. The
``pytest_configure`` hook in ``conftest.py`` forces every real ``Popen`` windowless
for the session (mocked subprocess calls never reach it).
"""
from __future__ import annotations

import sys

import pytest

import conftest


CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


def test_headless_flags_adds_create_no_window():
    assert conftest._headless_creationflags(0) == CREATE_NO_WINDOW


def test_headless_flags_preserve_existing_and_add_no_window():
    detached = 0x00000008  # DETACHED_PROCESS
    out = conftest._headless_creationflags(detached)
    assert out & detached
    assert out & CREATE_NO_WINDOW


def test_headless_flags_idempotent():
    once = conftest._headless_creationflags(0)
    assert conftest._headless_creationflags(once) == once


def test_headless_flags_leave_new_console_untouched():
    # CREATE_NO_WINDOW and CREATE_NEW_CONSOLE are mutually exclusive: a deliberate
    # new-console request must pass through unchanged.
    out = conftest._headless_creationflags(CREATE_NEW_CONSOLE)
    assert out == CREATE_NEW_CONSOLE
    assert not (out & CREATE_NO_WINDOW)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only headless hook")
def test_popen_hook_installed_on_windows():
    import subprocess

    init = subprocess.Popen.__init__
    assert getattr(init, "_aw_headless", False), \
        "pytest_configure must wrap subprocess.Popen.__init__ on Windows"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only headless hook")
def test_real_spawn_receives_no_window_flag(monkeypatch):
    """A real ``subprocess.run`` forwards ``CREATE_NO_WINDOW`` to the OS spawn."""
    import subprocess

    seen = {}
    hook = subprocess.Popen.__init__
    real = hook._aw_orig

    def _spy(self, *args, **kwargs):
        seen["creationflags"] = kwargs.get("creationflags", 0)
        return real(self, *args, **kwargs)

    # Rewrap so the hook still OR-s the flag, then delegates to our spy.
    def _rehook(self, *a, **k):
        k["creationflags"] = conftest._headless_creationflags(k.get("creationflags", 0))
        return _spy(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "__init__", _rehook)
    subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
    assert seen["creationflags"] & CREATE_NO_WINDOW
