"""A standalone Budget payload loads its own SDK without a repository library tree."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "budget_materialize_subject", REPO / "tools" / "materialize_installer_engine.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(REPO / "tools"))
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh", "bash"))
def test_materialized_budget_status_uses_only_packaged_engine(tmp_path: Path, host: str) -> None:
    if host == "bash" and os.name == "nt":
        pytest.skip("Native POSIX packaged startup requires POSIX")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    candidate = tmp_path / "release"
    payload = candidate / "plugins" / "budget-guidance"
    shutil.copytree(
        REPO / "plugins" / "budget-guidance", payload,
        ignore=shutil.ignore_patterns("__pycache__", ".venv", "tests"),
    )
    MODULE.materialize_refs(candidate, canonical_root=REPO)
    assert not (candidate / "libs").exists()
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".budget-guidance"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_"))
    }
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if host == "bash":
        command = [executable, str(payload / "scripts" / "install.sh"),
                   "status", "--install-dir", str(root)]
    else:
        command = [executable, "-NoProfile", "-NonInteractive", "-File",
                   str(payload / "scripts" / "install.ps1"), "-Action", "status",
                   "-InstallDir", str(root)]
    result = subprocess.run(
        command, cwd=home, env=environment, capture_output=True,
        text=True, timeout=60,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "budget-guidance status" in result.stdout
    assert not root.exists()
