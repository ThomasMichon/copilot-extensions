"""Actor-free delivery-reader packaging through the canonical toolkit generator."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_default_toolkit_packages_delivery_reader_and_its_local_imports(tmp_path, monkeypatch):
    source = ROOT / "libs" / "payload-invocation" / "runtime_admission.py"
    spec = importlib.util.spec_from_file_location("_delivery_toolkit_packager", source)
    assert spec and spec.loader
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tmp_path / "libs").mkdir()
    (tmp_path / "worktree-manager" / "src").mkdir(parents=True)
    for name in packager.ENTRY_MODULES:
        (tools / (name + ".py")).write_text("VALUE = 1\n", encoding="utf-8")
    (tools / "runtime_profile_delivery.py").write_text(
        "import fixture_delivery_owner\n", encoding="utf-8",
    )
    (tools / "fixture_delivery_owner.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(packager, "_RESOURCES", ())
    files = packager.expected_files(tmp_path)
    inventory = json.loads(files[Path("toolkit.json")])
    assert "runtime_profile_delivery" in inventory["entryModules"]
    assert files[Path("tools/runtime_profile_delivery.py")] == "import fixture_delivery_owner\n"
    assert files[Path("tools/fixture_delivery_owner.py")] == "VALUE = 2\n"
