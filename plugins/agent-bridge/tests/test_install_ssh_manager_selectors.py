"""Windows installer selectors must target the shipped SSH distribution."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.guard


def test_windows_ssh_manager_selectors_match_vendored_distribution():
    metadata = (PLUGIN / "libs" / "ssh-manager" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
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
