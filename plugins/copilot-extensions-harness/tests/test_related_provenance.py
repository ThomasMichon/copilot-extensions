from pathlib import Path

import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_copilot_extensions_related_provenance_is_shipped():
    path = PLUGIN_ROOT / ".agent-worktrees" / "related.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    entry = data["related"]["copilot-extensions"]
    assert entry["role"] == "tooling"
    assert entry["locus"]["preferred"] == "local"
    assert entry["delegate"]["via"] == "none"
    assert "primary" not in data
    assert "ownership" not in entry
