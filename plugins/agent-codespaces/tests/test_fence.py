"""Tests for the cross-harness in-CodeSpace lockfile fence (Phase 4)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_codespaces import coordination, fence

# ── FenceMarker.parse ────────────────────────────────────────────────────────

def _marker_json(**over) -> str:
    base = {
        "version": fence.FENCE_VERSION,
        "harness": "https://example/store.git",
        "holder": "m/p/w",
        "written_at": 1000.0,
        "ttl": 3600,
    }
    base.update(over)
    return json.dumps(base)


def test_parse_roundtrip():
    m = fence.FenceMarker.parse(_marker_json())
    assert m is not None
    assert m.harness == "https://example/store.git"
    assert m.holder == "m/p/w"
    assert m.written_at == 1000.0
    assert m.ttl == 3600


@pytest.mark.parametrize("text", ["", "   ", None, "not json", "[]", "42"])
def test_parse_absent_or_garbage_is_none(text):
    assert fence.FenceMarker.parse(text) is None


def test_parse_unknown_version_is_none():
    assert fence.FenceMarker.parse(_marker_json(version=99)) is None


def test_parse_missing_harness_is_none():
    assert fence.FenceMarker.parse(_marker_json(harness="")) is None


def test_parse_bad_numbers_is_none():
    assert fence.FenceMarker.parse(_marker_json(written_at="soon")) is None


def test_parse_tolerates_missing_holder():
    m = fence.FenceMarker.parse(_marker_json(holder=""))
    assert m is not None and m.holder == ""


# ── FenceMarker.is_fresh ─────────────────────────────────────────────────────

def test_is_fresh_within_ttl():
    m = fence.FenceMarker("h", "w", written_at=1000.0, ttl=100)
    assert m.is_fresh(now=1050.0)


def test_is_fresh_expired_past_ttl_and_skew():
    m = fence.FenceMarker("h", "w", written_at=1000.0, ttl=100)
    assert not m.is_fresh(now=1000.0 + 100 + fence.FENCE_SKEW + 1)


def test_is_fresh_within_skew_grace():
    m = fence.FenceMarker("h", "w", written_at=1000.0, ttl=100)
    # Just past ttl but inside the skew window -> still fresh.
    assert m.is_fresh(now=1000.0 + 100 + fence.FENCE_SKEW - 1)


def test_is_fresh_zero_ttl_never_fresh():
    m = fence.FenceMarker("h", "w", written_at=1e12, ttl=0)
    assert not m.is_fresh(now=1e12)


# ── evaluate ─────────────────────────────────────────────────────────────────

LOCAL = "https://example/store.git"


def test_evaluate_no_marker_proceeds():
    d = fence.evaluate(LOCAL, None)
    assert d.proceed and d.reason == "no-marker"


def test_evaluate_no_identity_proceeds():
    m = fence.FenceMarker("other", "w", written_at=1000.0, ttl=100)
    d = fence.evaluate("", m, now=1000.0)
    assert d.proceed and d.reason == "no-identity"


def test_evaluate_same_harness_proceeds():
    m = fence.FenceMarker(LOCAL, "otherw", written_at=1000.0, ttl=100)
    d = fence.evaluate(LOCAL, m, now=1000.0)
    assert d.proceed and d.reason == "same-harness"


def test_evaluate_stale_foreign_proceeds():
    m = fence.FenceMarker("foreign", "fw", written_at=1000.0, ttl=100)
    d = fence.evaluate(LOCAL, m, now=1000.0 + 100 + fence.FENCE_SKEW + 5)
    assert d.proceed and d.reason == "stale-foreign"
    assert d.foreign_harness == "foreign" and d.foreign_holder == "fw"


def test_evaluate_fresh_foreign_refuses():
    m = fence.FenceMarker("foreign", "fw", written_at=1000.0, ttl=3600)
    d = fence.evaluate(LOCAL, m, now=1100.0)
    assert d.refuse and d.reason == "fresh-foreign"
    assert d.foreign_harness == "foreign" and d.foreign_holder == "fw"


def test_evaluate_local_harness_whitespace_stripped():
    m = fence.FenceMarker(LOCAL, "w", written_at=1000.0, ttl=3600)
    d = fence.evaluate(f"  {LOCAL}  ", m, now=1100.0)
    assert d.proceed and d.reason == "same-harness"


# ── command builders ─────────────────────────────────────────────────────────

def test_read_marker_command_expands_home_and_swallows_absent():
    cmd = fence.read_marker_command()
    assert '"$HOME/.agent-lease"' in cmd
    assert "2>/dev/null" in cmd and "|| true" in cmd


def test_write_marker_command_atomic_and_quoted_payload():
    m = fence.FenceMarker(LOCAL, "m/p/w", written_at=1000.0, ttl=3600)
    cmd = fence.write_marker_command(m)
    assert '"$HOME/.agent-lease"' in cmd
    assert '"$HOME/.agent-lease.tmp.$$"' in cmd
    assert "mv " in cmd
    # The JSON payload is single-quoted (shlex.quote) so it survives the shell.
    assert "'" in cmd


def test_remote_path_expr_bare_tilde():
    assert fence._remote_path_expr("~") == '"$HOME"'


def test_remote_path_expr_absolute_untouched():
    assert fence._remote_path_expr("/opt/x") == '"/opt/x"'


# ── coordination.harness_identity shim ───────────────────────────────────────

# Captured at import BEFORE the package conftest's autouse ``_neutralize_l2``
# fixture stubs ``harness_identity`` -> None, so these tests can restore the real
# implementation and exercise it (stubbing only ``_run``).
_REAL_HARNESS_IDENTITY = coordination.harness_identity


@pytest.fixture
def _real_identity(monkeypatch):
    monkeypatch.setattr(coordination, "harness_identity", _REAL_HARNESS_IDENTITY)


def _proc(returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess
    return subprocess.CompletedProcess(["agent-worktrees"], returncode, stdout, stderr)


def test_harness_identity_returns_origin(_real_identity, monkeypatch):
    monkeypatch.setattr(
        coordination, "_run",
        lambda args, **k: _proc(0, "https://example/store.git\n"),
    )
    assert coordination.harness_identity() == "https://example/store.git"


def test_harness_identity_empty_is_none(_real_identity, monkeypatch):
    monkeypatch.setattr(coordination, "_run", lambda args, **k: _proc(0, "  \n"))
    assert coordination.harness_identity() is None


def test_harness_identity_unavailable_is_none(_real_identity, monkeypatch):
    monkeypatch.setattr(coordination, "_run", lambda args, **k: None)
    assert coordination.harness_identity() is None


def test_harness_identity_nonzero_is_none(_real_identity, monkeypatch):
    monkeypatch.setattr(coordination, "_run", lambda args, **k: _proc(1, "", "boom"))
    assert coordination.harness_identity() is None


# ── _check_cross_harness_fence wiring ────────────────────────────────────────

class _FakeManager:
    """Records exec_command calls; returns queued results by command substring."""

    def __init__(self, read_stdout: str = "", read_exit: int = 0):
        self.calls: list[str] = []
        self._read_stdout = read_stdout
        self._read_exit = read_exit

    async def exec_command(self, name, command, timeout=None):
        self.calls.append(command)
        if command.startswith("cat "):
            return SimpleNamespace(
                stdout=self._read_stdout, stderr="", exit_code=self._read_exit,
            )
        return SimpleNamespace(stdout="", stderr="", exit_code=0)


@pytest.fixture
def _fence(monkeypatch):
    from agent_codespaces import __main__ as m
    return m


@pytest.mark.asyncio
async def test_wiring_disabled_env_proceeds_without_ssh(_fence, monkeypatch):
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_FENCE", "1")
    mgr = _FakeManager()
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is True and mgr.calls == []


@pytest.mark.asyncio
async def test_wiring_no_identity_proceeds_without_ssh(_fence, monkeypatch):
    monkeypatch.setattr(coordination, "harness_identity", lambda: None)
    mgr = _FakeManager()
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is True and mgr.calls == []


@pytest.mark.asyncio
async def test_wiring_absent_marker_proceeds_and_writes(_fence, monkeypatch):
    monkeypatch.setattr(coordination, "harness_identity", lambda: LOCAL)
    mgr = _FakeManager(read_stdout="", read_exit=0)
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is True
    assert any(c.startswith("cat ") for c in mgr.calls)
    assert any("mv " in c for c in mgr.calls)  # our marker written


@pytest.mark.asyncio
async def test_wiring_fresh_foreign_refuses(_fence, monkeypatch, capsys):
    import time as _t
    monkeypatch.setattr(coordination, "harness_identity", lambda: LOCAL)
    foreign = json.dumps({
        "version": fence.FENCE_VERSION, "harness": "https://foreign/store.git",
        "holder": "other/p/w", "written_at": _t.time(), "ttl": 3600,
    })
    mgr = _FakeManager(read_stdout=foreign, read_exit=0)
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is False
    assert "[BUSY]" in capsys.readouterr().err
    # No marker overwrite when refusing.
    assert not any("mv " in c for c in mgr.calls)


@pytest.mark.asyncio
async def test_wiring_fresh_foreign_force_takes_over(_fence, monkeypatch):
    import time as _t
    monkeypatch.setattr(coordination, "harness_identity", lambda: LOCAL)
    foreign = json.dumps({
        "version": fence.FENCE_VERSION, "harness": "https://foreign/store.git",
        "holder": "other/p/w", "written_at": _t.time(), "ttl": 3600,
    })
    mgr = _FakeManager(read_stdout=foreign, read_exit=0)
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w", force=True)
    assert ok is True
    assert any("mv " in c for c in mgr.calls)  # takeover writes our marker


@pytest.mark.asyncio
async def test_wiring_same_harness_proceeds(_fence, monkeypatch):
    import time as _t
    monkeypatch.setattr(coordination, "harness_identity", lambda: LOCAL)
    same = json.dumps({
        "version": fence.FENCE_VERSION, "harness": LOCAL,
        "holder": "other/p/w", "written_at": _t.time(), "ttl": 3600,
    })
    mgr = _FakeManager(read_stdout=same, read_exit=0)
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is True and any("mv " in c for c in mgr.calls)


@pytest.mark.asyncio
async def test_wiring_read_failure_proceeds(_fence, monkeypatch):
    monkeypatch.setattr(coordination, "harness_identity", lambda: LOCAL)

    class _Boom(_FakeManager):
        async def exec_command(self, name, command, timeout=None):
            if command.startswith("cat "):
                raise RuntimeError("ssh dropped")
            return SimpleNamespace(stdout="", stderr="", exit_code=0)

    ok = await _fence._check_cross_harness_fence(_Boom(), "cs", "m/p/w")
    assert ok is True
