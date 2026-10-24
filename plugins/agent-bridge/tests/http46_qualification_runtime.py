"""Bind actual replay imports to the retained historical fixture's source bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
MARKER = ".http46-capture-qualification.json"


def pytest_terminal_summary(terminalreporter: Any) -> None:
    for report in terminalreporter.stats.get("passed", []):
        for name, value in report.user_properties:
            if name == "http46_capture_qualified":
                terminalreporter.write_line(
                    "HTTP46_CAPTURE_QUALIFIED " + json.dumps(value, sort_keys=True),
                )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    marker = json.loads((ROOT / MARKER).read_text(encoding="utf-8"))
    expected = marker["runtime_sha256"]
    packages = {
        relative.split("/src/", 1)[1].split("/", 1)[0].removesuffix(".py")
        for relative in expected
        if "/src/" in relative
    }
    from agent_bridge import protocol

    if protocol.HTTP_PROTOCOL_VERSION != 46:
        raise pytest.UsageError("historical replay imported a non-46 runtime")
    checked = 0
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] not in packages:
            continue
        location = getattr(module, "__file__", None)
        if location is None:
            continue
        path = Path(location).resolve()
        if not path.is_relative_to(ROOT):
            raise pytest.UsageError(f"historical replay imported foreign source: {name}")
        relative = path.relative_to(ROOT).as_posix()
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected.get(relative):
            raise pytest.UsageError(f"historical replay source bytes differ: {name}")
        checked += 1
    if checked == 0:
        raise pytest.UsageError("historical replay checked no runtime imports")
    print(
        "HTTP46_REPLAY_BOUND "
        + json.dumps(
            {
                "historical_source": marker["historical_source"],
                "checked_modules": checked,
                "protocol": 46,
                "exitstatus": exitstatus,
            },
            sort_keys=True,
        )
    )
