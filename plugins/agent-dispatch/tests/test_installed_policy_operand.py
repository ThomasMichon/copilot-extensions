"""Use the shipped helper without registering or observing native tasks."""
import json
import os
from pathlib import Path

import pytest

from agent_dispatch import installation_policy


@pytest.mark.skipif(os.name != "nt", reason="Windows scheduling helper")
@pytest.mark.parametrize("extended", [False, True])
def test_installed_policy_operand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extended: bool,
) -> None:
    module = Path(installation_policy.__file__).resolve()
    if extended:
        monkeypatch.setattr(installation_policy, "__file__", "\\\\?\\" + str(module))
    selector = tmp_path / "installation.json"
    selector.write_text(json.dumps({"version": 1, "installation": {
        "root": str(tmp_path), "local": {
            "windows_scheduled_task": "preserve", "start_coordinator": False, "start_supervisor": False,
        },
    }}), encoding="utf-8")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALLATION_CONFIG", str(selector))
    result = installation_policy.windows_policy(tmp_path, observe_supervisor=False)
    assert result == {
        "windows_scheduled_task": "preserve", "start_coordinator": False, "start_supervisor": False,
    }
    monkeypatch.setattr(installation_policy, "__file__", str(tmp_path / module.name))
    with pytest.raises(ValueError, match="not the installed file"):
        installation_policy.windows_policy(tmp_path)
