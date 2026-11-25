"""Prepared-only prerequisites for real MCP guidance wrapper controls."""
from __future__ import annotations

from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]

if sys.platform == "win32":
    HEADLESS = subprocess.CREATE_NO_WINDOW
else:
    HEADLESS = 0


@pytest.fixture(scope="module")
def prepared_helper() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    root = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=HEADLESS,
    ).strip()
    return Path(uv), Path(result.stdout.strip()).resolve(), Path(root)


@pytest.fixture(scope="module")
def packaged_hooks(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("mcp-hook-package") / "agent-mcp"
    shutil.copytree(PLUGIN / "scripts", payload / "scripts")
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml"):
        shutil.copy2(PLUGIN / name, payload / name)
    previous_path = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_payload_invocation.py"))
        changes = owner["materialize"](
            payload, canonical_root=REPO, source_consumer=PLUGIN,
        )
    finally:
        sys.path[:] = previous_path
    assert changes and not any(line.startswith("SKIP") for line in changes), changes
    return payload
