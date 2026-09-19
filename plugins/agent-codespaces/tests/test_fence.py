"""Tests for the cross-harness in-CodeSpace lockfile fence (Phase 4)."""

from __future__ import annotations

import json
import shlex
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import coordination, fence, lease

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
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: None)
    mgr = _FakeManager()
    ok = await _fence._check_cross_harness_fence(mgr, "cs", None)
    assert ok is True and mgr.calls == []


@pytest.mark.asyncio
async def test_wiring_absent_marker_proceeds_and_writes(_fence, monkeypatch):
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)
    mgr = _FakeManager(read_stdout="", read_exit=0)
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is True
    assert any(c.startswith("cat ") for c in mgr.calls)
    assert any("mv " in c for c in mgr.calls)  # our marker written


@pytest.mark.asyncio
async def test_wiring_fresh_foreign_refuses(_fence, monkeypatch, capsys):
    import time as _t
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)
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
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)
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
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)
    same = json.dumps({
        "version": fence.FENCE_VERSION, "harness": LOCAL,
        "holder": "other/p/w", "written_at": _t.time(), "ttl": 3600,
    })
    mgr = _FakeManager(read_stdout=same, read_exit=0)
    ok = await _fence._check_cross_harness_fence(mgr, "cs", "m/p/w")
    assert ok is True and any("mv " in c for c in mgr.calls)


@pytest.mark.asyncio
async def test_wiring_read_failure_proceeds(_fence, monkeypatch):
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)

    class _Boom(_FakeManager):
        async def exec_command(self, name, command, timeout=None):
            if command.startswith("cat "):
                raise RuntimeError("ssh dropped")
            return SimpleNamespace(stdout="", stderr="", exit_code=0)

    ok = await _fence._check_cross_harness_fence(_Boom(), "cs", "m/p/w")
    assert ok is True


@pytest.mark.asyncio
@pytest.mark.parametrize("caller,owner", [
    ("project-one", "project-two"),
    ("project-two", "project-one"),
    ("project-one", "project-one"),
])
async def test_fence_uses_declared_owner_project_not_cwd(
    _real_identity, _fence, tmp_path, monkeypatch, caller, owner,
):
    cwd = tmp_path / caller
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    registry = {
        name: f"https://example.test/{name}.git"
        for name in ("project-one", "project-two")
    }
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        project = args[1] if args[:1] == ["--project"] else cwd.name
        return _proc(0, registry[project] + "\n")

    monkeypatch.setattr(coordination, "_run", run)
    holder = f"machine/{owner}/worktree#session-one"
    mgr = _FakeManager(read_stdout=_marker_json(harness=registry[owner], written_at=10**12))
    assert await _fence._check_cross_harness_fence(mgr, "cs", holder)
    assert calls == [(["--project", owner, "get", "lease-origin"], {"timeout": 10})]
    written = json.loads(shlex.split(mgr.calls[-1])[2])
    assert written["harness"] == registry[owner]
    assert written["holder"] == holder


@pytest.mark.parametrize("holder", ["", "machine/project", "machine//worktree", "project", 42])
def test_invalid_explicit_owner_never_queries_ambient(_real_identity, monkeypatch, holder):
    monkeypatch.setattr(coordination, "_run", lambda *a, **k: pytest.fail("queried invalid owner"))
    with pytest.raises(lease.CoordinationRejected, match="qualified project"):
        coordination.harness_identity(holder)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, _proc(1), _proc(0, ""), _proc(0, "one\ntwo"), _proc(0, "one\0two")])
async def test_unresolved_explicit_owner_refuses_before_marker_io(
    _real_identity, _fence, monkeypatch, response,
):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args == ["get", "lease-origin"]:
            return _proc(0, LOCAL)  # ambient lookup would succeed, but is forbidden
        return response

    monkeypatch.setattr(coordination, "_run", run)
    mgr = _FakeManager()
    assert not await _fence._check_cross_harness_fence(mgr, "cs", "machine/missing/worktree")
    assert calls == [["--project", "missing", "get", "lease-origin"]]
    assert mgr.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_error", [False, True])
async def test_explicit_owner_resolution_failure_never_disables_fence(_fence, monkeypatch, raise_error):
    def unresolved(*args):
        if raise_error:
            raise OSError("owner registry unavailable")
        return None

    monkeypatch.setattr(coordination, "harness_identity", unresolved)
    mgr = _FakeManager()
    assert not await _fence._check_cross_harness_fence(mgr, "cs", "machine/project/worktree")
    assert mgr.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("now,proceed", [(1100.1, False), (1130, False), (1130.1, True)])
async def test_foreign_refusal_reports_expiry_without_changing_ttl(
    _fence, monkeypatch, capsys, now, proceed,
):
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)
    monkeypatch.setattr(_fence.time, "time", lambda: now)
    mgr = _FakeManager(read_stdout=_marker_json(
        harness="https://example.test/foreign.git", holder="machine/project/worktree",
        written_at=1000, ttl=100,
    ))
    assert await _fence._check_cross_harness_fence(mgr, "cs", "machine/project/worktree") is proceed
    assert any("mv " in c for c in mgr.calls) is proceed
    if not proceed:
        error = capsys.readouterr().err
        assert "1970-01-01T00:18:50+00:00" in error
        assert f"{30 if now == 1100.1 else 0}s remaining" in error
        assert "includes 30s clock-skew allowance" in error


@pytest.mark.asyncio
async def test_unrepresentable_foreign_expiry_still_refuses(_fence, monkeypatch, capsys):
    monkeypatch.setattr(coordination, "harness_identity", lambda *_: LOCAL)
    mgr = _FakeManager(read_stdout=_marker_json(harness="foreign", written_at=1e300))
    assert not await _fence._check_cross_harness_fence(mgr, "cs", "machine/project/worktree")
    assert "expiry cannot be represented" in capsys.readouterr().err
    assert len(mgr.calls) == 1


@pytest.mark.parametrize("native", [False, True])
def test_ssh_and_native_pass_qualified_holder_to_fence(ssh_runtime, tmp_path, monkeypatch, native):
    from agent_codespaces import __main__ as cli, native_transport, relay_readiness

    holder = "machine/owner-project/worktree"
    monkeypatch.setattr(coordination, "owner_ref", lambda **k: holder)
    monkeypatch.setattr(coordination, "journal_obligation", lambda *a: False)
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(cli, "_check_cross_harness_fence", check)
    monkeypatch.setattr(relay_readiness, "relay_ping", lambda _: True)
    monkeypatch.setattr(lease, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease, "_LOCK_FILE", tmp_path / "lease.lock")
    monkeypatch.setattr(lease, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(native_transport, "InputPump", lambda: SimpleNamespace(closed=threading.Event()))
    monkeypatch.setattr(native_transport.signal, "signal", lambda *a: None)
    if native:
        args = [
            "native-transport", "example-space", "--owner", "example-worktree",
            "--execution-id", "execution", "--generation", "generation", "--resume-infrastructure",
        ]
    else:
        args = ["ssh", "example-space", "--effort", "example-worktree", "--no-relay", "--remote-cmd", "true"]
    assert cli.main(args) == 75
    assert check.await_args.args[1:] == ("example-space", holder)
