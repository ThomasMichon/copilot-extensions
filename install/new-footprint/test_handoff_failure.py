import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

import onboard  # noqa: F401 - install the same bootstrap import roots as the real caller
from worktree_manager.onboarding_failure import phase


@pytest.mark.parametrize("owned", [True, False])
def test_silent_child_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                              capsys: pytest.CaptureFixture[str], owned: bool) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    root = tmp_path / "ce-bootstrap-trial-example"
    root.mkdir()
    (root / ".bootstrap-trial.json").write_text(json.dumps(str(root if owned else tmp_path)))
    with pytest.raises(subprocess.CalledProcessError) as failed, phase(root, "manager-handoff"):
        subprocess.run([sys.executable, "-I", "-B", "-c", "raise SystemExit(17)", "private-fixture"],
                       check=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert failed.value.returncode == 17
    expected = {"schema": "copilot-extensions.onboarding-failure/v1",
                "phase": "manager-handoff", "reason": "child-exit", "code": 17}
    assert json.loads(capsys.readouterr().err) == expected
    assert [json.loads(p.read_bytes()) for p in root.glob(".onboarding-failure-*.json")] == (
        [expected] if owned else [])
