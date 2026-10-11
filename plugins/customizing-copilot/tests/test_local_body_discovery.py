"""Non-discovered bodies and ownership-validated legacy cache migration."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from test_instruction_delivery import delivery, fixture, projections
from context_budget import _repo_instruction_files


def test_both_body_classes_excluded_from_repository_discovery(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    selector = repo / spec.destination
    local = repo / delivery.local_destination(spec.destination)
    fallback = repo / delivery.fallback_destination(spec.destination)
    for name in ["AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md"]:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Repository-owned instruction floor.\n")
    explicit = {
        *[repo / name for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md")],
        repo / ".github/copilot-instructions.md",
        *repo.glob(".github/instructions/**/*.instructions.md"),
    }
    automatic, conditional = _repo_instruction_files(repo)
    assert automatic == explicit
    assert local not in automatic | conditional
    assert fallback not in automatic | conditional
    text = selector.read_text()
    assert delivery.local_destination(spec.destination) in text
    assert delivery.fallback_destination(spec.destination) in text
    assert "Never assume authorization." not in text
    assert "](" not in text and "@import" not in text
    assert delivery.RECEIPT_PREFIX not in text
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.local_destination(spec.destination)
    assert selected["modelAdmission"] == "unknown"
    assert delivery.complete_body(local.read_bytes(), delivery.parse_marker(local.read_bytes()))
    assert not delivery.complete_body(selector.read_bytes(), delivery.parse_marker(selector.read_bytes()))
    inventory = projections.instruction_delivery_inventory(repo, automatic)["categories"]
    assert inventory["local_bodies"]["count"] == 1
    assert not inventory["local_bodies"]["automatically_discovered"]
    assert inventory["reviewed_fallbacks"]["count"] == 1
    assert not inventory["reviewed_fallbacks"]["automatically_discovered"]
    legacy = repo / delivery.legacy_local_destination(spec.destination)
    legacy.write_bytes(local.read_bytes())
    automatic, _ = _repo_instruction_files(repo)
    assert legacy in automatic
    inventory = projections.instruction_delivery_inventory(repo, automatic)["categories"]
    assert inventory["legacy_local_bodies"]["count"] == 1
    assert inventory["legacy_local_bodies"]["automatically_discovered"]
    assert not inventory["local_bodies"]["automatically_discovered"]


def test_nested_local_body_paths_preserve_subdirectories() -> None:
    assert delivery.local_destination(
        ".github/instructions/policy/nested/rules.instructions.md"
    ) == ".github/copilot/context-locals/policy/nested/rules.local.md"
    assert delivery.fallback_destination(
        ".github/instructions/policy/nested/rules.instructions.md"
    ) == ".github/copilot/context-fallbacks/policy/nested/rules.md"


@pytest.mark.parametrize("name", ["AGENTS", "CLAUDE", "GEMINI", "copilot-instructions"])
def test_fallbacks_cannot_use_automatically_loaded_instruction_names(name: str) -> None:
    assert delivery.local_destination(
        f".github/instructions/policy/{name}.instructions.md"
    ).endswith(f"/{name}.local.md")
    with pytest.raises(ValueError, match="automatic instruction discovery"):
        delivery.fallback_destination(f".github/instructions/policy/{name}.instructions.md")


@pytest.mark.parametrize("case", ["canonical", "older-version", "edited", "foreign", "tracked", "race", "new-failure"])
def test_legacy_local_retirement_requires_complete_owned_publication(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    canonical = projections.render_projection(spec, include_prefer_local=False).content
    raw = canonical
    if case == "edited":
        raw = canonical.replace(b"authorization.", b"authorization!")
    elif case == "foreign":
        raw = b"Repository-owned notes."
    old.write_bytes(raw)
    if case == "older-version":
        (source.payload_root / "plugin.json").write_text('{"name":"policy","version":"1.0.1"}')
    new = repo / delivery.local_destination(spec.destination)
    if case == "tracked":
        monkeypatch.setattr(
            projections, "_resolve_git_tracked_paths", lambda root, paths: ({old.relative_to(repo).as_posix()}, False)
        )
    elif case == "race":
        original = projections._resolve_git_tracked_paths
        calls = 0

        def tracked(root: Path, paths: list[str]) -> tuple[set[str], bool]:
            nonlocal calls
            calls += 1
            if calls == 2:
                old.write_bytes(b"concurrent legacy edit")
            return original(root, paths)

        monkeypatch.setattr(projections, "_resolve_git_tracked_paths", tracked)
    elif case == "new-failure":
        def fail(path: Path, content: bytes) -> None:
            raise OSError("publication failed")

        monkeypatch.setattr(projections.transaction, "create_exclusive", fail)
    result = projections.render_local_cache(repo, lambda: [source])
    if case in {"canonical", "older-version"}:
        assert not result.blocking and not old.exists()
        assert b"Never assume authorization." in new.read_bytes()
        assert old.relative_to(repo).as_posix() in result.removed
    else:
        assert old.read_bytes() == (b"concurrent legacy edit" if case == "race" else raw)
        if case != "new-failure":
            assert new.read_bytes() == canonical
            assert any(f.check == "projection-local-cache-legacy" for f in result.findings)


def test_legacy_local_compatibility_reads_are_authenticated(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.legacy_local_destination(spec.destination)
    old.write_bytes(old.read_bytes().replace(b"authorization.", b"authorization!"))
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert "rejected" in selected["diagnostic"]


def test_eight_explicit_inline_kernels_remain_declared() -> None:
    root = Path(__file__).resolve().parents[3]
    inline = set()
    for path in (root / "plugins").glob("*/instruction-projections.json"):
        for item in json.loads(path.read_text()).get("projections", []):
            if item.get("deliveryMode") == "inline":
                inline.add((path.parent.name, item["id"]))
    assert inline == {
        ("agent-conduct-guidance", "process-hygiene-fallback"),
        ("agent-conduct-guidance", "delegation-fallback"),
        ("agent-conduct-guidance", "scratch-space-fallback"),
        ("agent-conduct-guidance", "secret-masking-fallback"),
        ("ai-attribution", "publication-safety"),
        ("copilot-extensions-harness", "contribution-boundary"),
        ("efforts", "completion-gate"),
        ("customizing-copilot", "local-cache-catchall"),
    }
