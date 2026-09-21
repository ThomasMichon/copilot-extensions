"""Tests for the one-shot deterministic projection-sync-worker tool."""

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
import projection_sync_worker as worker  # noqa: E402


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


def test_no_change_is_not_actionable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    first = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )
    assert first.needs_pr

    # Re-running against an already-synced, unchanged repo must be a no-op:
    # idempotent, no accumulating side effect, no PR warranted.
    second = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )
    assert not second.needs_pr
    assert not second.changed
    assert not second.findings


def test_first_sync_is_bypass_eligible_when_trusted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )

    assert outcome.needs_pr
    assert outcome.bypass_eligible
    assert not outcome.needs_conflict_dispatch
    assert outcome.changed


def test_untrusted_source_routes_to_conflict_dispatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "third-party-marketplace", "policy")

    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )

    assert outcome.needs_pr
    assert not outcome.bypass_eligible
    assert outcome.needs_conflict_dispatch
    assert any(
        "trusted-source allowlist" in reason for reason in outcome.bypass.reasons
    )


def test_hand_edit_conflict_routes_away_from_bypass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    worker.run_sync_pass(repo, [source], trusted_marketplaces=["copilot-extensions"])

    destination = (
        repo / ".github" / "instructions" / "policy" / "fallback.instructions.md"
    )
    destination.write_text(
        destination.read_text(encoding="utf-8") + "hand-edited\n", encoding="utf-8"
    )

    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )

    assert outcome.needs_pr
    assert not outcome.bypass_eligible
    assert outcome.needs_conflict_dispatch
    assert any(
        "conflict-dispatch" in reason for reason in outcome.bypass.reasons
    )


def test_missing_pin_blocks_bypass_when_pins_supplied(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    outcome = worker.run_sync_pass(
        repo,
        [source],
        trusted_marketplaces=["copilot-extensions"],
        pinned_commits={},
    )

    assert outcome.needs_pr
    assert not outcome.bypass_eligible
    assert any(
        "immutable commit pin" in reason for reason in outcome.bypass.reasons
    )


def test_valid_pin_permits_bypass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    outcome = worker.run_sync_pass(
        repo,
        [source],
        trusted_marketplaces=["copilot-extensions"],
        pinned_commits={"policy@copilot-extensions": "a" * 40},
    )

    assert outcome.bypass_eligible


def test_refresh_callback_runs_before_the_pass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    calls: list[str] = []

    worker.run_sync_pass(
        repo,
        [source],
        trusted_marketplaces=["copilot-extensions"],
        refresh=lambda: calls.append("refreshed"),
    )

    assert calls == ["refreshed"]
