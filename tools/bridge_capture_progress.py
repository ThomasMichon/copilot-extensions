"""Source-bound HTTP-52 diagnostics, separate from capture acceptance evidence."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
from types import CodeType, FunctionType
from typing import TYPE_CHECKING, TextIO, TypedDict, cast
from unittest.mock import patch

if TYPE_CHECKING:
    import pytest

ENVIRONMENT = "BRIDGE_HTTP52_CAPTURE_PROGRESS"
PREFIX = "http52-progress-"
SOURCE = "tools/bridge_capture_progress.py"
MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 8192
_stream: TextIO | None = None
_records = 0


class ObserverSummary(TypedDict):
    journal: str
    started: int
    completed: int
    last_started: dict[str, object] | None
    slowest_phases: list[dict[str, object]]


class ProgressSummary(TypedDict):
    scope: str
    observers: list[ObserverSummary]


def _ordinary(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Capture progress requires an ordinary owned path")


def _write(event: dict[str, object]) -> None:
    global _records
    if _stream is None:
        return
    if _records >= MAX_RECORDS:
        raise ValueError("Capture progress record budget exceeded")
    line = json.dumps(event, sort_keys=True) + "\n"
    if _stream.tell() + len(line.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Capture progress byte budget exceeded")
    _stream.write(line)
    _stream.flush()
    _records += 1


def pytest_configure(config: pytest.Config) -> None:
    global _stream, _records
    if ENVIRONMENT not in os.environ:
        return
    import bridge_http47_runtime as binding

    request_path, request = binding.load_request()
    if request.get("capture_generation") != 52:
        raise ValueError("Capture progress requires its HTTP-52 source binding")
    root = Path(request["root"])
    directory = Path(os.environ[ENVIRONMENT])
    if directory.parent != request_path.parent.parent or not directory.name.startswith(PREFIX):
        raise ValueError("Capture progress directory escaped its request owner")
    for ancestor in (root, directory.parent, directory):
        _ordinary(ancestor, directory=True)
    source = Path(__file__)
    if source.resolve() != root / SOURCE:
        raise ValueError("Capture progress source escaped its selected root")
    _ordinary(source)
    data = source.read_bytes()
    expected = request["source_proof"][SOURCE]["sha256"]
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    codes: set[CodeType] = set()

    def collect(code: CodeType) -> None:
        codes.add(code)
        for value in code.co_consts:
            if isinstance(value, CodeType):
                collect(value)

    collect(compile(data, str(source), "exec", dont_inherit=True, optimize=sys.flags.optimize))
    functions = (
        inspect.unwrap(value) for value in vars(sys.modules[__name__]).values()
        if isinstance(value, FunctionType) and value.__module__ == __name__
    )
    if hashlib.sha256(normalized).hexdigest() != expected or any(
        function.__code__ not in codes for function in functions
    ):
        raise ValueError("Capture progress executable source is unproven")
    _stream = (directory / f"observer-{os.getpid()}.jsonl").open("x", encoding="utf-8")
    _records = 0
    _write({"kind": "observer", "pid": os.getpid()})


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    _write({"kind": "start", "nodeid": nodeid, "time": time.monotonic()})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _write({
        "kind": "report", "nodeid": report.nodeid, "phase": report.when,
        "outcome": report.outcome, "seconds": report.duration,
    })


def pytest_unconfigure(config: pytest.Config) -> None:
    global _stream
    if _stream is not None:
        _stream.close()
        _stream = None


def summarize(directory: Path) -> ProgressSummary:
    """Read bounded diagnostics only; never infer a pass or settle resources."""
    _ordinary(directory, directory=True)
    observers: list[ObserverSummary] = []
    for path in sorted(directory.glob("observer-*.jsonl")):
        _ordinary(path)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Capture progress byte budget exceeded")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_RECORDS:
            raise ValueError("Capture progress record budget exceeded")
        events: list[dict[str, object]] = []
        for index, line in enumerate(lines):
            try:
                event: object = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    break
                raise ValueError("Capture progress contains a malformed complete record") from None
            if not isinstance(event, dict) or any(not isinstance(key, str) for key in event):
                raise ValueError("Capture progress contains an invalid record")
            events.append(cast(dict[str, object], event))
        starts = [event for event in events if event.get("kind") == "start"]
        reports = [event for event in events if event.get("kind") == "report"]
        slow = sorted(
            (
                event for event in reports
                if type(event.get("seconds")) in (int, float)
            ),
            key=lambda event: cast(float, event["seconds"]), reverse=True,
        )[:5]
        observers.append({
            "journal": path.name, "started": len(starts),
            "completed": sum(event.get("phase") == "teardown" for event in reports),
            "last_started": starts[-1] if starts else None, "slowest_phases": slow,
        })
    return {"scope": "diagnostics-only; not capture evidence", "observers": observers}


@contextmanager
def observe(root: Path) -> Iterator[None]:
    parent = root / ".test-venvs"
    parent.mkdir(exist_ok=True)
    _ordinary(parent, directory=True)
    directory = Path(tempfile.mkdtemp(prefix=PREFIX, dir=parent))
    plugins = [item for item in os.environ.get("PYTEST_PLUGINS", "").split(",") if item]
    if "bridge_capture_progress" not in plugins:
        plugins.append("bridge_capture_progress")
    try:
        with patch.dict(os.environ, {
            ENVIRONMENT: str(directory), "PYTEST_PLUGINS": ",".join(plugins),
        }):
            yield
    except BaseException:
        try:
            print("HTTP-52 probe progress: " + json.dumps(summarize(directory), sort_keys=True), file=sys.stderr)
        except (OSError, ValueError) as error:
            print(f"HTTP-52 probe progress unavailable: {type(error).__name__}", file=sys.stderr)
        print(f"HTTP-52 progress retained: {directory}", file=sys.stderr)
        raise
    else:
        _ordinary(directory, directory=True)
        for path in directory.iterdir():
            _ordinary(path)
        shutil.rmtree(directory)
