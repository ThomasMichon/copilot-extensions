"""Regression tests for the process-group teardown guard (#1001).

Tearing down a spawned agent signals the child's process group. That is only
safe when the child leads its own group. If a spawn path forgets
``start_new_session`` the child inherits the bridge's process group, and a naive
``os.killpg(os.getpgid(pid), ...)`` would signal the **bridge itself** -- which
is exactly how stopping a remote/SSH session took the whole daemon down. These
tests pin ``safe_killpg``'s refusal to ever signal our own group.
"""

from __future__ import annotations

import os
import signal

from agent_bridge.procgroup import safe_killpg


def test_safe_killpg_refuses_own_group(monkeypatch):
    """If the child shares our process group, no group signal is sent."""
    own_pgid = os.getpgid(0)
    killed: list[tuple[int, int]] = []

    # Pretend the child's pgid resolves to OUR process group.
    monkeypatch.setattr(os, "getpgid", lambda pid: own_pgid)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    sent = safe_killpg(1234, signal.SIGTERM)

    assert sent is False
    assert killed == []  # never signaled our own group


def test_safe_killpg_signals_foreign_group(monkeypatch):
    """A child in its own group is signaled normally."""
    own_pgid = os.getpgid(0)
    child_pgid = own_pgid + 4321  # any group that is not ours
    killed: list[tuple[int, int]] = []

    # getpgid(0) must still report OUR group; the child resolves elsewhere.
    monkeypatch.setattr(
        os, "getpgid", lambda pid: own_pgid if pid == 0 else child_pgid
    )
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    sent = safe_killpg(1234, signal.SIGTERM)

    assert sent is True
    assert killed == [(child_pgid, signal.SIGTERM)]


def test_safe_killpg_handles_dead_pid(monkeypatch):
    """A vanished pid is reported as not-signaled rather than raising."""
    def _boom(pid):
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", _boom)
    monkeypatch.setattr(
        os, "killpg",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    assert safe_killpg(4242, signal.SIGTERM) is False
