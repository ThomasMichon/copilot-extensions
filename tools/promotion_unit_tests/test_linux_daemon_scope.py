"""Admit only actor-free native-scope selection and compatibility controls."""

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs/agent-procutil/src"))
source = ROOT / "libs/agent-procutil/tests/test_linux_daemon_scope.py"
spec = importlib.util.spec_from_file_location("promotion_original_linux_scope", source)
original = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = original
spec.loader.exec_module(original)

test_native_cleanup_boundary = original.test_native_cleanup_boundary
test_scope_preserves_literal_command_and_child_environment = (
    original.test_scope_preserves_literal_command_and_child_environment
)
test_no_native_cleanup_boundary_keeps_plain_posix_launch = (
    original.test_no_native_cleanup_boundary_keeps_plain_posix_launch
)
test_unknown_native_runner_is_not_inherited_lifetime = (
    original.test_unknown_native_runner_is_not_inherited_lifetime
)
test_missing_native_runner_refuses = original.test_missing_native_runner_refuses
test_other_platforms_do_not_read_linux_cgroups = original.test_other_platforms_do_not_read_linux_cgroups
test_relative_runner_search_uses_daemon_cwd_for_probe_and_launch = (
    original.test_relative_runner_search_uses_daemon_cwd_for_probe_and_launch
)
test_native_probe_timeout_is_an_admission_refusal = original.test_native_probe_timeout_is_an_admission_refusal
