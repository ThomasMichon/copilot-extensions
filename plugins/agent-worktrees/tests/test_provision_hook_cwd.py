"""Provisioning hooks must not retain the installed plugin as their cwd."""

from __future__ import annotations

import json
from pathlib import Path


_PLUGIN = Path(__file__).resolve().parents[1]


def _provision_hook() -> dict[str, object]:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    return next(
        hook
        for hook in hooks["hooks"]["sessionStart"]
        if "provision-check" in str(hook)
    )


def test_provision_hook_leaves_payload_cwd_before_running_script():
    hook = _provision_hook()
    assert "Set-Location -LiteralPath $HOME" in hook["powershell"]
    assert 'cd "$HOME"' in hook["bash"]


def test_detached_provision_worker_runs_from_home():
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    assert "-WorkingDirectory $HOME" in ps1
    assert 'cd "$HOME" || exit 0' in sh
