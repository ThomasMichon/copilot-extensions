"""The canonical child environment preserves the actual dependency-policy call."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "_uv_policy_install_guard", ROOT / "tools/check-install-contract.py",
)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


@pytest.mark.parametrize(("prefix", "admitted"), [
    ("", True), ("invoke_uv_bootstrap_environment ", True), ("unrelated_wrapper ", False),
], ids=["direct", "isolated", "unknown"])
def test_dependency_policy_admits_only_known_environment_prefix(prefix: str, admitted: bool) -> None:
    source = (
        prefix + '"$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" '
        '--uv "$UV" -- --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet\n'
    )
    assert guard._uses_dependency_policy_install(source, "sh") is admitted
    assert guard._incomplete_dependency_policy_calls(source) == 0
