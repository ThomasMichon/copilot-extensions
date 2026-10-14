"""Fresh registration contract without provisioning a service."""

from pathlib import Path
import re

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
NAMES = ("agent-dispatch.json", "agent-dispatch-maintenance.json")


@pytest.mark.guard
def test_both_platform_installers_defer_to_attributed_picker_materialization():
    patterns = {
        "install.ps1": r"function Register-PickerPivot \{(.*?)\n\}",
        "install.sh": r"_register_pivot\(\) \{(.*?)\n\}",
    }
    for script, pattern in patterns.items():
        text = (PLUGIN / "scripts" / script).read_text()
        body = re.search(pattern, text, re.DOTALL).group(1)
        assert "attributed" in body
        assert "Copy-Item" not in body and "cp -f" not in body
        assert ".agent-worktrees" not in body
    assert all((PLUGIN / "pivots" / name).is_file() for name in NAMES)
