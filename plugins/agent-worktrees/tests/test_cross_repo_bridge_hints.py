"""Guard denials retain deliberate Bridge profile startup in their remediation."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Any

import pytest

from test_cross_repo_guard import guard


@pytest.mark.parametrize("locus,target", [
    ({"preferred": "machine:host-b", "machines": ["host-c"]}, "host-b"),
    ({"machines": ["host-c"]}, "host-c"),
])
def test_guard_denial_selects_legacy_startup(
    tmp_path: Path, locus: dict[str, Any], target: str,
) -> None:
    root = tmp_path / "target"
    root.mkdir()
    decision = guard.decide(
        {"toolName": "edit", "cwd": str(tmp_path), "toolArgs": {"path": str(root / "file")}},
        env={}, home=tmp_path,
        guarded_roots=[{
            "name": "project", "delegate": "agent-bridge", "path": str(root), "locus": locus,
        }],
    )
    assert decision["permissionDecision"] == "deny"
    commands = re.findall(r"`(agent-bridge send[^`]*)`", decision["permissionDecisionReason"])
    assert [shlex.split(command) for command in commands] == [
        ["agent-bridge", "send", target, "<task>", "--legacy-selector"],
    ]
