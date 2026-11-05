import os
from pathlib import Path
import subprocess
import sys

import packaging


def test_payload_toolkit_imports_without_a_source_checkout():
    plugin = Path(__file__).resolve().parents[1]
    toolkit = plugin / "scripts" / "_runtime_admission"
    packaging_site = Path(packaging.__file__).resolve().parent.parent
    script = """
import pathlib, sys
payload, tooling = map(pathlib.Path, sys.argv[1:])
sys.path[:0] = [str(payload.parent), str(tooling)]
import _runtime_admission
_runtime_admission.activate()
import agent_procutil.owned_process, installer_dependency_policy
import runtime_profile, runtime_profile_delivery, versioned_runtime
for module in (agent_procutil.owned_process, installer_dependency_policy,
               runtime_profile, runtime_profile_delivery, versioned_runtime):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(payload)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(toolkit), str(packaging_site)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr


def test_payload_toolkit_shell_assets_are_executable_in_git():
    repo = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--stage", "--",
         "plugins/agent-worktrees/scripts/_runtime_admission"],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    shell_modes = [
        line.split(" ", 1)[0] for line in result.stdout.splitlines()
        if line.partition("\t")[2].endswith(".sh")
    ]
    assert shell_modes and all(mode == "100755" for mode in shell_modes)
