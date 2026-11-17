"""First-touch application admission through a real isolated Python child."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import agent_worktrees
from test_manager_application_identity import application as application
from test_manager_recovery import adopted as adopted


def test_normal_controller_reads_fresh_application_in_a_clean_process(
    application: SimpleNamespace,
) -> None:
    source = Path(agent_worktrees.__file__).resolve().parent.parent
    before = (application.root / "current-version").read_bytes()
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", "-c",
            "import json,sys;sys.path.insert(0,sys.argv[1]);"
            "from agent_worktrees.manager_recovery import controller_selection;"
            "selected=controller_selection();assert selected.adoption is not None;"
            "source=selected.adoption.with_source();selected.verify();"
            "print(json.dumps({'root':str(source.root),'repo':source.repo,'ref':source.ref}))",
            str(source),
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "root": str(application.root),
        "repo": str(application.repo),
        "ref": "application-release",
    }
    assert (application.root / "current-version").read_bytes() == before
    assert not (application.root / ".installer-inputs").exists()
