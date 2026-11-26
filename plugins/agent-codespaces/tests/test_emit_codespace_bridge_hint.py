"""CodeSpace guidance selects profile startup rather than exact conversation lookup."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import shlex

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "emit_codespace_map.py"
_SPEC = importlib.util.spec_from_file_location("codespace_bridge_hint", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_codespace_map_selects_legacy_startup() -> None:
    context = _MODULE._render([{
        "name": "project", "vessel": "example/project", "workspace_folder": "", "machine": "",
    }])
    commands = re.findall(r"`(agent-bridge send[^`]*)`", context)
    assert [shlex.split(command) for command in commands] == [
        ["agent-bridge", "send", "codespace:<name>", "<task>", "--legacy-selector"],
    ]
