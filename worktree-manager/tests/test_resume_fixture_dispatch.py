"""The native Resume fixture must serve the launcher's observation request."""

import ast
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("operation", ["--describe-contract", "--observe"])
def test_resume_fixture_forwards_observation_input_and_exit(monkeypatch, operation):
    source = Path(__file__).parent / "native" / "test_resume_pane_startup.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "OWNER" for target in node.targets)
    )
    arguments = ["-I", "-X", "utf8", "-m", "agent_remote_driver.mux_observation", operation]
    request = b'{"version":1,"session":"fixture-session"}\n'
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(sys, "argv", ["fixture-owner", *arguments])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(request)))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as exited:
        exec(compile(owner, str(source), "exec"), {"__name__": "__main__"})

    assert exited.value.code == 17
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == [sys.executable, *arguments]
    assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
    if operation == "--observe":
        assert options["input"] == request
        assert "stdin" not in options
    else:
        assert options["stdin"] == subprocess.DEVNULL
        assert "input" not in options
