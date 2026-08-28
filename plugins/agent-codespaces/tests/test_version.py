from __future__ import annotations

import json
from pathlib import Path

def test_source_fallback_matches_plugin_manifest_exactly():
    plugin = Path(__file__).resolve().parents[1]
    version = json.loads(
        (plugin / "plugin.json").read_text(encoding="utf-8")
    )["version"]

    source = (
        plugin / "src" / "agent_codespaces" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert f'__version__ = "{version}"' in source
