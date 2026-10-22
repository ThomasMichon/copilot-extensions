"""Standalone Manager engine delivery and bare-bootstrap contracts."""

from __future__ import annotations

import importlib.util
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import installer_engine_ref as contract


def _sync():
    spec = importlib.util.spec_from_file_location(
        "manager_engine_sync", REPO / "tools" / "sync-installer-engine.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manager_engine_and_runtime_helpers_are_canonical():
    consumer = REPO / "worktree-manager"
    assert consumer in contract.python_adopter_dirs(REPO)
    for name in (*contract.PYTHON_FILES, *contract.SUPPORT_FILES):
        assert (consumer / "scripts" / name).read_bytes() == (
            REPO / "libs" / "installer-engine" / name
        ).read_bytes()
    for name in (
        "versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_source.py",
        "versioned_runtime_construction.py", "versioned_runtime_native.ps1",
        "versioned_runtime_publication.py",
    ):
        assert (consumer / "scripts" / name).read_bytes() == (
            REPO / "libs" / "versioned-runtime" / name
        ).read_bytes()


def test_manager_helper_sync_detects_missing_or_foreign_bytes(tmp_path, monkeypatch):
    module = _sync()
    canonical = tmp_path / "libs" / "installer-engine"
    canonical.mkdir(parents=True)
    consumer = tmp_path / "worktree-manager"
    consumer.mkdir()
    for name in (*contract.PYTHON_FILES, *contract.SUPPORT_FILES):
        (canonical / name).write_text(f"canonical {name}", encoding="utf-8")
    monkeypatch.setattr(module, "REPO", tmp_path)
    monkeypatch.setattr(module, "CANONICAL_DIR", canonical)
    monkeypatch.setattr(module, "ADOPTERS", ())
    monkeypatch.setattr(module, "PYTHON_ADOPTERS", ())
    monkeypatch.setattr(contract, "PYTHON_ADOPTERS", ())
    assert module.verify()
    written = module.sync()
    assert len(written) == len(contract.PYTHON_FILES) + len(contract.SUPPORT_FILES)
    assert module.verify() == []
    (consumer / "scripts" / "installer_engine.py").write_text("foreign", encoding="utf-8")
    assert any("differs from canonical" in error for error in module.verify())


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_bare_bootstrap_does_not_resolve_a_project_before_construction(extension):
    text = (REPO / "worktree-manager" / f"bootstrap.{extension}").read_text(encoding="utf-8")
    assert "uv run" not in text
    assert "uv python find --no-project" in text
    assert "runtime_install.py" in text


def test_recovery_fingerprints_match_manager_owned_templates():
    source = REPO / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "manager_launcher_ownership.py"
    expected = next(
        ast.literal_eval(node.value) for node in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_OWNED_STUBS" for target in node.targets)
    )
    script = (
        "import sys,json,hashlib;"
        "sys.path.insert(0,sys.argv[1]);"
        "from worktree_manager import runtime_launchers as r;"
        "print(json.dumps({name:hashlib.sha256(getattr(r,fn)().encode()).hexdigest() "
        "for name,fn in [('worktree-manager.cmd','_cmd_binstub'),"
        "('worktree-manager.ps1','_ps1_binstub'),('worktree-manager','_sh_binstub')]}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(REPO / "worktree-manager" / "src")],
        capture_output=True, text=True, check=True, timeout=10,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    assert all(digest in expected[name] for name, digest in json.loads(result.stdout).items())
