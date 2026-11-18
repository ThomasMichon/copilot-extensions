"""Published review instructions preserve assignment scope, not model evidence."""

from pathlib import Path
import json
from types import SimpleNamespace

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


def test_review_findings_do_not_authorize_edits_or_upstream_publication() -> None:
    text = " ".join(
        (PLUGIN / "skills/reviewing-customizations/SKILL.md").read_text().split()
    )
    for clause in (
        "A finding does not assign source work or authorize a report",
        "A review-only request stays read-only",
        "Treat external-plugin findings as findings, not new assignments",
        "do not wait for an upstream fix",
        "External source fixes require a separate assignment",
        "do not auto-enable it to act on a finding",
        "Repository docs and native adapters remain usable without enabling",
        "copilot-extensions-maintainer:contributing-to-copilot-extensions",
    ):
        assert clause in text
    assert "Treat external-plugin findings as upstream work" not in text


def test_external_collision_warning_does_not_assign_source_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = PLUGIN / "skills/reviewing-customizations/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import scan_skills
    from scan_plugin_sources import PluginSource

    root = tmp_path / "consumer"
    owned = root / ".github/skills/owned/SKILL.md"
    external = tmp_path / "external/skills/foreign/SKILL.md"
    for path in (owned, external):
        path.parent.mkdir(parents=True)
        path.write_text(
            f"---\nname: {path.parent.name}\ndescription: >\n"
            "  Trigger phrases include:\n  - 'same request'\n---\n",
        )
    findings: list[tuple[str, str]] = []
    report = SimpleNamespace(add=lambda severity, check, path, message: findings.append((check, message)))
    source = PluginSource(
        origin="example/external", controlled=False,
        skills_root=external.parent.parent, source="https://example.com/tool",
    )
    scan_skills.scan_skills(root, report, [source])
    warnings = [message for check, message in findings if check == "trigger-collision"]
    assert len(warnings) == 1
    for clause in (
        "A finding grants no edit or publication authority",
        "workaround only within assigned scope",
        "offer a bug report at the established destination",
        "do not automatically file an issue/PR",
        "or wait for an upstream fix",
        "A separately assigned source fix",
        "`<repo>-maintainer` procedures",
        "do not auto-enable a plugin",
    ):
        assert clause in warnings[0]
    assert "OR file an issue/PR upstream" not in warnings[0]


def test_authoring_standard_separates_product_delivery_from_tool_maintenance() -> None:
    text = " ".join(
        (PLUGIN / "skills/authoring-harness-plugins/SKILL.md").read_text().split()
    )
    for clause in (
        "supporting tooling/harness source stays frozen",
        "The product may span repositories, including implementation libraries",
        "fork/PR delivery do not need another generic prompt",
        "defaultEnabled: false",
        "enabling this optional package",
        "Never patch installed package logic",
        "belongs in the optional maintainer package",
        "an error does not authorize an update or reset",
    ):
        assert clause in text
    base_structure = text.split("plugins/<repo>-harness/", 1)[1].split("plugins/<repo>-maintainer/", 1)[0]
    assert "contributing-to-<repo>/SKILL.md" not in base_structure
    assert "exactly two sanctioned responses" not in text


def test_existing_consumer_checked_in_projection_retirement_rehearsal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(PLUGIN / "skills/reviewing-customizations/scripts"))
    import instruction_projections as projections

    retired = {
        "contribution-boundary", "cross-repo-debug-tracking",
        "commented-review-verdict", "validate-and-promote-triage",
    }
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy = tmp_path / "legacy"
    (legacy / "instructions").mkdir(parents=True)
    (legacy / "plugin.json").write_text(json.dumps({
        "name": "copilot-extensions-harness", "version": "0.1.0",
    }))
    declaration = []
    for source_id in sorted(retired):
        template = f"instructions/{source_id}.instructions.md"
        (legacy / template).write_text('---\napplyTo: "**"\n---\nLegacy maintenance.\n')
        declaration.append({
            "id": source_id, "deliveryMode": "selector", "template": template,
            "destination": f".github/instructions/copilot-extensions-harness/{source_id}.instructions.md",
            "customizationKind": "instructions", "applyTo": "**", "legacyMarkers": [],
        })
    (legacy / "instruction-projections.json").write_text(json.dumps({
        "schema": "copilot-extensions.instruction-projections", "version": 1,
        "projections": declaration,
    }))

    def source(payload: Path) -> SimpleNamespace:
        return SimpleNamespace(
            origin="copilot-extensions/copilot-extensions-harness",
            payload_root=payload, skills_root=payload / "skills",
            controlled=False, source="", version="",
        )

    assert projections.sync_repository(workspace, [source(legacy)]).blocking == 0
    current = PLUGIN.parent / "copilot-extensions-harness"
    assert projections.sync_repository(workspace, [source(current)]).blocking == 0
    lock_path = workspace / ".github/copilot/context-projections.json"
    lock = json.loads(lock_path.read_text())
    assert retired <= {entry["sourceId"] for entry in lock["projections"]}
    guide = (current / "references/consumer-migration.md").read_text()
    for source_id in retired:
        assert f"`{source_id}`" in guide
    keep = workspace / "keep.txt"
    keep.write_text("unrelated workspace content")
    retained = []
    for entry in lock["projections"]:
        if entry["plugin"] == "copilot-extensions-harness@copilot-extensions" and entry["sourceId"] in retired:
            for name in (entry["destination"], entry["fallback"]["destination"]):
                artifact = workspace / name
                assert entry["plugin"] in artifact.read_text()
                artifact.unlink()
        else:
            retained.append(entry)
    lock["projections"] = retained
    lock_path.write_bytes(projections._canonical_json(lock, pretty=True))
    assert projections.sync_repository(workspace, [source(current)]).blocking == 0
    assert {entry["sourceId"] for entry in json.loads(lock_path.read_text())["projections"]} == {"consumer-boundary"}
    for source_id in retired:
        assert not (workspace / f".github/instructions/copilot-extensions-harness/{source_id}.instructions.md").exists()
        assert not (workspace / f".github/copilot/context-fallbacks/copilot-extensions-harness/{source_id}.md").exists()
    assert keep.read_text() == "unrelated workspace content"
