"""Regression tests for the asyncio.wait_for timeout-handler guard.

Drives the real ``tools/check-asyncio-timeout-handlers.py`` as a subprocess over
a throwaway ``plugins/<p>/src`` tree (mirroring the other tool tests) so the AST
walk, the inner-``finally`` / outer-``except`` shape, ``contextlib.suppress``,
and the safe cases are all exercised end to end.

Run:  python -m pytest tools/test_check_asyncio_timeout_handlers.py
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-asyncio-timeout-handlers.py"


def _write(root: Path, body: str) -> Path:
    p = root / "plugins" / "p" / "src" / "mod.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("import asyncio\nimport contextlib\n\n" + textwrap.dedent(body), encoding="utf-8")
    return p


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    # The guard resolves REPO as the script's parent.parent; point it at the tmp
    # tree by copying the script in and running it from there.
    tool_dir = root / "tools"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / SCRIPT.name).write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(tool_dir / SCRIPT.name)],
        capture_output=True, text=True, check=False,
    )


def test_flags_bare_timeout_handler(tmp_path: Path) -> None:
    _write(tmp_path, """
        async def f(proc):
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                pass
    """)
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "mod.py:" in (r.stdout + r.stderr)
    assert "missing asyncio.TimeoutError" in (r.stdout + r.stderr)


def test_flags_tuple_with_oserror(tmp_path: Path) -> None:
    _write(tmp_path, """
        async def f(proc):
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
            except (OSError, TimeoutError):
                pass
    """)
    assert _run(tmp_path).returncode == 1


def test_flags_suppress(tmp_path: Path) -> None:
    _write(tmp_path, """
        async def f(proc):
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
    """)
    assert _run(tmp_path).returncode == 1


def test_flags_inner_finally_outer_except(tmp_path: Path) -> None:
    # The manager.py shape: wait_for in an inner try/finally (no handlers), the
    # real except one level out. Must still be flagged.
    _write(tmp_path, """
        async def f(proc, info):
            try:
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=5.0)
                finally:
                    info.done = True
            except TimeoutError:
                pass
    """)
    assert _run(tmp_path).returncode == 1


def test_ok_when_asyncio_timeout_named(tmp_path: Path) -> None:
    _write(tmp_path, """
        async def f(proc):
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, TimeoutError, OSError):
                pass
    """)
    assert _run(tmp_path).returncode == 0


def test_ok_when_concurrent_futures_timeout_named(tmp_path: Path) -> None:
    # concurrent.futures.TimeoutError is a valid safe catch on 3.10 (asyncio's
    # wait_for timeout derives from it); the dotted name must be recognized, not
    # truncated to '?.TimeoutError' (regression for the PR #642 review note).
    _write(tmp_path, """
        import concurrent.futures
        async def f(proc):
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (TimeoutError, concurrent.futures.TimeoutError):
                pass
    """)
    assert _run(tmp_path).returncode == 0


def test_ok_when_broad_exception(tmp_path: Path) -> None:
    _write(tmp_path, """
        async def f(proc):
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                pass
    """)
    assert _run(tmp_path).returncode == 0


def test_ok_when_no_timeout_named(tmp_path: Path) -> None:
    # A handler that does not express timeout intent (no builtin TimeoutError) is
    # not flagged -- it may not mean to swallow the timeout at all.
    _write(tmp_path, """
        async def f(proc):
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except ValueError:
                pass
    """)
    assert _run(tmp_path).returncode == 0


def test_ok_inner_asyncio_timeout_shields_outer_bare(tmp_path: Path) -> None:
    # Inner handler safely catches asyncio.TimeoutError; the outer bare
    # TimeoutError never sees the exception, so nothing is vulnerable.
    _write(tmp_path, """
        async def f(proc):
            try:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            except TimeoutError:
                pass
    """)
    assert _run(tmp_path).returncode == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
