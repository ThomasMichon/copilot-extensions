"""Regression tests for the asyncio.wait_for timeout-handler guard.

Drives the real ``tools/check-asyncio-timeout-handlers.py`` as a subprocess over
a throwaway ``plugins/<p>/src`` tree (mirroring the other tool tests) so the AST
walk, the inner-``finally`` / outer-``except`` shape, ``contextlib.suppress``,
and the safe cases are all exercised end to end.

Run:  python -m pytest tools/test_check_asyncio_timeout_handlers.py
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-asyncio-timeout-handlers.py"


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("asyncio_timeout_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exercise(guard, paths: list[Path], listing: bool) -> None:
    if listing:
        guard._list_all()
    else:
        for path in paths:
            guard._analyze(path)


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


@pytest.mark.parametrize("listing", [False, True], ids=["verify", "list"])
def test_parent_maps_only_for_matching_files(guard, tmp_path, monkeypatch, listing):
    paths = [tmp_path / name for name in ("plain.py", "matching.py", "other.py")]
    bodies = [
        "value = unrelated.wait_for()\n",
        "asyncio.wait_for(first())\nasyncio.wait_for(second())\n",
        "value = 'asyncio.wait_for(not_a_call)'\n",
    ]
    for path, body in zip(paths, bodies):
        path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "_py_files", lambda: paths)
    original_walk, original_parse = ast.walk, ast.parse
    walked, parsed, mapped = [], [], []

    def walk(tree):
        walked.append(tree)
        return original_walk(tree)

    def parse(text, *args, **kwargs):
        tree = original_parse(text, *args, **kwargs)
        parsed.append((text, tree))
        return tree

    original_map = guard._build_parent_field_map

    def parent_map(tree):
        mapped.append(tree)
        return original_map(tree)

    monkeypatch.setattr(guard.ast, "walk", walk)
    monkeypatch.setattr(guard.ast, "parse", parse)
    monkeypatch.setattr(guard, "_build_parent_field_map", parent_map)
    _exercise(guard, paths, listing)
    assert [text for text, _ in parsed] == bodies
    assert mapped == [parsed[1][1]]
    assert [walked.count(tree) for _, tree in parsed] == [1, 2, 1]


@pytest.mark.parametrize("listing", [False, True], ids=["verify", "list"])
def test_breadth_first_call_and_file_order(guard, tmp_path, monkeypatch, capsys, listing):
    path = tmp_path / "first.py"
    path.write_text(
        "async def outer():\n"
        "    try:\n"
        "        async def nested():\n"
        "            await asyncio.wait_for(deep())\n"
        "        await asyncio.wait_for(shallow())\n"
        "    except TimeoutError:\n"
        "        pass\n"
        "asyncio.wait_for(unguarded())\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.py"
    second.write_text(
        "with contextlib.suppress(TimeoutError):\n"
        "    asyncio.wait_for(other())\n",
        encoding="utf-8",
    )
    paths = [second, path]
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "_py_files", lambda: paths)
    if listing:
        guard._list_all()
        assert capsys.readouterr().out.splitlines() == [
            "second.py:2: wait_for [contextlib.suppress: TimeoutError]  <== VULNERABLE",
            "first.py:8: wait_for (unguarded)",
            "first.py:5: wait_for [except: TimeoutError]  <== VULNERABLE",
            "first.py:4: wait_for [except: TimeoutError]  <== VULNERABLE",
        ]
    else:
        problems = [problem for item in paths for problem in guard._analyze(item)]
        assert [problem.split(": asyncio.wait_for")[0] for problem in problems] == [
            "second.py:2", "first.py:5", "first.py:4",
        ]


@pytest.mark.parametrize("listing", [False, True], ids=["verify", "list"])
@pytest.mark.parametrize("body", [b"def invalid(:", b"\xff"])
def test_invalid_source_is_still_ignored(guard, tmp_path, monkeypatch, capsys, listing, body):
    path = tmp_path / "invalid.py"
    path.write_bytes(body)
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "_py_files", lambda: [path])
    _exercise(guard, [path], listing)
    assert capsys.readouterr().out == ""
    assert guard._analyze(path) == []


@pytest.mark.parametrize("listing", [False, True], ids=["verify", "list"])
def test_no_matching_calls_still_validates_path(guard, tmp_path, monkeypatch, listing):
    path = tmp_path / "outside.py"
    path.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", tmp_path / "repo")
    monkeypatch.setattr(guard, "_py_files", lambda: [path])
    with pytest.raises(ValueError):
        _exercise(guard, [path], listing)


@pytest.mark.parametrize("listing", [False, True], ids=["verify", "list"])
def test_read_errors_still_propagate(guard, tmp_path, monkeypatch, listing):
    path = tmp_path / "unreadable.py"
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "_py_files", lambda: [path])

    def refuse_read(self, *, encoding):
        assert self == path
        assert encoding == "utf-8"
        raise OSError("synthetic read failure")

    monkeypatch.setattr(Path, "read_text", refuse_read)
    with pytest.raises(OSError, match="synthetic read failure"):
        _exercise(guard, [path], listing)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
