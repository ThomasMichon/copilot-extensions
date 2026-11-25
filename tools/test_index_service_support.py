"""Index service admission ships canonically and has finite nested removal."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

import materialize_installer_engine as materializer

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "libs/installer-engine/installer_engine_uninstall.py"
SPEC = importlib.util.spec_from_file_location("_index_service_uninstall", SOURCE)
assert SPEC and SPEC.loader
_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = _module
SPEC.loader.exec_module(_module)
MODULE = cast(Any, _module)


def test_index_service_support_materializes_from_canonical_owners(tmp_path: Path) -> None:
    consumer = REPO / "plugins/agent-index"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    names = frozenset({"bootstrap-uv.sh", "native_owned.sh", "installer_engine_uninstall.py"})
    log = materializer.materialize_derived_files(
        candidate, canonical_root=REPO, copied_consumer=(consumer, candidate), file_names=names,
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    for name in names:
        canonical = (
            REPO / "libs/agent-procutil/src/agent_procutil" / name
            if name == "native_owned.sh" else REPO / "libs/installer-engine" / name
        )
        assert (candidate / "scripts" / name).read_bytes() == canonical.read_bytes()


def test_index_removal_plans_both_service_closures_but_preserves_user_files(tmp_path: Path) -> None:
    payload = REPO / "plugins/agent-index"
    profile = MODULE.profile_for(payload, "agent-index", None)
    root = tmp_path / "index"
    names = ("service-runtime.sh", "bootstrap-uv.sh", "native_owned.sh",
             "daemon_instance_runtime.pyz", "operator-helper.sh")
    for directory in (root, root / "engine"):
        for name in names:
            path = directory / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic support")
        (directory / "service.env").write_bytes(b"operator configuration")
    targets = (MODULE.Target(root, (), ()), MODULE.Target(root / "engine", (), ()))
    operations = MODULE._plan(payload, root, profile, targets)
    selected = {directory / name for directory, entries in operations for name in entries}
    for directory in (root, root / "engine"):
        assert {directory / "bin" / name for name in names[:-1]} <= selected
        assert directory / "bin" / names[-1] not in selected
        assert directory / "service.env" not in selected
