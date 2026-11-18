"""The update planner's Index role probe must outlast its nested owner read."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import update_runtime_plan

# configuration_context.read_owner_context bounds the nested Worktrees read.
NESTED_OWNER_READ_S = 20


def test_index_role_resolution_outlasts_nested_owner_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = Path(__file__).resolve().parents[2] / "agent-index"
    host, client = tmp_path / "host", tmp_path / "client"

    def slow_resolver(
        argv: list[str], **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        timeout = kwargs["timeout"]
        if timeout <= NESTED_OWNER_READ_S:
            raise subprocess.TimeoutExpired(argv, timeout)
        role = "host" if argv[argv.index("--repo") + 1] == str(host.resolve()) else "client"
        return subprocess.CompletedProcess(argv, 0, stdout=f"{role}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", slow_resolver)
    selected = update_runtime_plan._index_repository(
        payload, client, (client, host), {"AGENT_INDEX_MACHINE": "fixture"},
    )
    assert selected == host.resolve()
