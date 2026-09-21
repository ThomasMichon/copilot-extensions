"""Tests for projection-reflect's deterministic-sync decision layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.guard

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import instruction_projections as projections  # noqa: E402
import projection_reflect as reflect  # noqa: E402


def _source(plugin: Path, marketplace: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        origin=f"{marketplace}/{name}",
        payload_root=plugin,
        skills_root=plugin / "skills",
        controlled=False,
        source="",
        version="",
    )


def _write_plugin(
    root: Path,
    marketplace: str,
    name: str,
    *,
    version: str = "1.0.0",
    body: str = "Keep this static fallback useful.\n",
) -> tuple[Path, SimpleNamespace]:
    plugin = root / marketplace / name
    template = plugin / "instructions" / "fallback.instructions.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        '---\napplyTo: "**"\n---\n\n# Fallback\n\n' + body,
        encoding="utf-8",
        newline="\n",
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    (plugin / "instruction-projections.json").write_text(
        json.dumps(
            {
                "schema": projections.DECLARATION_SCHEMA,
                "version": projections.DECLARATION_VERSION,
                "projections": [
                    {
                        "id": "fallback",
                        "template": "instructions/fallback.instructions.md",
                        "destination": f".github/instructions/{name}/fallback.instructions.md",
                        "customizationKind": "instructions",
                        "applyTo": "**",
                        "legacyMarkers": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plugin, _source(plugin, marketplace, name)


# ---- pure classification/decision unit tests --------------------------------


def _finding(check: str):
    return SimpleNamespace(check=check)


def test_classify_findings_splits_plain_from_conflict() -> None:
    findings = [
        _finding("projection-missing"),
        _finding("projection-source-update"),
        _finding("projection-ownership"),
        _finding("projection-local-modification"),
    ]

    result = reflect.classify_findings(findings)

    assert [f.check for f in result.plain] == [
        "projection-missing",
        "projection-source-update",
    ]
    assert [f.check for f in result.conflict] == [
        "projection-ownership",
        "projection-local-modification",
    ]
    assert not result.is_clean


def test_classify_findings_routes_unknown_check_to_conflict() -> None:
    # A check name this module has never seen (e.g. a future addition to
    # instruction_projections.py) must fail closed, not silently pass.
    result = reflect.classify_findings([_finding("projection-brand-new-check")])

    assert result.plain == ()
    assert [f.check for f in result.conflict] == ["projection-brand-new-check"]


def test_classify_findings_all_plain_is_clean() -> None:
    result = reflect.classify_findings(
        [_finding("projection-missing"), _finding("projection-source-update")]
    )

    assert result.is_clean


def test_has_actionable_change_requires_changed_or_lock_updated() -> None:
    assert not reflect.has_actionable_change(changed=[], lock_updated=False)
    assert reflect.has_actionable_change(changed=[], lock_updated=True)
    assert reflect.has_actionable_change(changed=["a"], lock_updated=False)
    assert reflect.has_actionable_change(changed=["a"], lock_updated=True)


def test_marketplace_of_extracts_suffix() -> None:
    assert reflect.marketplace_of("agent-worktrees@copilot-extensions") == (
        "copilot-extensions"
    )
    assert reflect.marketplace_of("no-marketplace-here") == ""


def test_bypass_decision_eligible_when_clean_and_trusted() -> None:
    decision = reflect.bypass_decision(
        findings=[_finding("projection-missing")],
        changed_lock_entries=[{"plugin": "agent-worktrees@copilot-extensions"}],
        trusted_marketplaces=["copilot-extensions"],
    )

    assert decision.eligible
    assert decision.reasons == ()


def test_bypass_decision_blocks_on_conflict_finding() -> None:
    decision = reflect.bypass_decision(
        findings=[_finding("projection-ownership")],
        changed_lock_entries=[{"plugin": "agent-worktrees@copilot-extensions"}],
        trusted_marketplaces=["copilot-extensions"],
    )

    assert not decision.eligible
    assert any("conflict-dispatch" in reason for reason in decision.reasons)


def test_bypass_decision_blocks_on_untrusted_source() -> None:
    decision = reflect.bypass_decision(
        findings=[],
        changed_lock_entries=[{"plugin": "some-plugin@third-party-marketplace"}],
        trusted_marketplaces=["copilot-extensions"],
    )

    assert not decision.eligible
    assert any("trusted-source allowlist" in reason for reason in decision.reasons)


def test_bypass_decision_reports_every_violation_not_just_the_first() -> None:
    decision = reflect.bypass_decision(
        findings=[_finding("projection-ownership")],
        changed_lock_entries=[{"plugin": "some-plugin@third-party-marketplace"}],
        trusted_marketplaces=["copilot-extensions"],
    )

    assert not decision.eligible
    assert len(decision.reasons) == 2


def test_bypass_decision_empty_trusted_set_rejects_everything() -> None:
    decision = reflect.bypass_decision(
        findings=[],
        changed_lock_entries=[{"plugin": "agent-worktrees@copilot-extensions"}],
        trusted_marketplaces=[],
    )

    assert not decision.eligible


# ---- integration against the real sync/scan engine ---------------------------


def test_real_sync_then_scan_is_bypass_eligible_when_trusted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    sync_result = projections.sync_repository(repo, [source])
    scan_result = projections.scan_repository(repo, [source])

    assert sync_result.blocking == 0
    assert reflect.has_actionable_change(
        changed=sync_result.changed, lock_updated=sync_result.lock_updated
    )
    lock_entries = [
        entry
        for entry in json.loads(
            (repo / ".github" / "copilot" / "context-projections.json").read_text(
                encoding="utf-8"
            )
        )["projections"]
        if entry["destination"] in sync_result.changed
    ]
    decision = reflect.bypass_decision(
        findings=scan_result.findings,
        changed_lock_entries=lock_entries,
        trusted_marketplaces=["copilot-extensions"],
    )

    assert decision.eligible, decision.reasons


def test_real_hand_edit_conflict_routes_away_from_bypass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    projections.sync_repository(repo, [source])

    destination = (
        repo / ".github" / "instructions" / "policy" / "fallback.instructions.md"
    )
    # Simulate a local hand-edit of a managed projection -- the exact
    # "flag it, don't silently overwrite" case the reconciler exists for.
    destination.write_text(
        destination.read_text(encoding="utf-8") + "hand-edited\n", encoding="utf-8"
    )

    scan_result = projections.scan_repository(repo, [source])
    decision = reflect.bypass_decision(
        findings=scan_result.findings,
        changed_lock_entries=[],
        trusted_marketplaces=["copilot-extensions"],
    )

    assert not decision.eligible
    assert any("conflict-dispatch" in reason for reason in decision.reasons)
