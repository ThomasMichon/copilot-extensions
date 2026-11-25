"""Export original package-tree units, retaining the real-checkout CLI smoke."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_no_agent_machines_packages.py"
_spec = importlib.util.spec_from_file_location("promotion_original_no_machine_packages_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

for _name, _function in vars(_original).items():
    if _name.startswith("test_") and _name != "test_real_repo_checkout_is_clean":
        assert _name not in globals(), f"duplicate original package-tree test: {_name}"
        globals()[_name] = _function
