"""Verify finite removal of generated Bridge ingress without selecting state."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
from typing import Any, cast

import pytest


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_bridge_cleanup_original", ROOT / "tools/test_installer_engine_uninstall.py",
)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
installation = _original.installation


def test_bridge_service_ingress_cleanup_retains_unknown_siblings(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    shutil.copyfile(
        ROOT / "libs/versioned-runtime/versioned_runtime_identity.py",
        payload / "scripts/versioned_runtime_identity.py",
    )
    names = ("bootstrap-uv.sh", "start-agent-bridge")
    for name in names:
        (root / "bin" / name).write_text("generated service support\n", encoding="utf-8")
    monkeypatch.setattr(_original.MODULE, "refuse_consumers", lambda *_: None)
    assert _original._cleanup(
        payload, root, remove_runtime=True, remover=_original._remove,
    )["status"] == "removed"
    assert all(not (root / "bin" / name).exists() for name in names)
    assert (root / "bin/custom").read_bytes() == b"unknown helper"
    assert (root / "config.yaml").read_bytes() == b"configuration"
