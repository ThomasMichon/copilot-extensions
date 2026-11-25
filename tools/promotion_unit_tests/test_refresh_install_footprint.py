"""Producer refresh bytes remain stable across working trees and Git blobs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_refresh_emits_stable_lf_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "refresh_footprint_test_subject", ROOT / "tools/refresh-install-footprint.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "tools/refresh-install-footprint.py"))
    monkeypatch.setattr(module.guard, "ADOPTERS", ("agent-mcp",))
    monkeypatch.setattr(sys, "argv", ["refresh-install-footprint.py"])

    canonical = tmp_path / "libs/installer-engine/installer_engine_footprint.py"
    canonical.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "libs/installer-engine/installer_engine_footprint.py", canonical)
    payload = tmp_path / "plugins/agent-mcp"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "init.sh").write_bytes(b"fixture script\n")
    (payload / "src").mkdir()
    (payload / "src/module.py").write_bytes(b"VALUE = 1\n")
    path = scripts / "uninstall-profile.json"
    original = json.loads((ROOT / "plugins/agent-mcp/scripts/uninstall-profile.json").read_text())
    path.write_bytes((json.dumps(original, indent=2).replace("\n", "\r\n") + "\r\n").encode())

    assert module.main() == 0
    first = path.read_bytes()
    assert b"\r" not in first
    assert first.endswith(b"\n")
    refreshed = json.loads(first)
    assert refreshed["revision"] == original["revision"]
    assert refreshed["contract"] == original["contract"]
    assert refreshed["producers"] != original["producers"]
    assert module.main() == 0
    assert path.read_bytes() == first
