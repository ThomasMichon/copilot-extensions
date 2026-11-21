"""The canonical child environment preserves the actual dependency-policy call."""
from __future__ import annotations

import pytest
from install_contract_guard import (
    _incomplete_dependency_policy_calls,
    _uses_dependency_policy_install,
)


@pytest.mark.parametrize(("prefix", "admitted"), [
    ("", True), ("invoke_uv_bootstrap_environment ", True), ("unrelated_wrapper ", False),
], ids=["direct", "isolated", "unknown"])
def test_dependency_policy_admits_only_known_environment_prefix(prefix: str, admitted: bool) -> None:
    source = (
        prefix + '"$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" '
        '--uv "$UV" -- --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet\n'
    )
    assert _uses_dependency_policy_install(source, "sh") is admitted
    assert _incomplete_dependency_policy_calls(source) == 0
