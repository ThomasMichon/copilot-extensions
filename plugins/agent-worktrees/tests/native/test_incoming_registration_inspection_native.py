"""Opt-in real incoming construction with an unusable selected predecessor."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
import yaml

from runtime_install_fixture import isolate_account_profile, run_native_installer
@pytest.mark.timeout(300)
def test_incoming_inspection_ignores_broken_prior_runtime_and_preserves_state(
    tmp_path,
):
    uv = shutil.which("uv")
    assert uv, "native construction requires the selected UV tool"
    home = tmp_path / "home"
    home.mkdir()
    anchor = tmp_path / "selected checkout"
    anchor.mkdir()
    payload = Path(__file__).resolve().parents[2]
    if os.name != "nt":
        with pytest.MonkeyPatch.context() as patch:
            patch.syspath_prepend(str(payload / "scripts"))
            spec = importlib.util.spec_from_file_location(
                "incoming_native_fixture_engine", payload / "scripts" / "installer_engine.py",
            )
            module = importlib.util.module_from_spec(spec)
            patch.setitem(sys.modules, spec.name, module)
            spec.loader.exec_module(module)
            payload = module.freeze_inputs(payload, tmp_path / "source").plugin
        isolate_account_profile(payload, home)
    root = home / ".agent-worktrees"
    old = root / "versions" / "0.0.1"
    old.mkdir(parents=True)
    (old / "unusable-runtime").write_bytes(b"never execute or repair")
    (root / "current-version").write_bytes(b"0.0.1\n")
    (root / "deploy-manifest.json").write_bytes(b"historical malformed owner\n")
    (root / "repos.yaml").write_text(yaml.safe_dump({"repos": {
        "custom-control": {
            "class": "worktree", "agent": False,
            "windows" if os.name == "nt" else "linux": str(anchor),
        },
    }}), encoding="utf-8")
    (root / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"custom-control": {"expose_agent": True}},
    }), encoding="utf-8")
    (home / "knowledge-binding.yaml").write_bytes(b"knowledge_repo: retained\n")
    before = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*") if path.is_file()
    }
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_WORKTREES_", "WORKTREE_MANAGER_"))
        and key not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    }
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary),
        APPDATA=str(home / "roaming"), LOCALAPPDATA=str(home / "local"),
        XDG_CONFIG_HOME=str(home / ".config"),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1", AGENT_WORKTREES_STATUS_MONITOR="0",
        PYTHONDONTWRITEBYTECODE="1",
    )
    entry = [
        sys.executable, "-I", "-B", str(payload / "scripts" / "inspect_registration.py"),
        "--anchor", str(anchor), "--default-name", "control", "--uv", uv,
    ]
    started = time.monotonic()
    print("Incoming registration inspection: construct and read (180 seconds)", flush=True)
    try:
        result = run_native_installer(entry, env=environment, timeout=180)
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"Incoming construction/read timed out after {time.monotonic() - started:.3f}s:\n"
            f"{error.stdout or ''}\n{error.stderr or ''}",
        )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["contract"] == "copilot-extensions.project-registration-inspection"
    assert data["contract_version"] == 1 and data["registered"] is True
    assert data["project"] == "custom-control" and data["agent"] is False
    assert {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*") if path.is_file()
    } == before
    assert not list(temporary.glob("worktrees-registration-inspection-*"))
