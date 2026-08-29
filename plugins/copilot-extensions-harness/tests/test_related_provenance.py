from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_copilot_extensions_related_provenance_is_shipped():
    path = PLUGIN_ROOT / ".agent-worktrees" / "related.yaml"
    text = path.read_text(encoding="utf-8")

    assert "\n  copilot-extensions:\n" in text
    assert "\n    role: tooling\n" in text
    assert "Use a public GitHub\n      account" in text
    assert "\n      preferred: local\n" in text
    assert "\n    delegate: { via: none }\n" in text
    assert "\nprimary:" not in text
