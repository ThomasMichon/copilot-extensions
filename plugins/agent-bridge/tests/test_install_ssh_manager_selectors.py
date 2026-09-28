"""Windows installer selectors must target the shipped SSH distribution."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.guard


def _ssh_manager_pyproject() -> Path:
    """The real ``ssh-manager`` ``pyproject.toml`` this dev checkout would
    resolve, mirroring ``install.ps1``'s own ``Resolve-VendoredLib``
    two-tier resolution: a local marketplace-layout copy under
    ``PLUGIN/libs/ssh-manager`` first, else the canonical `uv`-editable
    reference at the repo-root ``libs/ssh-manager`` (vendor-pointer-
    generalization effort, Phase 1 -- this plugin's own vendored copy no
    longer exists in a `dev`-branch checkout)."""
    local = PLUGIN / "libs" / "ssh-manager" / "pyproject.toml"
    if local.is_file():
        return local
    canonical = PLUGIN.parents[1] / "libs" / "ssh-manager" / "pyproject.toml"
    assert canonical.is_file(), (
        f"neither {local} nor {canonical} exists -- ssh-manager is "
        "unresolvable from this checkout"
    )
    return canonical


def test_windows_ssh_manager_selectors_match_vendored_distribution():
    metadata = _ssh_manager_pyproject().read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", metadata)
    assert project is not None
    name = re.search(r'(?m)^name\s*=\s*"([^"]+)"\s*$', project.group(1))
    assert name is not None
    distribution = name.group(1)

    installer = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    commands = [
        line
        for line in installer.replace("`\n", " ").splitlines()
        if "Invoke-UvPipInstallResilient" in line and "$SshManagerDir" in line
    ]
    assert len(commands) == 2, "Expected both install and update SSH commands"
    for command in commands:
        selectors = re.findall(
            r"--(reinstall|refresh)-package['\"]?\s*,?\s*['\"]([\w.-]+)['\"]", command
        )
        assert sorted(selectors) == [
            ("refresh", distribution),
            ("reinstall", distribution),
        ], command
