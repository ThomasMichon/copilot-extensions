"""The native fixture owner preserves the real observer's structured refusal."""

import ast
import json
import os
from pathlib import Path
import subprocess

import pytest
from _native_python_owner import native_python_owner

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows executable contract"),
    pytest.mark.timeout(60),
]


def test_native_owner_preserves_observation_refusal(tmp_path: Path) -> None:
    source = Path(__file__).with_name("test_resume_pane_startup.py")
    module = ast.parse(source.read_text(encoding="utf-8"))
    value = next(
        node.value for node in module.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "OWNER" for target in node.targets)
    )
    owner = ast.literal_eval(value)
    assert isinstance(owner, str)
    (tmp_path / "owner.py").write_text(owner, encoding="utf-8")
    env = os.environ.copy()
    executable = native_python_owner(tmp_path, env)
    result = subprocess.run(
        [str(executable), "-I", "-X", "utf8", "-m",
         "agent_remote_driver.mux_observation", "--observe"],
        input=json.dumps({"version": 1, "session": ""}),
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
        check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW"),
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "contract": "copilot-driver.mux-observation", "version": 1,
        "status": "unsupported", "reason": "unsupported-target",
        "incarnation": "", "clients": None, "panes": [],
    }
