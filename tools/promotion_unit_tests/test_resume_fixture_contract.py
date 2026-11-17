"""Actor-free routing controls for the real native Resume fixture owner."""

from __future__ import annotations

import ast
import io
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "worktree-manager/tests/native/test_resume_pane_startup.py"
)
_NO_WINDOW = 0x08000000


@pytest.mark.parametrize(
    "module,operation,supported",
    [
        ("agent_remote_driver.mux_creation", "--describe-contract", True),
        ("agent_remote_driver.mux_observation", "--describe-contract", True),
        ("agent_remote_driver.mux_observation", "--observe", True),
        ("agent_remote_driver.mux_creation", "--observe", False),
        ("unselected.module", "--observe", False),
    ],
)
def test_native_owner_forwards_selected_observation_stdin_and_exit(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    operation: str,
    supported: bool,
) -> None:
    tree = ast.parse(_FIXTURE.read_text(encoding="utf-8"))
    owner = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "OWNER"
            for target in node.targets
        )
    )
    arguments = ["-I", "-X", "utf8", "-m", module, operation]
    payload = b'{"version":1,"session":"wt-fixture"}\n'
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(sys, "argv", ["owner.py", *arguments])
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
    )
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", _NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as caught:
        exec(compile(owner, str(_FIXTURE) + ":OWNER", "exec"), {"__name__": "__main__"})
    assert caught.value.code == (17 if supported else 91)
    if supported:
        assert calls == [
            (
                [sys.executable, *arguments],
                {"input": payload, "timeout": 25, "creationflags": _NO_WINDOW},
            )
        ]
    else:
        assert calls == []
