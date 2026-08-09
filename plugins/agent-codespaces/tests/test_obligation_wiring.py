"""Tests for the CodeSpace obligation settle-on-disconnect hook (Ph3b-wiring/2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_codespaces import __main__ as m
from agent_codespaces import cleanliness as cl


class _FakeManager:
    """Minimal ConnectionManager stub exposing exec_command for the probe."""

    def __init__(self, stdout="", exit_code=0, raises=False):
        self._stdout = stdout
        self._exit = exit_code
        self._raises = raises

    async def exec_command(self, name, command, timeout=None):
        if self._raises:
            raise RuntimeError("channel dropped")
        return SimpleNamespace(stdout=self._stdout, stderr="", exit_code=self._exit)


_CLEAN = "OBLIGATION_PROBE=1\nDIRTY=0\nAHEAD=0\nUNPUSHED_BRANCHES=0\n"
_DIRTY = "OBLIGATION_PROBE=1\nDIRTY=1\nAHEAD=0\nUNPUSHED_BRANCHES=0\n"
_UNPUSHED = "OBLIGATION_PROBE=1\nDIRTY=0\nAHEAD=0\nUNPUSHED_BRANCHES=1\n"


@pytest.mark.asyncio
async def test_settle_on_clean_disconnect_settles(monkeypatch):
    settled = {}
    monkeypatch.setattr(
        "agent_codespaces.coordination.settle_obligation",
        lambda name, ref, **k: settled.update(name=name, ref=ref) or True,
    )
    mgr = _FakeManager(stdout=_CLEAN)
    ok = await m._settle_codespace_on_disconnect(mgr, "cs-one", "mach/proj/wt-b")
    assert ok is True
    assert settled == {"name": "cs-one", "ref": "mach/proj/wt-b"}


@pytest.mark.asyncio
async def test_no_settle_when_dirty(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        "agent_codespaces.coordination.settle_obligation",
        lambda *a, **k: called.update(n=called["n"] + 1) or True,
    )
    mgr = _FakeManager(stdout=_DIRTY)
    ok = await m._settle_codespace_on_disconnect(mgr, "cs-one", "mach/proj/wt-b")
    assert ok is False and called["n"] == 0  # dirty -> not at-rest -> no settle


@pytest.mark.asyncio
async def test_no_settle_when_unpushed_branch(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.coordination.settle_obligation",
        lambda *a, **k: pytest.fail("should not settle with unpushed work"),
    )
    mgr = _FakeManager(stdout=_UNPUSHED)
    ok = await m._settle_codespace_on_disconnect(mgr, "cs-one", "mach/proj/wt-b")
    assert ok is False


@pytest.mark.asyncio
async def test_probe_failure_degrades_no_settle(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.coordination.settle_obligation",
        lambda *a, **k: pytest.fail("should not settle when probe unknown"),
    )
    mgr = _FakeManager(raises=True)
    ok = await m._settle_codespace_on_disconnect(mgr, "cs-one", "mach/proj/wt-b")
    assert ok is False  # un-probeable -> known=False -> not at-rest


@pytest.mark.asyncio
async def test_settle_returns_false_when_settle_fails(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.coordination.settle_obligation",
        lambda *a, **k: False,  # e.g. cross-machine defer / no binstub
    )
    mgr = _FakeManager(stdout=_CLEAN)
    ok = await m._settle_codespace_on_disconnect(mgr, "cs-one", "mach/proj/wt-b")
    assert ok is False


def test_at_rest_helper_matches_expectations():
    # Sanity: the clean probe parses to at-rest (not in flight).
    assert cl.at_rest(cl.parse_probe(_CLEAN), in_flight=False)
    assert not cl.at_rest(cl.parse_probe(_CLEAN), in_flight=True)
    assert not cl.at_rest(cl.parse_probe(_UNPUSHED), in_flight=False)
