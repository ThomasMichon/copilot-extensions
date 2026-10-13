"""Fresh-process machine reads retain the narrowed configuration-load contract."""

import subprocess
import sys

from agent_procutil import no_window_kwargs


def test_native_machine_scalar_loads_configuration_once(tmp_path):
    script = """
import sys
from pathlib import Path
from types import SimpleNamespace
from agent_worktrees import config as cfg, context_cli
root = Path(sys.argv[1])
config = cfg.Config(
    srcroot=str(root), machine="desk", platform=cfg.detect_platform(), repo_name="receiving",
    repos={"receiving": cfg.RepoConfig(anchor=str(root), worktree_root=str(root / "trees"))},
)
calls = []
def load_config(*args, **kwargs):
    calls.append(kwargs["include_control_plane_related_pr"])
    return config
cfg.load_config = load_config
assert context_cli.cmd_get(SimpleNamespace(key="machine")) == 0
assert calls == [False], calls
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "desk"
