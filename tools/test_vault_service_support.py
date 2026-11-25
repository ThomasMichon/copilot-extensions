"""Vault's prepared service closure ships and retires through existing owners."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import materialize_installer_engine as materializer

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "libs/installer-engine/installer_engine_uninstall.py"
SPEC = importlib.util.spec_from_file_location("_vault_uninstall_profile", SOURCE)
assert SPEC and SPEC.loader
_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = _module
SPEC.loader.exec_module(_module)
MODULE = cast(Any, _module)


def test_vault_profile_removes_service_closure_but_preserves_unknown_siblings(tmp_path: Path) -> None:
    payload = REPO / "plugins/agent-vault"
    profile = MODULE.profile_for(payload, "agent-vault", None)
    root = tmp_path / "runtime"
    names = (
        "vault-service.sh", "vault-cache-service.sh", "vault-service-uv",
        "bootstrap-uv.sh", "native_owned.sh", "daemon_instance_runtime.pyz",
        "bootstrap-uv.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe", "operator-helper.sh",
    )
    for name in names:
        path = root / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture code")
    operations = MODULE._plan(payload, root, profile, (MODULE.Target(root, (), ()),))
    selected = {directory / name for directory, entries in operations for name in entries}
    assert {root / "bin" / name for name in names[:-1]} <= selected
    assert root / "bin" / names[-1] not in selected


def test_vault_materializes_canonical_prepared_service_support(tmp_path: Path) -> None:
    consumer = REPO / "plugins/agent-vault"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    names = frozenset({
        "bootstrap-uv.sh", "installer-instance-python-version.txt", "native_owned.sh",
        "installer_engine_uninstall.py", "bootstrap-uv.ps1", "bootstrap-capture.ps1",
        "bootstrap-capture.exe",
    })
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
