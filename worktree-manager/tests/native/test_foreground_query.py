"""Foreground admission waits for its actual runtime, not a startup clock."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Protocol, cast

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows terminal contract"),
    pytest.mark.timeout(90),
]
HELPER = Path(__file__).resolve().parents[2] / "bin" / "terminal-launch.ps1"


class _ProcessHandles(Protocol):
    process: int


class _Terminal(Protocol):
    process: _ProcessHandles
    output: bytearray

    def exited(self, handle: int, timeout: int = 0) -> bool: ...
    def wait(self) -> int: ...
    def __enter__(self) -> _Terminal: ...
    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _TerminalFactory(Protocol):
    def __call__(
        self, argv: list[str], *, env: dict[str, str], cwd: Path,
    ) -> _Terminal: ...


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def test_slow_terminal_capability_query_has_no_launch_deadline(tmp_path: Path) -> None:
    from _terminal_console_fixture import Console

    terminal = cast(_TerminalFactory, Console)
    program = (
        "import time;time.sleep(10.25);"
        "from worktree_manager import terminal_launch;"
        "assert terminal_launch.answer_query()"
    )
    host = tmp_path / "slow-query.ps1"
    host.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_quote(HELPER)}\n"
        f"$supported=Get-WorktreeTerminalCapability {_quote(sys.executable)} "
        f"@('-I','-B','-c',{_quote(program)},'--version') worktree_manager\n"
        "if(-not $supported){throw 'Delayed runtime was not admitted'}\n"
        "'slow-query-ready'\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("CE_TERMINAL_LAUNCH", None)
    shell = shutil.which("pwsh")
    assert shell
    with terminal([shell, "-NoProfile", "-File", str(host)], env=environment, cwd=tmp_path) as console:
        deadline = time.monotonic() + 60
        while not console.exited(console.process.process):
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert console.wait() == 0, console.output.decode("utf-8", "replace")
        assert b"slow-query-ready" in console.output
