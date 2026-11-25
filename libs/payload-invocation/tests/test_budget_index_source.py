"""Export the original source-only Budget index controls without app setup."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

_source = Path(__file__).resolve().parents[3] / "plugins/budget-guidance/tests/test_installers.py"
_spec = importlib.util.spec_from_file_location("_budget_index_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
test_native_index_reader_matches_uv_working_directory = _original.test_native_index_reader_matches_uv_working_directory
test_system_flag_pins_provider_in_actual_launch_context = _original.test_system_flag_pins_provider_in_actual_launch_context
test_posix_installer_bootstraps_with_uv_and_no_ambient_python = _original.test_posix_installer_bootstraps_with_uv_and_no_ambient_python
