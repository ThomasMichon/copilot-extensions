from __future__ import annotations

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "check-install-contract.py"
PLUGIN = REPO / "plugins" / "agent-pull-requests" / "scripts"

_SPEC = importlib.util.spec_from_file_location("check_install_contract", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


def test_agent_pull_requests_wrapper_counts_as_real_engine_usage() -> None:
    ps1 = (PLUGIN / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "install.sh").read_text(encoding="utf-8")

    assert checker._uses_installer_engine(ps1, "ps1") is True
    assert checker._uses_engine_uv_install(ps1, "ps1") is True
    assert checker._uses_engine_manifest_writer(ps1, "ps1") is True

    assert checker._uses_installer_engine(sh, "sh") is True
    assert checker._uses_engine_uv_install(sh, "sh") is True
    assert checker._uses_engine_manifest_writer(sh, "sh") is True


def test_engine_usage_bypass_comments_do_not_count() -> None:
    ps1 = """
# . (Join-Path $PSScriptRoot 'installer-engine.ps1')
# Invoke-UvPipInstallResilient -Arguments @('--python', 'x')
$note = "Write-DeployManifest should not count here"
"""
    sh = """
# source "$SCRIPT_DIR/installer-engine.sh"
# invoke_uv_pip_install_resilient "$UV_CMD" --python "$VENV_PYTHON"
note="write_deploy_manifest should not count here"
"""

    assert checker._uses_installer_engine(ps1, "ps1") is False
    assert checker._uses_engine_uv_install(ps1, "ps1") is False
    assert checker._uses_engine_manifest_writer(ps1, "ps1") is False

    assert checker._uses_installer_engine(sh, "sh") is False
    assert checker._uses_engine_uv_install(sh, "sh") is False
    assert checker._uses_engine_manifest_writer(sh, "sh") is False


def test_engine_usage_requires_a_real_function_call() -> None:
    ps1 = """
. (Join-Path $PSScriptRoot 'installer-engine.ps1')
$doc = "Invoke-UvPipInstallResilient"
"""
    sh = """
source "$SCRIPT_DIR/installer-engine.sh"
note="invoke_uv_pip_install_resilient"
"""

    assert checker._uses_installer_engine(ps1, "ps1") is True
    assert checker._uses_engine_uv_install(ps1, "ps1") is False

    assert checker._uses_installer_engine(sh, "sh") is True
    assert checker._uses_engine_uv_install(sh, "sh") is False
