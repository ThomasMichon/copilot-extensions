"""Exercise the Manager's selected payload materializer without installing a runtime."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from worktree_manager.self_install_payload import _materialize_payload_pointers

PAYLOAD = Path(__file__).resolve().parents[1]
CODEC = PAYLOAD.parent / "libs/worktree-identity"


def test_selected_manager_materializer_embeds_codec_without_changing_authoring_source(tmp_path):
    slot = tmp_path / "standalone"
    slot.mkdir()
    original = (PAYLOAD / "pyproject.toml").read_bytes()
    shutil.copy2(PAYLOAD / "pyproject.toml", slot / "pyproject.toml")
    _materialize_payload_pointers(PAYLOAD, slot)
    assert (PAYLOAD / "pyproject.toml").read_bytes() == original
    project = tomllib.loads((slot / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-worktree-identity" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["agent-worktree-identity"] == {
        "path": "libs/worktree-identity",
    }
    embedded = slot / "libs/worktree-identity"
    for relative in ("pyproject.toml", "README.md", "src/worktree_identity/__init__.py"):
        assert (embedded / relative).read_bytes() == (CODEC / relative).read_bytes()
    result = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            "import importlib.util,sys; sys.path.insert(0,sys.argv[1]); "
            "import worktree_identity as w; "
            "assert importlib.util.find_spec('agent_worktrees') is None; "
            "assert importlib.util.find_spec('machine_transport') is None; "
            "identity=w.WorktreeMachineIdentity('host','wsl','custom-guest'); "
            "assert w.parse_machine_identity(identity.to_record_fields()) == identity",
            str(embedded / "src"),
        ],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
