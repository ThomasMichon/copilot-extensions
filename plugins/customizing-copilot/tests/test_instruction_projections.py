"""Tests for declarative static instruction projections."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import instruction_projections as projections

_SCANNER_PATH = _SCRIPTS / "scan-customizations.py"
_scanner_spec = importlib.util.spec_from_file_location(
    "scan_customizations_projection_tests", _SCANNER_PATH
)
scanner = importlib.util.module_from_spec(_scanner_spec)
sys.modules[_scanner_spec.name] = scanner
_scanner_spec.loader.exec_module(scanner)
_MANAGER_PATH = _SCRIPTS / "manage-instruction-projections.py"
_manager_spec = importlib.util.spec_from_file_location(
    "manage_instruction_projections_tests", _MANAGER_PATH
)
manager = importlib.util.module_from_spec(_manager_spec)
sys.modules[_manager_spec.name] = manager
_manager_spec.loader.exec_module(manager)

REPO = Path(__file__).resolve().parents[3]


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
    entries: list[dict] | None = None,
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
        json.dumps({"name": name, "version": version}),
        encoding="utf-8",
    )
    declarations = entries or [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/{name}/fallback.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [f"{name}:static-fallback"],
        }
    ]
    (plugin / "instruction-projections.json").write_text(
        json.dumps(
            {
                "schema": projections.DECLARATION_SCHEMA,
                "version": projections.DECLARATION_VERSION,
                "projections": declarations,
            }
        ),
        encoding="utf-8",
    )
    return plugin, _source(plugin, marketplace, name)


def _lock(repo: Path) -> dict:
    return json.loads(
        (repo / ".github" / "copilot" / "context-projections.json").read_text(
            encoding="utf-8"
        )
    )


def _projection(repo: Path, name: str = "policy") -> Path:
    return (
        repo
        / ".github"
        / "instructions"
        / name
        / "fallback.instructions.md"
    )


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _git_commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_render_is_deterministic_and_marker_carries_complete_provenance(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    result = projections.Result(operation="test")
    specs, _unknown = projections._load_specs(repo, [source], result)

    first = projections.render_projection(specs[0])
    second = projections.render_projection(specs[0])
    marker = projections._parse_marker(first.content)

    assert result.blocking == 0
    assert first.content == second.content
    assert first.sha256 == second.sha256
    assert b"\r" not in first.content
    assert marker == first.marker
    assert marker["schema"] == projections.PROJECTION_SCHEMA
    assert marker["version"] == projections.PROJECTION_VERSION
    assert marker["plugin"] == "policy@market"
    assert marker["pluginVersion"] == "1.0.0"
    assert marker["templateSha256"] == specs[0].template_sha256
    assert marker["renderedBytes"] == len(first.content)
    without_marker = b"\n".join(
        line
        for line in first.content.splitlines()
        if not line.startswith(projections.MARKER_PREFIX.encode("ascii"))
    )
    assert b"Keep this static fallback useful." in without_marker


def test_local_sibling_destination_naming() -> None:
    assert (
        projections.local_sibling_destination(
            ".github/instructions/policy/fallback.instructions.md"
        )
        == ".github/instructions/policy/fallback.local.instructions.md"
    )
    with pytest.raises(ValueError, match="already a local cache path"):
        projections.local_sibling_destination(
            ".github/instructions/policy/fallback.local.instructions.md"
        )
    with pytest.raises(ValueError, match="not a plain .instructions.md path"):
        projections.local_sibling_destination(".github/instructions/policy/README.md")


def test_render_local_cache_writes_sibling_without_touching_checked_in_or_lock(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    result = projections.render_local_cache(repo, lambda: [source])

    assert result.blocking == 0
    assert result.changed == [
        ".github/instructions/policy/fallback.local.instructions.md"
    ]
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    assert local_path.exists()
    # The checked-in destination and the lock file are untouched by this
    # render-only path -- only sync_repository_locked/sync_repository ever
    # write either.
    assert not _projection(repo, "policy").exists()
    assert not (repo / ".github" / "copilot" / "context-projections.json").exists()

    # Content matches what the checked-in sync would have rendered (same
    # render_projection call, just a different destination).
    specs, _unknown = projections._load_specs(
        repo, [source], projections.Result(operation="test")
    )
    expected = projections.render_projection(specs[0])
    assert local_path.read_bytes() == expected.content


def test_render_local_cache_never_touches_git_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    # No .git directory at all -- proves this path has no git dependency.
    assert not (repo / ".git").exists()

    first = projections.render_local_cache(repo, lambda: [source])
    second = projections.render_local_cache(repo, lambda: [source])

    assert first.blocking == 0
    assert second.blocking == 0
    assert not (repo / ".git").exists()
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    assert local_path.exists()


def test_render_local_cache_reports_root_error_without_raising(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does-not-exist"
    result = projections.render_local_cache(missing, lambda: [])
    assert result.blocking == 1
    assert result.changed == []


def test_render_local_cache_reconciles_stale_siblings_when_source_disabled(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )

    first = projections.render_local_cache(repo, lambda: [source])
    assert first.blocking == 0
    assert local_path.exists()

    # The source is now disabled/removed -- an ordinary sighted user file
    # that happens to share the naming convention must never be touched,
    # but this call's own previous output must not linger forever.
    second = projections.render_local_cache(repo, lambda: [])
    assert second.blocking == 0
    assert not local_path.exists()
    assert local_path.as_posix().endswith("fallback.local.instructions.md")


def test_render_local_cache_never_removes_a_file_without_its_own_marker(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    unrelated = (
        repo
        / ".github"
        / "instructions"
        / "someone-elses"
        / "notes.local.instructions.md"
    )
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("Just a note a human left here.\n", encoding="utf-8")

    result = projections.render_local_cache(repo, lambda: [])

    assert result.blocking == 0
    assert unrelated.exists()
    assert unrelated.read_text(encoding="utf-8") == "Just a note a human left here.\n"


def test_render_local_cache_rejects_ambiguous_destination_collisions(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    # Two distinct sourceIds within the same plugin declaring the identical
    # checked-in destination (a copy-paste duplicate) both convert to the
    # same local-cache sibling -- destinations are namespaced per plugin, so
    # this is the realistic collision shape, not a cross-plugin one.
    entries = [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": ".github/instructions/policy/fallback.instructions.md",
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
        {
            "id": "fallback-dup",
            "template": "instructions/fallback.instructions.md",
            "destination": ".github/instructions/policy/fallback.instructions.md",
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
    ]
    _plugin, source = _write_plugin(tmp_path, "market", "policy", entries=entries)

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(
        finding.check == "projection-local-cache-ambiguous"
        for finding in result.findings
    )
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    assert not local_path.exists()


def test_render_local_cache_is_idempotent_and_reports_unchanged(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )

    first = projections.render_local_cache(repo, lambda: [source])
    assert first.changed == [
        ".github/instructions/policy/fallback.local.instructions.md"
    ]
    mtime_after_first = local_path.stat().st_mtime_ns

    second = projections.render_local_cache(repo, lambda: [source])
    assert second.changed == []
    assert second.unchanged == [
        ".github/instructions/policy/fallback.local.instructions.md"
    ]
    assert local_path.stat().st_mtime_ns == mtime_after_first


def test_render_local_cache_enforces_per_file_and_aggregate_budget(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, oversized = _write_plugin(
        tmp_path,
        "market",
        "large",
        body="x" * 3700 + "\n",
    )
    file_result = projections.render_local_cache(repo, lambda: [oversized])
    assert any(
        finding.check == "projection-local-cache-budget"
        for finding in file_result.findings
    )
    assert file_result.changed == []

    aggregate_repo = tmp_path / "aggregate-repo"
    aggregate_repo.mkdir()
    sources = [
        _write_plugin(
            tmp_path,
            "aggregate-market",
            f"policy-{index}",
            body="x" * 2700 + "\n",
        )[1]
        for index in range(4)
    ]
    aggregate_result = projections.render_local_cache(aggregate_repo, lambda: sources)
    assert any(
        finding.check == "projection-local-cache-budget"
        and "aggregate" in finding.message
        for finding in aggregate_result.findings
    )
    assert aggregate_result.changed == []


def test_render_local_cache_stops_publishing_when_budget_config_is_malformed(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    # A cache from an earlier, healthy call already exists.
    first = projections.render_local_cache(repo, lambda: [source])
    assert first.blocking == 0
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    assert local_path.exists()

    _write_budget_config(repo, {"aggregateBytes": "not-a-number"})
    result = projections.render_local_cache(repo, lambda: [source])

    assert any(finding.check == "projection-config" for finding in result.findings)
    # Refuses to *publish* (refresh) anything new under a broken config --
    # but a source that goes away entirely under this same broken config
    # must still be reconciled, not left to override the checked-in
    # fallback forever just because the budget couldn't be validated.
    assert not any(
        finding.check.startswith("projection-local-cache-budget")
        for finding in result.findings
    )


def test_render_local_cache_reconciles_stale_siblings_even_when_budget_config_fails(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )

    first = projections.render_local_cache(repo, lambda: [source])
    assert first.blocking == 0
    assert local_path.exists()

    _write_budget_config(repo, {"aggregateBytes": "not-a-number"})
    # The source is gone *and* the budget config is broken in the same
    # call -- the stale cache must still be reconciled away.
    result = projections.render_local_cache(repo, lambda: [])

    assert any(finding.check == "projection-config" for finding in result.findings)
    assert not local_path.exists()


def test_render_local_cache_never_writes_the_wrong_destination_or_loads_it_unbounded(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.parent.mkdir(parents=True)
    oversized = b"x" * (projections.MAX_PROJECTION_BYTES + 1)
    local_path.write_bytes(oversized)

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(finding.check == "projection-local-cache" for finding in result.findings)
    assert result.changed == []
    # Never loaded (and therefore never silently truncated/overwritten)
    # wholesale just to decide whether to replace it.
    assert local_path.read_bytes() == oversized


def test_render_local_cache_reports_handoff_when_a_source_is_renamed(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shared_destination = ".github/instructions/policy/shared.instructions.md"
    old_entries = [
        {
            "id": "old-id",
            "template": "instructions/fallback.instructions.md",
            "destination": shared_destination,
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
    ]
    _plugin, old_source = _write_plugin(
        tmp_path, "market", "policy", entries=old_entries
    )
    first = projections.render_local_cache(repo, lambda: [old_source])
    assert first.blocking == 0
    local_path = repo / ".github" / "instructions" / "policy" / "shared.local.instructions.md"
    assert local_path.exists()

    new_entries = [
        {
            "id": "new-id",
            "template": "instructions/fallback.instructions.md",
            "destination": shared_destination,
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
    ]
    _plugin, new_source = _write_plugin(
        tmp_path, "market", "policy", entries=new_entries
    )

    result = projections.render_local_cache(repo, lambda: [new_source])

    assert any(
        finding.check == "projection-local-cache-handoff" for finding in result.findings
    )
    assert local_path.exists()
    assert result.changed == [
        ".github/instructions/policy/shared.local.instructions.md"
    ]


def test_render_local_cache_write_failure_on_one_source_never_blocks_another(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{source_id}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id in ("first", "second")
    ]
    _plugin, source = _write_plugin(tmp_path, "market", "policy", entries=entries)

    real_atomic_write = projections._atomic_write

    def fail_for_first(path: Path, content: bytes) -> None:
        if path.name == "first.local.instructions.md":
            raise OSError("simulated write failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_for_first)

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(
        finding.check == "projection-local-cache"
        and "first.local.instructions.md" in finding.path
        for finding in result.findings
    )
    first_path = (
        repo / ".github" / "instructions" / "policy" / "first.local.instructions.md"
    )
    second_path = (
        repo / ".github" / "instructions" / "policy" / "second.local.instructions.md"
    )
    assert not first_path.exists()
    assert second_path.exists()
    assert result.changed == [
        ".github/instructions/policy/second.local.instructions.md"
    ]
    # No checked-in destination or lock exists at all -- this render-only
    # path never touches either, failure or not.
    assert not (repo / ".github" / "copilot" / "context-projections.json").exists()


def test_render_local_cache_removes_stale_content_when_a_refresh_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )

    first = projections.render_local_cache(repo, lambda: [source])
    assert first.blocking == 0
    assert local_path.exists()

    # Change the template so the next render's content differs (forcing an
    # actual write attempt rather than the unchanged/no-op path), then make
    # that write fail.
    template = _plugin / "instructions" / "fallback.instructions.md"
    template.write_text(
        '---\napplyTo: "**"\n---\n\n# Fallback\n\nUpdated body.\n',
        encoding="utf-8",
        newline="\n",
    )

    def fail_write(path: Path, content: bytes) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(projections, "_atomic_write", fail_write)

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(finding.check == "projection-local-cache" for finding in result.findings)
    # The stale (pre-update) sibling must not be left in place as if it
    # were still current -- a failed refresh is reconciled away, not
    # silently preferred.
    assert not local_path.exists()


def test_render_local_cache_rejects_a_checked_in_destination_using_the_reserved_suffix(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                ".github/instructions/policy/fallback.local.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
    ]
    _plugin, source = _write_plugin(tmp_path, "market", "policy", entries=entries)

    result = projections.Result(operation="test")
    specs, _unknown = projections._load_specs(repo, [source], result)

    assert specs == []
    assert any(
        finding.check == "projection-declaration"
        and "reserved" in finding.message
        for finding in result.findings
    )


def test_render_local_cache_rejects_duplicate_source_key_different_destinations(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": ".github/instructions/policy/fallback.instructions.md",
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": ".github/instructions/policy/other.instructions.md",
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
    ]
    _plugin, source = _write_plugin(tmp_path, "market", "policy", entries=entries)

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(
        finding.check == "projection-local-cache-ambiguous"
        and "declared more than once" in finding.message
        for finding in result.findings
    )
    assert result.changed == []


def test_render_local_cache_refuses_to_overwrite_a_foreign_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.parent.mkdir(parents=True)
    local_path.write_text("A human wrote this, not the renderer.\n", encoding="utf-8")

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(
        finding.check == "projection-local-cache-foreign" for finding in result.findings
    )
    assert result.changed == []
    assert (
        local_path.read_text(encoding="utf-8")
        == "A human wrote this, not the renderer.\n"
    )


def test_render_local_cache_stale_cleanup_ignores_a_marker_at_the_wrong_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    first = projections.render_local_cache(repo, lambda: [source])
    assert first.blocking == 0
    genuine_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    # Copy the genuine, marker-bearing render to an unrelated local-cache
    # path -- same bytes, same marker, wrong location. It must never be
    # deleted as "stale" even though the source it actually belongs to is
    # gone this call, because it does not sit at *its own* matching sibling.
    misplaced_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "misplaced.local.instructions.md"
    )
    misplaced_path.write_bytes(genuine_path.read_bytes())

    projections.render_local_cache(repo, lambda: [])

    assert not genuine_path.exists()
    assert misplaced_path.exists()


def test_render_local_cache_reports_conflict_when_lock_already_held(
    tmp_path: Path,
) -> None:
    # A concurrent holder of this render's own dedicated lock (simulating
    # a worktree create/resume racing its own sessionStart, or two
    # overlapping sessions) must make this call fail closed with a
    # projection-local-cache-lock finding and touch nothing at all, never
    # silently interleave its render/write/cleanup pass with the other
    # call's -- the exact race that would otherwise let an older render
    # clobber a newer one, or delete a sibling the other call just
    # refreshed.
    repo = tmp_path / "repo"
    repo.mkdir()
    root = projections.validate_repository_root(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    held_lock = projections._local_cache_lock(root)
    held_lock.__enter__()
    try:
        result = projections.render_local_cache(repo, lambda: [source])
    finally:
        held_lock.__exit__(None, None, None)

    assert any(
        finding.check == "projection-local-cache-lock" for finding in result.findings
    )
    assert result.changed == []
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    assert not local_path.exists()


def test_render_local_cache_does_not_call_discover_sources_when_lock_is_contended(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = projections.validate_repository_root(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    calls: list[int] = []

    def discover() -> list[object]:
        calls.append(1)
        return [source]

    held_lock = projections._local_cache_lock(root)
    held_lock.__enter__()
    try:
        projections.render_local_cache(repo, discover)
    finally:
        held_lock.__exit__(None, None, None)

    # A precomputed source list can't be un-computed once it's already an
    # argument, so the only way to guarantee no call ever acts on stale
    # information is to never even *resolve* the source set until the lock
    # is held -- proven here by showing the resolver is never invoked at
    # all when the lock is contended.
    assert calls == []


def test_render_local_cache_resolves_sources_exactly_once_when_uncontended(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    calls: list[int] = []

    def discover() -> list[object]:
        calls.append(1)
        return [source]

    result = projections.render_local_cache(repo, discover)

    assert calls == [1]
    assert result.blocking == 0


def test_render_local_cache_refuses_to_write_a_git_tracked_destination(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    # Simulate the exact hazard: the *.local.instructions.md ignore rule
    # was never adopted, so this file got committed like any other.
    local_path.parent.mkdir(parents=True)
    local_path.write_text("Accidentally committed before .gitignore existed.\n", encoding="utf-8")
    _git_commit_all(repo, "accidentally commit a local cache file")

    result = projections.render_local_cache(repo, lambda: [source])

    assert any(
        finding.check == "projection-local-cache-tracked" for finding in result.findings
    )
    assert result.changed == []
    assert (
        local_path.read_text(encoding="utf-8")
        == "Accidentally committed before .gitignore existed.\n"
    )


def test_render_local_cache_never_deletes_a_git_tracked_stale_sibling(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.parent.mkdir(parents=True)
    # A real render's own bytes (owned marker), but this copy got
    # committed -- the reconciliation pass must never delete a git-tracked
    # file, even one it can otherwise prove it owns.
    rendered = projections.render_projection(
        projections._load_specs(repo, [source], projections.Result(operation="t"))[0][
            0
        ]
    )
    local_path.write_bytes(rendered.content)
    _git_commit_all(repo, "accidentally commit a genuine-looking local cache")

    result = projections.render_local_cache(repo, lambda: [])

    assert any(
        finding.check == "projection-local-cache-tracked" for finding in result.findings
    )
    assert local_path.exists()


def test_render_local_cache_treats_inconclusive_git_check_as_tracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    def broken_run(*args, **kwargs):
        if "rev-parse" in args[0]:
            return subprocess.CompletedProcess(args[0], 0, stdout=b"true\n", stderr=b"")
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(projections.subprocess, "run", broken_run)

    result = projections.render_local_cache(repo, lambda: [source])

    # A real git working tree whose tracked-files query itself failed must
    # be treated exactly like "tracked" -- never like "confirmed
    # untracked" -- so nothing gets written on the strength of a broken
    # check.
    assert any(
        finding.check == "projection-local-cache-tracked" for finding in result.findings
    )
    assert result.changed == []
    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    assert not local_path.exists()


def test_render_local_cache_reports_discovery_failures_without_raising(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def broken_discover() -> list[object]:
        raise ValueError("repository settings are malformed")

    result = projections.render_local_cache(repo, broken_discover)

    assert any(
        finding.check == "projection-local-cache-discovery"
        for finding in result.findings
    )
    assert result.changed == []


def test_render_local_cache_files_excluded_from_checked_in_orphan_scan(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    sync_result = projections.sync_repository(repo, [source])
    assert sync_result.blocking == 0
    local_result = projections.render_local_cache(repo, lambda: [source])
    assert local_result.blocking == 0

    lock = _lock(repo)
    scan_result = projections.scan_repository(repo, [source])

    assert not any(
        finding.check == "projection-orphan-file" for finding in scan_result.findings
    )
    assert lock["projections"]


def test_orphan_scan_still_excludes_local_cache_file_in_an_untracked_git_repo(
    tmp_path: Path,
) -> None:
    """The exclusion must still hold once a repo *does* have git, so long as
    the ``.local.instructions.md`` file itself is genuinely untracked (the
    adopted-the-convention case this mechanism is built for)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    _git_commit_all(repo, "commit the checked-in projection")

    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.write_bytes(_projection(repo).read_bytes())
    # Deliberately left untracked (no git add/commit) -- this is the
    # adopted-the-convention case the suffix exclusion exists for.

    scan_result = projections.scan_repository(repo)

    assert not any(
        finding.check == "projection-orphan-file" for finding in scan_result.findings
    )


def test_orphan_scan_flags_a_git_tracked_local_cache_file_instead_of_hiding_it(
    tmp_path: Path,
) -> None:
    """A stray ``*.local.instructions.md`` file committed before (or
    without) the ``.gitignore`` convention being adopted must never be
    silently excluded from the orphan scan merely because of its suffix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    _git_commit_all(repo, "commit the checked-in projection")

    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.write_bytes(_projection(repo).read_bytes())
    _git_commit_all(repo, "accidentally commit a local cache file")

    scan_result = projections.scan_repository(repo)

    assert any(
        finding.check == "projection-orphan-file" for finding in scan_result.findings
    )


def test_orphan_scan_treats_inconclusive_git_check_as_not_excludable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    _git_commit_all(repo, "commit the checked-in projection")

    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.write_bytes(_projection(repo).read_bytes())
    # Left untracked -- an inconclusive check must never be treated as
    # "confirmed untracked" merely because it also happens to be true here.

    def broken_run(*args, **kwargs):
        if "rev-parse" in args[0]:
            return subprocess.CompletedProcess(args[0], 0, stdout=b"true\n", stderr=b"")
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(projections.subprocess, "run", broken_run)

    scan_result = projections.scan_repository(repo)

    assert any(
        finding.check == "projection-orphan-file" for finding in scan_result.findings
    )


def test_orphan_scan_distinguishes_a_failed_probe_from_a_confirmed_non_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real git working tree whose ``rev-parse`` probe itself fails to
    complete (timeout, transient subprocess error) must never be treated
    like a confirmed plain, non-git directory -- that would silently hide
    a tracked local-cache file from the orphan scan. Only a *completed*
    probe reporting "not a work tree", or a genuinely missing ``git``
    binary (``FileNotFoundError``), may be treated that way."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    _git_commit_all(repo, "commit the checked-in projection")

    local_path = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "fallback.local.instructions.md"
    )
    local_path.write_bytes(_projection(repo).read_bytes())
    _git_commit_all(repo, "accidentally commit a local cache file")

    def broken_rev_parse(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(projections.subprocess, "run", broken_rev_parse)

    scan_result = projections.scan_repository(repo)

    assert any(
        finding.check == "projection-orphan-file" for finding in scan_result.findings
    )


def test_resolve_git_tracked_paths_treats_missing_git_binary_as_not_applicable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely missing ``git`` binary is the one case kept as "not
    applicable" (``inconclusive=False``, matching the plain-directory
    case) rather than inconclusive -- it can never itself be a transient
    failure *of* an existing repository."""

    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(projections.subprocess, "run", missing_git)

    tracked, inconclusive = projections._resolve_git_tracked_paths(
        tmp_path, ["some/file.local.instructions.md"]
    )

    assert tracked == set()
    assert inconclusive is False


def test_sync_safely_creates_then_updates_projection_and_lock(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")

    created = projections.sync_repository(repo, [source])
    original = _projection(repo).read_bytes()
    initial_lock = _lock(repo)

    assert created.blocking == 0
    assert created.changed == [
        ".github/instructions/policy/fallback.instructions.md"
    ]
    assert created.lock_updated is True
    assert initial_lock["schema"] == projections.LOCK_SCHEMA
    assert initial_lock["version"] == projections.LOCK_VERSION

    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "1.0.1"}),
        encoding="utf-8",
    )
    template = plugin / "instructions" / "fallback.instructions.md"
    template.write_text(
        '---\napplyTo: "**"\n---\n\n# Fallback\n\nUpdated policy.\n',
        encoding="utf-8",
        newline="\n",
    )
    updated = projections.sync_repository(repo, [source])

    assert updated.blocking == 0
    assert _projection(repo).read_bytes() != original
    assert _lock(repo)["projections"][0]["pluginVersion"] == "1.0.1"

    unchanged = projections.sync_repository(repo, [source])
    assert unchanged.blocking == 0
    assert unchanged.changed == []
    assert unchanged.unchanged == [
        ".github/instructions/policy/fallback.instructions.md"
    ]
    assert unchanged.lock_updated is False


@pytest.mark.parametrize(
    "mutation",
    [
        "unmarked",
        "local-body",
        "malformed-marker",
        "missing-lock",
        "different-owner",
    ],
)
def test_sync_refuses_unsafe_overwrite_cases(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    destination = _projection(repo)
    if mutation == "unmarked":
        destination.parent.mkdir(parents=True)
        destination.write_text("repository-owned\n", encoding="utf-8")
    else:
        assert projections.sync_repository(repo, [source]).blocking == 0
        if mutation == "local-body":
            destination.write_text(
                destination.read_text(encoding="utf-8") + "\nlocal edit\n",
                encoding="utf-8",
            )
        elif mutation == "malformed-marker":
            text = destination.read_text(encoding="utf-8")
            destination.write_text(
                text.replace(
                    projections.MARKER_PREFIX,
                    projections.MARKER_PREFIX + "{bad ",
                    1,
                ),
                encoding="utf-8",
            )
        elif mutation == "missing-lock":
            (
                repo / ".github" / "copilot" / "context-projections.json"
            ).unlink()
        elif mutation == "different-owner":
            lock = _lock(repo)
            lock["projections"][0]["plugin"] = "other@market"
            (
                repo / ".github" / "copilot" / "context-projections.json"
            ).write_text(json.dumps(lock), encoding="utf-8")

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert result.changed == []


def test_sync_refuses_destination_symlink_or_reparse_point(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    instructions = repo / ".github" / "instructions"
    instructions.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        instructions.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert not (outside / "policy" / "fallback.instructions.md").exists()


def test_sync_and_scan_refuse_dangling_lock_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    lock_path.parent.mkdir(parents=True)
    try:
        lock_path.symlink_to(tmp_path / "missing-lock-target")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    synced = projections.sync_repository(repo, [source])
    scanned = projections.scan_repository(repo)

    assert synced.blocking > 0
    assert synced.changed == []
    assert scanned.blocking > 0
    assert not _projection(repo).exists()


def test_sync_refuses_dangling_declaration_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    declaration = plugin / "instruction-projections.json"
    declaration.unlink()
    try:
        declaration.symlink_to(tmp_path / "missing-declaration")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-declaration"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


def test_sync_skips_partial_enabled_plugin_payload_when_not_participating(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    partial = tmp_path / "installed" / "market" / "policy"
    partial.mkdir(parents=True)
    source = _source(partial, "market", "policy")

    result = projections.sync_repository(repo, [source])

    # This enabled plugin declares no instruction-projections.json and has
    # never been locked, so an unreadable manifest is not a projection-stack
    # problem -- it simply is not a participant.
    assert result.blocking == 0
    assert result.changed == []


def test_sync_blocks_partial_enabled_plugin_payload_with_declaration(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    partial = tmp_path / "installed" / "market" / "policy"
    partial.mkdir(parents=True)
    (partial / "instruction-projections.json").write_text(
        json.dumps(
            {
                "schema": projections.DECLARATION_SCHEMA,
                "version": projections.DECLARATION_VERSION,
                "projections": [
                    {
                        "id": "fallback",
                        "template": "instructions/fallback.instructions.md",
                        "destination": (
                            ".github/instructions/policy/fallback.instructions.md"
                        ),
                        "customizationKind": "instructions",
                        "applyTo": "**",
                        "legacyMarkers": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source = _source(partial, "market", "policy")

    result = projections.sync_repository(repo, [source])

    # A visible declaration means this plugin actively claims participation,
    # so its unreadable manifest is still blocking even without a prior lock.
    assert any(
        finding.check == "projection-source-unavailable"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


@pytest.mark.parametrize(
    "manifest_relative",
    [
        Path("plugin.json"),
        Path(".claude-plugin/plugin.json"),
    ],
)
def test_nonparticipating_available_plugin_is_not_projection_validated(
    tmp_path: Path,
    manifest_relative: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "installed" / "market" / "policy"
    manifest = plugin / manifest_relative
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"name": "policy", "version": "1.0.0-beta.1"}),
        encoding="utf-8",
    )
    source = _source(plugin, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert result.declared == 0
    assert result.changed == []


def test_projection_participant_supports_legacy_manifest_layout(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    root_manifest = plugin / "plugin.json"
    legacy_manifest = plugin / ".claude-plugin" / "plugin.json"
    legacy_manifest.parent.mkdir()
    root_manifest.replace(legacy_manifest)

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert result.declared == 1
    assert _projection(repo).is_file()


def test_sync_refuses_symlinked_repository_root(tmp_path: Path) -> None:
    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    linked_repo = tmp_path / "linked-repo"
    try:
        linked_repo.symlink_to(real_repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    result = projections.sync_repository(linked_repo, [source])

    assert any(
        finding.check == "projection-root"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )


def test_cli_preserves_symlinked_repository_root_for_refusal(
    tmp_path: Path,
    capsys,
) -> None:
    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    linked_repo = tmp_path / "linked-repo"
    try:
        linked_repo.symlink_to(real_repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    exit_code = manager.main(["scan", str(linked_repo), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["findings"][0]["check"] == "projection-root"


def test_offline_scan_validates_marker_lock_and_orphans(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    clean = projections.scan_repository(repo)
    assert clean.blocking == 0

    orphan = (
        repo
        / ".github"
        / "instructions"
        / "orphan"
        / "orphan.instructions.md"
    )
    orphan.parent.mkdir()
    orphan.write_bytes(_projection(repo).read_bytes())
    orphaned = projections.scan_repository(repo)
    assert any(
        finding.check == "projection-orphan-file"
        for finding in orphaned.findings
    )

    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    lock_path.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    malformed = projections.scan_repository(repo)
    assert any(
        finding.check == "projection-lock"
        and finding.severity == projections.BLOCKING
        for finding in malformed.findings
    )


def test_current_source_update_is_advisory_until_sync(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    original = _projection(repo).read_bytes()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )

    result = projections.scan_repository(repo, [source])

    assert result.blocking == 0
    assert any(
        finding.check == "projection-source-update"
        and finding.severity == projections.WARNING
        for finding in result.findings
    )
    assert _projection(repo).read_bytes() == original


def test_duplicate_sources_destinations_and_overlap_are_reported(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    duplicate_entries = [
        {
            "id": "same",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                ".github/instructions/policy/first.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
        {
            "id": "same",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                ".github/instructions/policy/first.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
    ]
    _plugin, duplicate_source = _write_plugin(
        tmp_path, "market", "policy", entries=duplicate_entries
    )
    duplicate_result = projections.scan_repository(repo, [duplicate_source])
    assert any(
        finding.check == "projection-duplicate-source"
        for finding in duplicate_result.findings
    )
    assert any(
        finding.check == "projection-duplicate-destination"
        for finding in duplicate_result.findings
    )

    _one, first = _write_plugin(tmp_path, "market", "first")
    _two, second = _write_plugin(tmp_path, "market", "second")
    overlap = projections.scan_repository(repo, [first, second])
    assert any(
        finding.check == "projection-overlap"
        and finding.severity == projections.WARNING
        for finding in overlap.findings
    )


@pytest.mark.parametrize(
    "filename",
    [
        "AUX.instructions.md",
        "bad?.instructions.md",
        "trailing.instructions.md.",
    ],
)
def test_declarations_reject_nonportable_destinations(
    tmp_path: Path,
    filename: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": f".github/instructions/policy/{filename}",
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
    ]
    _plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-declaration"
        and "portable filesystem components" in finding.message
        for finding in result.findings
    )


def test_declarations_reject_casefolded_destination_collisions(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{filename}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id, filename in (
            ("upper", "Fallback"),
            ("lower", "fallback"),
        )
    ]
    _plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-duplicate-destination"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


@pytest.mark.parametrize("failure_call", [2, 3])
def test_sync_rolls_back_projection_transaction_failures(
    tmp_path: Path,
    monkeypatch,
    failure_call: int,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{source_id}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id in ("first", "second")
    ]
    plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )
    assert projections.sync_repository(repo, [source]).blocking == 0
    paths = [
        repo
        / ".github"
        / "instructions"
        / "policy"
        / f"{source_id}.instructions.md"
        for source_id in ("first", "second")
    ]
    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    before = {path: path.read_bytes() for path in [*paths, lock_path]}
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )
    original_atomic_write = projections._atomic_write
    calls = 0

    def fail_once(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected transaction failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_once)

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert result.changed == []
    assert {path: path.read_bytes() for path in before} == before


def test_sync_preserves_edit_made_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    destination = _projection(repo)
    lock_before = (
        repo / ".github" / "copilot" / "context-projections.json"
    ).read_bytes()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )
    original_transaction = projections._transactional_write
    local_edit = b"concurrent local edit\n"

    def edit_before_commit(changes) -> None:
        destination.write_bytes(local_edit)
        original_transaction(changes)

    monkeypatch.setattr(
        projections, "_transactional_write", edit_before_commit
    )

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert result.changed == []
    assert destination.read_bytes() == local_edit
    assert (
        repo / ".github" / "copilot" / "context-projections.json"
    ).read_bytes() == lock_before


def test_rollback_does_not_overwrite_a_concurrent_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{source_id}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id in ("first", "second")
    ]
    plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )
    assert projections.sync_repository(repo, [source]).blocking == 0
    first = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "first.instructions.md"
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )
    original_atomic_write = projections._atomic_write
    local_edit = b"concurrent local edit\n"
    calls = 0

    def fail_after_edit(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            first.write_bytes(local_edit)
            raise OSError("injected transaction failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_after_edit)

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert first.read_bytes() == local_edit
    assert any("rollback also failed" in finding.message for finding in result.findings)


def test_file_and_aggregate_budgets_are_blocking(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, oversized = _write_plugin(
        tmp_path,
        "market",
        "large",
        body="x" * 3700 + "\n",
    )
    file_result = projections.sync_repository(repo, [oversized])
    assert any(
        finding.check == "projection-budget"
        and finding.severity == projections.BLOCKING
        for finding in file_result.findings
    )

    aggregate_repo = tmp_path / "aggregate-repo"
    aggregate_repo.mkdir()
    sources = [
        _write_plugin(
            tmp_path,
            "aggregate-market",
            f"policy-{index}",
            body="x" * 2700 + "\n",
        )[1]
        for index in range(4)
    ]
    aggregate_result = projections.sync_repository(aggregate_repo, sources)
    assert any(
        finding.check == "projection-budget"
        and "aggregate" in finding.message
        for finding in aggregate_result.findings
    )


def _write_budget_config(repo: Path, payload: object) -> None:
    config_path = repo.joinpath(*projections.CONFIG_RELATIVE.parts)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")


def test_repository_config_raises_the_aggregate_budget(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_budget_config(
        repo,
        {
            "schema": projections.CONFIG_SCHEMA,
            "version": projections.CONFIG_VERSION,
            "maxAggregateBytes": 20 * 1024,
        },
    )
    sources = [
        _write_plugin(
            tmp_path,
            "aggregate-market",
            f"policy-{index}",
            body="x" * 2700 + "\n",
        )[1]
        for index in range(4)
    ]

    result = projections.sync_repository(repo, sources)

    assert not any(
        finding.check == "projection-budget" for finding in result.findings
    )
    assert result.blocking == 0


def test_repository_config_can_lower_the_aggregate_budget(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_budget_config(
        repo,
        {
            "schema": projections.CONFIG_SCHEMA,
            "version": projections.CONFIG_VERSION,
            "maxAggregateBytes": projections.MIN_AGGREGATE_BYTES,
        },
    )
    sources = [
        _write_plugin(
            tmp_path,
            "lower-market",
            f"policy-{index}",
            body="x" * 2700 + "\n",
        )[1]
        for index in range(4)
    ]

    result = projections.sync_repository(repo, sources)

    assert any(
        finding.check == "projection-budget"
        and "aggregate" in finding.message
        and str(projections.MIN_AGGREGATE_BYTES) in finding.message
        for finding in result.findings
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"maxAggregateBytes": 20480},
        {
            "schema": "wrong-schema",
            "version": 1,
            "maxAggregateBytes": 20480,
        },
        {
            "schema": "copilot-extensions.instruction-projections-config",
            "version": 2,
            "maxAggregateBytes": 20480,
        },
        {
            "schema": "copilot-extensions.instruction-projections-config",
            "version": 1,
            "maxAggregateBytes": "20480",
        },
        {
            "schema": "copilot-extensions.instruction-projections-config",
            "version": 1,
            "maxAggregateBytes": 1,
        },
        {
            "schema": "copilot-extensions.instruction-projections-config",
            "version": 1,
            "maxAggregateBytes": 999999,
        },
        {
            "schema": "copilot-extensions.instruction-projections-config",
            "version": 1,
            "maxAggregateBytes": True,
        },
    ],
)
def test_malformed_or_out_of_range_config_is_blocking_and_uses_default(
    tmp_path: Path, payload: dict
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_budget_config(repo, payload)
    sources = [
        _write_plugin(
            tmp_path,
            "aggregate-market",
            f"policy-{index}",
            body="x" * 2700 + "\n",
        )[1]
        for index in range(4)
    ]

    result = projections.sync_repository(repo, sources)

    assert any(finding.check == "projection-config" for finding in result.findings)
    assert any(
        finding.check == "projection-budget"
        and str(projections.MAX_AGGREGATE_BYTES) in finding.message
        for finding in result.findings
    )


def test_absent_config_uses_default_budget_unaffected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = projections._load_aggregate_budget(repo, projections.Result(operation="scan"))
    assert result == projections.MAX_AGGREGATE_BYTES


@pytest.mark.parametrize(
    "dynamic",
    [
        "Session 123e4567-e89b-12d3-a456-426614174000\n",
        "Read ${HOME} before acting.\n",
        "Read ~/.copilot/session-state/<id>/files/policy.md.\n",
        "Read C:\\Users\\example\\.copilot\\installed-plugins\\policy.\n",
    ],
)
def test_declarations_reject_dynamic_or_session_specific_content(
    tmp_path: Path,
    dynamic: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(
        tmp_path, "market", "policy", body=dynamic
    )

    result = projections.scan_repository(repo, [source])

    assert any(
        finding.check == "projection-declaration"
        and "forbidden dynamic content" in finding.message
        for finding in result.findings
    )


def test_legacy_managed_region_is_actionable_but_not_deleted(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy = (
        "<!-- policy:static-fallback:start -->\n"
        "Old fallback.\n"
        "<!-- policy:static-fallback:end -->\n"
    )
    (repo / "AGENTS.md").write_text(legacy, encoding="utf-8")
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert any(
        finding.check == "projection-legacy-region"
        and finding.severity == projections.WARNING
        for finding in result.findings
    )
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == legacy


def test_legacy_scan_prunes_dependency_and_build_trees(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy = (
        "<!-- policy:static-fallback:start -->\n"
        "Vendored fallback.\n"
        "<!-- policy:static-fallback:end -->\n"
    )
    for directory in ("node_modules", "vendor", "build", ".test-venvs"):
        agents = repo / directory / "package" / "AGENTS.md"
        agents.parent.mkdir(parents=True)
        agents.write_text(legacy, encoding="utf-8")
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert not any(
        finding.check == "projection-legacy-region"
        for finding in result.findings
    )


def test_existing_scanner_includes_projection_findings_and_inventory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    report = scanner.run(repo, [source])

    assert any(
        finding.check == "projection-missing"
        for finding in report.findings
    )
    assert report.instruction_projections is not None
    assert report.instruction_projections["schema"] == projections.RESULT_SCHEMA
    assert report.instruction_projections["declared"] == 1


def test_orphaned_lock_is_reported_when_enabled_sources_no_longer_declare_it(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0

    result = projections.scan_repository(repo, [])

    assert any(
        finding.check == "projection-orphan-lock"
        and finding.severity == projections.WARNING
        for finding in result.findings
    )


def test_repository_projection_discovery_excludes_user_only_plugins(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    installed = tmp_path / "installed"
    _write_plugin(installed, "market", "repo-policy")
    _write_plugin(installed, "market", "user-policy")
    repo_settings = repo / ".github" / "copilot" / "settings.json"
    repo_settings.parent.mkdir(parents=True)
    repo_settings.write_text(
        json.dumps(
            {"enabledPlugins": {"repo-policy@market": True}}
        ),
        encoding="utf-8",
    )
    (repo_settings.parent / "settings.local.json").write_text(
        json.dumps(
            {"enabledPlugins": {"user-policy@market": True}}
        ),
        encoding="utf-8",
    )
    claude_settings = repo / ".claude" / "settings.local.json"
    claude_settings.parent.mkdir(parents=True)
    claude_settings.write_text(
        json.dumps(
            {"enabledPlugins": {"user-policy@market": True}}
        ),
        encoding="utf-8",
    )
    user_settings = home / ".copilot" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(
        json.dumps(
            {"enabledPlugins": {"user-policy@market": True}}
        ),
        encoding="utf-8",
    )

    sources = projections.discover_enabled_sources(
        repo,
        installed_root=installed,
        home=home,
        require_trust=False,
    )

    assert [source.origin for source in sources] == ["market/repo-policy"]


def test_cli_sync_reads_committed_settings_without_folder_trust(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _write_plugin(installed, "market", "policy")
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"enabledPlugins": {"policy@market": True}}),
        encoding="utf-8",
    )

    exit_code = manager.main(
        [
            "sync",
            str(repo),
            "--installed-root",
            str(installed),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["declared"] == 1
    assert _projection(repo).is_file()


def test_integrated_scanner_reads_projection_settings_without_folder_trust(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    marketplace_root = repo / "payloads" / "market"
    _write_plugin(repo / "payloads", "market", "policy")
    marketplace_manifest = (
        marketplace_root / ".claude-plugin" / "marketplace.json"
    )
    marketplace_manifest.parent.mkdir()
    marketplace_manifest.write_text(
        json.dumps(
            {
                "name": "market",
                "plugins": [{"name": "policy", "source": "policy"}],
            }
        ),
        encoding="utf-8",
    )
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "enabledPlugins": {"policy@market": True},
                "extraKnownMarketplaces": {
                    "market": {
                        "source": {
                            "source": "directory",
                            "path": "payloads/market",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = scanner.main(
        [str(repo), "--from-settings", "--strict", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["instruction_projections"]["declared"] == 1
    assert any(
        finding["check"] == "projection-missing"
        for finding in payload["findings"]
    )


def test_cli_sync_blocks_malformed_committed_settings(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{", encoding="utf-8")

    exit_code = manager.main(["sync", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["findings"][0]["check"] == "projection-settings"


def test_manager_forwards_agent_worktrees_path_to_discover_enabled_sources(
    tmp_path: Path, monkeypatch,
) -> None:
    """`manage-instruction-projections.py`'s `--agent-worktrees-path` must
    reach `discover_enabled_sources()` -- a second, independent CLI boundary
    from `scan-customizations.py`'s own forwarding."""
    import shutil
    import subprocess

    other_repo = tmp_path / "other-repo-checkout"
    marketplace_root = other_repo / ".ai"
    _write_plugin(marketplace_root, "other-marketplace", "cap")
    (marketplace_root / ".claude-plugin" / "marketplace.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (marketplace_root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({
            "name": "other-marketplace",
            "plugins": [{"name": "cap", "source": "cap"}],
        }),
        encoding="utf-8",
    )

    commands = []

    def fake_which(name):
        if name == "agent-worktrees":
            return str(tmp_path / "wrong-cell" / "agent-worktrees")
        return None

    def fake_run(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"{other_repo}\n", stderr="",
        )

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)

    repo = tmp_path / "repo"
    repo.mkdir()
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"cap@other-marketplace": True},
        "extraKnownMarketplaces": {
            "other-marketplace": {
                "source": {
                    "source": "agent-worktrees-repo",
                    "repo": "other-repo-alias",
                },
            },
        },
    }), encoding="utf-8")

    resolved = str(tmp_path / "right-cell" / "agent-worktrees")
    manager.main([
        "sync", str(repo), "--json", "--agent-worktrees-path", resolved,
    ])

    assert commands, "agent-worktrees-repo resolution was never attempted"
    assert all(c[0] == resolved for c in commands), commands


def test_sync_skips_unavailable_enabled_plugin_payload_when_not_participating(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    missing = tmp_path / "installed" / "market" / "policy"
    source = _source(missing, "market", "policy")

    result = projections.sync_repository(repo, [source])

    # No declaration was ever visible for this plugin and it has never been
    # locked, so its payload being entirely unresolvable is not itself a
    # projection-stack problem.
    assert result.blocking == 0
    assert result.changed == []


def test_scan_blocks_when_a_locked_plugin_payload_becomes_unavailable(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    synced = projections.sync_repository(repo, [source])
    assert synced.blocking == 0
    assert synced.changed == [".github/instructions/policy/fallback.instructions.md"]

    # The plugin's payload has since vanished (e.g. uninstalled, or its
    # source can no longer be resolved from this settings layer) while its
    # projection is still locked in this repository -- that is a regression
    # that must still block, unlike a never-locked, non-participating plugin.
    vanished_source = _source(tmp_path / "gone" / "market" / "policy", "market", "policy")

    result = projections.scan_repository(repo, [vanished_source])

    assert any(
        finding.check == "projection-source-unavailable"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )


def test_self_host_source_supports_legacy_marketplace_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    editable, _editable_source = _write_plugin(
        repo, "payloads", "policy", version="2.0.0", body="Editable.\n"
    )
    legacy_manifest = repo / ".claude-plugin" / "marketplace.json"
    legacy_manifest.parent.mkdir()
    legacy_manifest.write_text(
        json.dumps(
            {
                "name": "market",
                "plugins": [{"name": "policy", "source": "payloads/policy"}],
            }
        ),
        encoding="utf-8",
    )
    installed, source = _write_plugin(
        tmp_path, "market", "policy", version="1.0.0", body="Installed.\n"
    )
    assert editable != installed
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(repo, [source], result)

    assert result.blocking == 0
    assert specs[0].plugin_version == "2.0.0"
    assert b"Editable." in specs[0].template_content


def test_consumer_same_named_plugin_directory_does_not_shadow_resolved_payload(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shadow, _shadow_source = _write_plugin(
        repo, "plugins", "policy", version="9.9.9", body="Shadow.\n"
    )
    resolved, source = _write_plugin(
        tmp_path, "market", "policy", version="1.2.3", body="Resolved.\n"
    )
    assert shadow != resolved
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(repo, [source], result)

    assert result.blocking == 0
    assert specs[0].plugin_version == "1.2.3"
    assert b"Resolved." in specs[0].template_content


def test_result_json_shape_is_versioned_and_deterministic() -> None:
    result = projections.Result(operation="scan")
    result.add(
        projections.WARNING,
        "projection-overlap",
        "<plugin-stack>",
        "example",
    )

    first = json.dumps(result.to_dict(), sort_keys=True)
    second = json.dumps(result.to_dict(), sort_keys=True)

    assert first == second
    assert result.to_dict()["schema"] == projections.RESULT_SCHEMA
    assert result.to_dict()["version"] == projections.RESULT_VERSION


def test_representative_plugins_ship_valid_canonical_declarations() -> None:
    sources = [
        _source(
            REPO / "plugins" / "ai-attribution",
            "copilot-extensions",
            "ai-attribution",
        ),
        _source(
            REPO / "plugins" / "efforts",
            "copilot-extensions",
            "efforts",
        ),
    ]
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(REPO, sources, result)

    assert result.blocking == 0
    assert {spec.source_id for spec in specs} == {
        "publication-safety",
        "session-guidance",
        "completion-gate",
    }
    assert all(spec.apply_to == "**" for spec in specs)
    assert all(spec.template_bytes < projections.MAX_TEMPLATE_BYTES for spec in specs)
    assert all(
        f"[owner: {spec.plugin_name}@{spec.plugin_version}]"
        in spec.template_content.decode("utf-8")
        for spec in specs
        if spec.source_id != "session-guidance"
    )


@pytest.mark.guard
def test_context_handoff_handoff_fallback_projection_is_valid() -> None:
    """Regression test for the ``handoff-fallback`` template's forbidden-
    content scanner failure (see ThomasMichon/copilot-extensions#3079): it
    used ``$env:COPILOT_PLUGIN_ROOT`` and a hardcoded
    ``~/.copilot/session-state/...`` path as CLI-fallback documentation, both
    of which trip the dynamic-content scanner below and silently aborted
    ``manage-instruction-projections.py sync`` for every consuming repo
    before it could create or refresh any other plugin's projections.
    context-handoff's own test suite covers ``session-guidance`` but not
    ``handoff-fallback``, so this loads the real declaration and runs the
    actual forbidden-content + rendered-byte-budget validation against it,
    catching a future regression here rather than only via a downstream
    repo's sync silently aborting.
    """
    sources = [
        _source(
            REPO / "plugins" / "context-handoff",
            "copilot-extensions",
            "context-handoff",
        ),
    ]
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(REPO, sources, result)

    assert result.blocking == 0
    assert {spec.source_id for spec in specs} == {
        "session-guidance",
        "handoff-fallback",
        "awareness",
    }
    handoff_fallback = next(
        spec for spec in specs if spec.source_id == "handoff-fallback"
    )
    rendered = projections.render_projection(handoff_fallback)
    assert rendered.byte_count <= projections.MAX_PROJECTION_BYTES
