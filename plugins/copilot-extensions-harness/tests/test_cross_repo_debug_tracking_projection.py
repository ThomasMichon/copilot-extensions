"""Contract test for the cross-repo-debug-tracking static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "cross-repo-debug-tracking.instructions.md"


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    by_id = {p["id"]: p for p in declaration["projections"] if isinstance(p, dict)}
    assert "cross-repo-debug-tracking" in by_id
    projection = by_id["cross-repo-debug-tracking"]
    assert projection["template"] == (
        "instructions/cross-repo-debug-tracking.instructions.md"
    )
    assert projection["destination"] == (
        ".github/instructions/copilot-extensions-harness/"
        "cross-repo-debug-tracking.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n')
    assert "copilot-extensions-harness@" in content
    assert "related resolve" in content
    assert "working-cross-repo" in content
    assert "cross-link" in content
    # No live/session/host state -- a checked-in static fallback must never
    # embed a resolved path or session identifier.
    for forbidden in (
        "COPILOT_AGENT_SESSION_ID",
        "session-state",
        "C:\\",
        "/home/",
    ):
        assert forbidden not in content


def test_postmerge_freshness_belongs_to_consented_maintenance():
    content = " ".join(_TEMPLATE.read_text(encoding="utf-8").split())
    assert "adopted, consented maintenance" in content
    assert "normally once daily" in content
    assert "permissionless local rendering" in content
    assert "reviewed offline fallback" in content
    assert "missing/corrupt locked artifacts remain blocking" in content
    assert "primed/pending, not deployed" in content
    assert "explicit rollout authorization" in content
    assert "immediately force-update" not in content
    assert "before ending your turn" not in content


def test_contribution_rollout_preserves_authorization_and_running_state_proof():
    path = _PLUGIN / "skills" / "contributing-to-copilot-extensions" / "SKILL.md"
    step = path.read_text(encoding="utf-8").split("8. **", 1)[1].split("\n##", 1)[0]
    normalized = " ".join(step.split())
    assert "explicit rollout authorization" in normalized
    assert "safety/permission gates" in normalized
    assert "required deployment workflow" in normalized
    assert "running system reflects the change" in normalized
    assert "Adopted, consented maintenance" in normalized
    assert "not deployed" in normalized
    assert "deployment completion obligation" in normalized
    assert "<repo> update" in normalized


def test_cross_linking_preserves_public_traceability_without_private_publication():
    content = " ".join(_TEMPLATE.read_text(encoding="utf-8").split())
    assert "cross-links in both directions only when both trackers and artifacts are public" in content
    assert "private downstream tracker may link to public upstream work" in content
    assert "public upstream artifacts must never receive private links, IDs or context" in content
    assert "private symptom/rationale downstream" in content
    assert "public report self-contained" in content
    assert "local tracking issue in the upstream one" not in content
