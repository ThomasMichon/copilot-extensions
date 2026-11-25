"""Exercise the process-owner boundary with synthetic native file adapters."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_path = Path(__file__).resolve().parents[1] / "test_installer_process_inputs.py"
_spec = importlib.util.spec_from_file_location(
    "promotion_installer_process_inputs", _path
)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

engine = _original.engine


@pytest.fixture(autouse=True)
def synthetic_native_files(monkeypatch: pytest.MonkeyPatch, engine: ModuleType) -> None:
    import installer_dependency_policy as policy
    import installer_wheel_snapshot as snapshots

    def private_root(parent: Path) -> Path:
        root = parent / "inputs"
        root.mkdir(mode=0o700)
        return root

    monkeypatch.setattr(policy, "_private_retained_root", private_root)
    monkeypatch.setattr(snapshots, "_open", lambda path: path.open("rb"))


_SELECTED = (
    "test_closed_wheel_failed_import_retires_partial_namespace",
    "test_ordinary_child_keeps_frozen_source_and_relative_imports",
    "test_closed_wheel_child_retains_real_process_package_until_settlement",
    "test_closed_wheel_retries_share_retained_process_package",
    "test_closed_wheel_owner_refuses_before_launch",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
