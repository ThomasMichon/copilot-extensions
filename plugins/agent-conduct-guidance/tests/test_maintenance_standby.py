"""Declarative standby delivery, not a runtime scheduler or model evaluation."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from test_multi_agent_rules_of_engagement import _projection_source, projections

pytestmark = pytest.mark.guard
PLUGIN = Path(__file__).resolve().parents[1]
DESTINATION = ".github/instructions/agent-conduct-guidance/maintenance-standby.instructions.md"
TEMPLATE = PLUGIN / "instructions/maintenance-standby.instructions.md"


def test_normal_projection_contains_the_complete_bounded_standby_policy(tmp_path: Path) -> None:
    result = projections.Result(operation="test")
    specs, unknown = projections._load_specs(tmp_path, [_projection_source(PLUGIN)], result)
    assert not unknown and result.blocking == 0, result.findings
    spec, = [item for item in specs if item.source_id == "maintenance-standby"]
    assert spec.destination == DESTINATION and spec.apply_to == "**"
    assert spec.delivery_mode == "selector"
    rendered = projections.render_projection(spec)
    assert spec.template_bytes <= projections.MAX_TEMPLATE_BYTES
    assert rendered.byte_count <= projections.MAX_PROJECTION_BYTES
    assert rendered.marker["deliveryKind"] == "selector"
    assert b"[owner: agent-conduct-guidance]" not in rendered.content
    assert rendered.fallback_content is not None
    text = rendered.fallback_content.decode("utf-8")
    for required in (
        "[owner: agent-conduct-guidance]", "~/.copilot-extensions/maintenance.json",
        "current OS user's home", "ownerSessionId", "your own durable Copilot session ID",
        "startedAt", "sit tight", "exactly one native", "**30 minutes**",
        'status: "complete"', "outstanding obligations", "never shell commands",
        "native scheduled prompts are unavailable", "not an all-clear",
        "do not label a failure intentional maintenance",
    ):
        assert required in text, required


def test_reviewed_standby_survives_payload_removal_without_activating_a_hold(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    shutil.copytree(
        PLUGIN, payload,
        ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache"),
    )
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    result = projections.sync_repository(adopter, [_projection_source(payload)])
    assert result.blocking == 0, result.findings
    destination = adopter / DESTINATION
    before = destination.read_bytes()
    fallback_relative = projections.delivery.fallback_destination(DESTINATION)
    fallback = adopter / fallback_relative
    fallback_before = fallback.read_bytes()
    assert b"copilot-guidance-body-end:v1" not in before
    assert projections.delivery.complete_body(
        fallback_before, projections.delivery.parse_marker(fallback_before),
    )
    assert b"exactly one native" in fallback_before
    shutil.rmtree(payload)
    offline = projections.scan_repository(adopter)
    assert offline.blocking == 0, offline.findings
    assert destination.read_bytes() == before
    assert fallback.read_bytes() == fallback_before
    selected = projections.resolve_instruction_source(adopter, DESTINATION)
    assert selected["selectedPath"] == fallback_relative
    assert not (adopter / ".copilot-extensions/maintenance.json").exists()


def test_notice_example_and_projection_are_inert_and_organization_neutral() -> None:
    example = json.loads((PLUGIN / "examples/maintenance.json").read_text(encoding="utf-8"))
    assert example == {
        "version": 1, "status": "active", "ownerSessionId": "<durable-copilot-session-id>",
        "summary": "Supporting agent tools are intentionally unavailable during maintenance.",
        "startedAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
    }
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["runtimeScope"] == "none"
    assert not any(manifest.get(key) for key in ("hooks", "mcpServers", "commands"))
    content = TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n') and content.isascii()
    for forbidden in ("C:\\", "/home/", "COPILOT_AGENT_SESSION_ID", "session-state/"):
        assert forbidden not in content
