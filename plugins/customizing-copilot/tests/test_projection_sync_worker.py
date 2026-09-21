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
import projection_reflect_consent  # noqa: E402
import projection_sync_worker as worker  # noqa: E402
import scan_plugin_sources  # noqa: E402


def _write_consent(
    repo: Path,
    *,
    trusted_marketplaces: list[str],
    require_immutable_pin: bool = False,
) -> None:
    consent_path = repo.joinpath(*projection_reflect_consent.CONSENT_PATH_PARTS)
    consent_path.parent.mkdir(parents=True, exist_ok=True)
    consent_path.write_text(
        json.dumps(
            {
                "schema": projection_reflect_consent.CONSENT_SCHEMA,
                "version": projection_reflect_consent.CONSENT_VERSION,
                "enabled": True,
                "reconcilerAgent": "projection-reconciler",
                "dispatchLabel": "projection-reflect-conflict",
                "trustedMarketplaces": trusted_marketplaces,
                "requireImmutablePin": require_immutable_pin,
            }
        ),
        encoding="utf-8",
    )


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


def test_untrusted_source_is_review_only_not_dispatched(tmp_path: Path) -> None:
    # An untrusted-marketplace refusal is not a reconciler-resolvable
    # conflict -- there is no hand-edit or git-level conflict here, just a
    # policy refusal -- so it must open a normal review-only PR, never
    # route to conflict-dispatch.
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "third-party-marketplace", "policy")

    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )

    assert outcome.needs_pr
    assert not outcome.bypass_eligible
    assert not outcome.needs_conflict_dispatch
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
    assert not outcome.needs_conflict_dispatch
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


def test_resolve_pins_runs_after_refresh_and_inside_the_lock(
    tmp_path: Path,
) -> None:
    # resolve_pins must be called AFTER refresh and INSIDE the held lock --
    # resolving any earlier would leave a window where a refresh changes
    # the payload after its commit was captured, letting a stale-but-
    # well-formed SHA pass bypass_decision's pin conjunct even though it no
    # longer describes what this pass renders.
    repo = tmp_path / "repo"
    repo.mkdir()
    root = projections.validate_repository_root(repo)
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    calls: list[str] = []

    def resolve_pins(sources):
        calls.append("resolved")
        # The repository lock must already be held when this runs -- a
        # second acquisition attempt must fail.
        second = projections.repository_sync_lock(root)
        try:
            second.__enter__()
            calls.append("lock_was_free")
        except (BlockingIOError, OSError):
            calls.append("lock_was_held")
        else:
            second.__exit__(None, None, None)
        return {"policy@copilot-extensions": "a" * 40}

    outcome = worker.run_sync_pass(
        repo,
        [source],
        trusted_marketplaces=["copilot-extensions"],
        resolve_pins=resolve_pins,
        refresh=lambda: calls.append("refreshed"),
    )

    assert calls == ["refreshed", "resolved", "lock_was_held"]
    assert outcome.bypass_eligible


def test_resolve_pins_takes_precedence_over_pinned_commits(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    outcome = worker.run_sync_pass(
        repo,
        [source],
        trusted_marketplaces=["copilot-extensions"],
        pinned_commits={},  # would reject if actually used
        resolve_pins=lambda sources: {"policy@copilot-extensions": "a" * 40},
    )

    assert outcome.bypass_eligible


# ---- regression coverage for the review findings on PR #3139 ----------------


def test_policy_relevant_destinations_includes_lock_only_changes() -> None:
    # A lock-only update (some lock-entry field moved without a matching
    # content-diff destination in `changed`) must still be evaluated by the
    # trust/pin conjuncts -- never silently exempted.
    before = {"a": {"pluginVersion": "1.0.0"}}
    after = {"a": {"pluginVersion": "1.0.1"}}

    relevant = worker._policy_relevant_destinations(
        changed=[], entries_before=before, entries_after=after
    )

    assert relevant == {"a"}


def test_policy_relevant_destinations_excludes_untouched_entries() -> None:
    before = {"a": {"pluginVersion": "1.0.0"}, "b": {"pluginVersion": "2.0.0"}}
    after = {"a": {"pluginVersion": "1.0.0"}, "b": {"pluginVersion": "2.0.0"}}

    relevant = worker._policy_relevant_destinations(
        changed=[], entries_before=before, entries_after=after
    )

    assert relevant == set()


def test_policy_relevant_destinations_still_includes_content_changes() -> None:
    relevant = worker._policy_relevant_destinations(
        changed=["a"], entries_before={}, entries_after={}
    )

    assert relevant == {"a"}


def test_sync_findings_are_not_dropped_when_scan_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A sync-side failure (e.g. a failed-to-acquire sync lock) must never
    # look like a clean no-op just because a subsequent scan finds nothing
    # new of its own to report.
    repo = tmp_path / "repo"
    repo.mkdir()

    sync_finding = projections.Finding(
        severity=projections.BLOCKING,
        check="projection-sync-lock",
        path=str(repo),
        message="cannot acquire repository synchronization lock",
    )
    fake_sync_result = SimpleNamespace(
        changed=[], unchanged=[], lock_updated=False, findings=[sync_finding]
    )
    fake_scan_result = SimpleNamespace(findings=[])
    monkeypatch.setattr(
        worker.projections, "sync_repository_locked", lambda *a, **kw: fake_sync_result
    )
    monkeypatch.setattr(
        worker.projections, "scan_repository", lambda *a, **kw: fake_scan_result
    )

    outcome = worker.run_sync_pass(
        repo, [], trusted_marketplaces=["copilot-extensions"]
    )

    assert outcome.needs_pr
    assert sync_finding in outcome.findings
    assert not outcome.bypass.eligible
    assert outcome.needs_conflict_dispatch
    assert any("conflict-dispatch" in reason for reason in outcome.bypass.reasons)


# ---- CLI consent gate (regression coverage for the review findings on
# PR #3139's second round) --------------------------------------------------


def test_cli_refuses_to_run_without_consent(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    exit_code = worker.main([str(repo)])

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "consent" in err.lower()


def test_cli_json_error_without_consent_never_mutates(
    tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    exit_code = worker.main([str(repo), "--json"])

    assert exit_code != 0
    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    assert not lock_path.exists()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "consent" in payload["error"].lower()
    assert captured.err == ""


def test_cli_happy_path_with_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # A live, valid committed consent file plus a discoverable trusted
    # source must let the CLI run, derive trust from consent (not a CLI
    # flag -- there is none), and emit a bypass-eligible JSON outcome.
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_consent(repo, trusted_marketplaces=["copilot-extensions"])
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    monkeypatch.setattr(
        worker.projections, "discover_enabled_sources", lambda *a, **kw: [source]
    )

    exit_code = worker.main([str(repo), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["needsPr"] is True
    assert payload["bypassEligible"] is True
    assert payload["needsConflictDispatch"] is False


def _real_source(plugin: Path, marketplace: str, name: str, *, commit: str = "") -> object:
    return scan_plugin_sources.PluginSource(
        skills_root=plugin / "skills",
        origin=f"{marketplace}/{name}",
        controlled=False,
        source="",
        version="",
        commit=commit,
    )


def test_cli_ignores_pin_requirement_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # requireImmutablePin absent (default False): an unpinned source must
    # still be bypass-eligible -- unaffected by the resolver's existence.
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_consent(repo, trusted_marketplaces=["copilot-extensions"])
    plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    unpinned = _real_source(plugin, "copilot-extensions", "policy", commit="")
    monkeypatch.setattr(
        worker.projections, "discover_enabled_sources", lambda *a, **kw: [unpinned]
    )

    exit_code = worker.main([str(repo), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bypassEligible"] is True


def test_cli_require_immutable_pin_blocks_unpinned_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # requireImmutablePin=true: an unpinned source must NOT be bypass-
    # eligible, even though it's from a trusted marketplace.
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_consent(
        repo,
        trusted_marketplaces=["copilot-extensions"],
        require_immutable_pin=True,
    )
    plugin, _source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    unpinned = _real_source(plugin, "copilot-extensions", "policy", commit="")
    monkeypatch.setattr(
        worker.projections, "discover_enabled_sources", lambda *a, **kw: [unpinned]
    )

    exit_code = worker.main([str(repo), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bypassEligible"] is False
    assert any("immutable commit pin" in reason for reason in payload["bypassReasons"])


def test_cli_require_immutable_pin_permits_pinned_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_consent(
        repo,
        trusted_marketplaces=["copilot-extensions"],
        require_immutable_pin=True,
    )
    plugin, _source = _write_plugin(tmp_path, "copilot-extensions", "policy")
    pinned = _real_source(plugin, "copilot-extensions", "policy", commit="a" * 40)
    monkeypatch.setattr(
        worker.projections, "discover_enabled_sources", lambda *a, **kw: [pinned]
    )

    exit_code = worker.main([str(repo), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bypassEligible"] is True


def test_cli_json_error_for_invalid_root(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "does-not-exist"

    exit_code = worker.main([str(missing), "--json"])

    assert exit_code != 0
    payload = json.loads(capsys.readouterr().out)
    assert "not a directory" in payload["error"]


def test_cli_json_error_for_discovery_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_consent(repo, trusted_marketplaces=["copilot-extensions"])

    def _boom(*_a: object, **_kw: object) -> list[object]:
        raise ValueError("settings are malformed")

    monkeypatch.setattr(worker.projections, "discover_enabled_sources", _boom)

    exit_code = worker.main([str(repo), "--json"])

    assert exit_code != 0
    payload = json.loads(capsys.readouterr().out)
    assert "settings are malformed" in payload["error"]


def test_run_sync_pass_reports_conflict_when_lock_already_held(
    tmp_path: Path,
) -> None:
    # A concurrent holder of the same per-repository lock (simulating
    # another worker mid-pass) must make this call fail closed with a
    # projection-sync-lock conflict finding, never silently proceed and
    # interleave with the other run -- the exact race the held-lock
    # refactor exists to prevent.
    repo = tmp_path / "repo"
    repo.mkdir()
    root = projections.validate_repository_root(repo)
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    held_lock = projections.repository_sync_lock(root)
    held_lock.__enter__()
    try:
        outcome = worker.run_sync_pass(
            repo, [source], trusted_marketplaces=["copilot-extensions"]
        )
    finally:
        held_lock.__exit__(None, None, None)

    assert outcome.needs_pr
    assert outcome.needs_conflict_dispatch
    checks = {getattr(f, "check", None) for f in outcome.findings}
    assert "projection-sync-lock" in checks


def test_lock_is_held_through_scan_not_released_after_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The prior test only proves acquisition fails when the lock is ALREADY
    # held before the call starts -- it would pass even against the old,
    # buggy implementation that released the lock right after sync and ran
    # scan/the post-read unlocked. This test proves the lock is genuinely
    # still held *while scan runs*: from inside a spied scan_repository, a
    # second, independent attempt to acquire the SAME lock (as a second
    # worker would) must itself fail -- demonstrating no other worker could
    # have interleaved between this pass's sync and its scan/lock-read.
    repo = tmp_path / "repo"
    repo.mkdir()
    root = projections.validate_repository_root(repo)
    _plugin, source = _write_plugin(tmp_path, "copilot-extensions", "policy")

    observed: dict[str, bool] = {}
    real_scan = projections.scan_repository

    def spying_scan(r: Path, sources: object) -> object:
        second_attempt = projections.repository_sync_lock(root)
        try:
            second_attempt.__enter__()
        except (BlockingIOError, OSError):
            observed["second_worker_blocked"] = True
        else:
            observed["second_worker_blocked"] = False
            second_attempt.__exit__(None, None, None)
        return real_scan(r, sources)

    monkeypatch.setattr(worker.projections, "scan_repository", spying_scan)

    worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["copilot-extensions"]
    )

    assert observed.get("second_worker_blocked") is True
