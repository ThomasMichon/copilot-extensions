"""Published consumer/maintainer surfaces, not a model-behavior evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


ROOT = Path(__file__).resolve().parents[3]
PLUGINS = ROOT / "plugins"
BASE = PLUGINS / "copilot-extensions-harness"
MAINTAINER = PLUGINS / "copilot-extensions-maintainer"


def _json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_base_is_consumer_only_and_maintainer_is_optional() -> None:
    base = _json(BASE / "plugin.json")
    maintenance = _json(MAINTAINER / "plugin.json")
    assert base["runtimeScope"] == "none"
    assert not any(base.get(key) for key in ("hooks", "agents", "commands", "mcpServers"))
    assert {p.name for p in (BASE / "skills").iterdir()} == {
        "diagnosing-copilot-extensions", "checking-copilot-extensions-release",
    }
    assert not any((BASE / name).exists() for name in ("hooks.json", "agents", "scripts"))
    assert maintenance["defaultEnabled"] is False
    catalog = {p["name"]: p for p in _json(ROOT / ".github/plugin/marketplace.json")["plugins"]}
    assert catalog[maintenance["name"]]["defaultEnabled"] is False
    assert catalog[maintenance["name"]]["version"] == maintenance["version"]
    assert catalog[base["name"]]["version"] == base["version"]
    assert (MAINTAINER / "agents/clean-room-judge.agent.md").is_file()
    assert (MAINTAINER / "agents/copilot-extensions-reviewer.agent.md").is_file()


def test_source_adapters_resolve_without_enabling_maintainer() -> None:
    for skill in ("contributing-to-copilot-extensions", "validating-in-clean-room"):
        canonical = MAINTAINER / "skills" / skill / "SKILL.md"
        adapter = ROOT / ".github/skills" / skill / "SKILL.md"
        assert canonical.is_file()
        assert f"plugins/copilot-extensions-maintainer/skills/{skill}/SKILL.md" in adapter.read_text()
        assert not (BASE / "skills" / skill).exists()
    assert "does not require this plugin to be enabled" in (MAINTAINER / "README.md").read_text()
    procedure = " ".join(
        (MAINTAINER / "skills/contributing-to-copilot-extensions/SKILL.md").read_text().split()
    )
    assert "Account for every failure in the assigned validation scope" in procedure
    assert "explicitly assigned harness-focused test-maintenance charter" in procedure
    assert "Required CI remains a merge gate" in procedure
    assert "Fix every failing test you encounter" not in procedure


def test_projection_sources_are_bounded_and_resolve_to_their_owner() -> None:
    destinations = set()
    for plugin in (BASE, MAINTAINER):
        declaration = _json(plugin / "instruction-projections.json")
        for projection in declaration["projections"]:
            template = plugin / projection["template"]
            assert template.is_file()
            assert len(template.read_bytes()) <= 4096
            destination = projection["destination"]
            assert f"/{plugin.name}/" in destination
            assert destination not in destinations
            destinations.add(destination)
    assert {p["id"] for p in _json(BASE / "instruction-projections.json")["projections"]} == {
        "consumer-boundary",
    }


def test_source_repair_extension_is_opt_in_and_keeps_the_consumer_gate() -> None:
    skill = MAINTAINER / "skills/contributing-to-copilot-extensions"
    procedure = (skill / "SKILL.md").read_text(encoding="utf-8")
    reference = skill / "references/infrastructure-repair.md"
    assert "references/infrastructure-repair.md" in procedure
    extension = " ".join(reference.read_text(encoding="utf-8").split())
    for term in (
        "operator explicitly adopts this extension",
        "not the base role's default",
        "incidental tooling failures during another task",
        "original operator source",
        "never by editing a deployed slot",
        "ordinary diagnosis, controlled local experiments",
        "do not need a new generic permission prompt",
        "Preserve separately required deployment",
        "first failing boundary",
        "normal review, required CI, merge and release",
        "retry the original failing installation path",
        "An unproved theory is not an operator hold",
        "failed operation and consumer checks pass",
        "standing maintenance role still ends only on retirement or accepted transfer",
    ):
        assert term in extension
    assert "infrastructure-repair" not in (BASE / "instructions/consumer-boundary.instructions.md").read_text()
