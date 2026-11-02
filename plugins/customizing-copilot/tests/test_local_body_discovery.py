"""Auto-discovered local instructions and non-discovered reviewed fallbacks."""

from __future__ import annotations

from pathlib import Path
import pytest

from test_instruction_delivery import delivery, fixture, managed_path, projections
from context_budget import _repo_instruction_files

pytestmark = pytest.mark.guard


def test_only_reviewed_fallback_is_excluded_from_repository_discovery(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    managed = managed_path(repo, spec.destination)
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
    assert local not in automatic
    assert managed in automatic
    assert fallback not in automatic | conditional
    text = selector.read_text()
    assert delivery.managed_destination(spec.destination, spec.plugin) in text
    assert delivery.fallback_destination(spec.destination) in text
    assert "Never assume authorization." not in text
    assert "](" not in text and "@import" not in text
    assert delivery.RECEIPT_PREFIX not in text
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == managed.relative_to(repo).as_posix()
    assert selected["modelAdmission"] == "unknown"
    assert managed.read_bytes() == (source.payload_root / spec.template).read_bytes()
    assert not delivery.complete_body(selector.read_bytes(), delivery.parse_marker(selector.read_bytes()))
    inventory = projections.instruction_delivery_inventory(repo, automatic)["categories"]
    assert inventory["local_bodies"]["count"] == 1
    assert inventory["local_bodies"]["automatically_discovered"]
    assert inventory["reviewed_fallbacks"]["count"] == 1
    assert not inventory["reviewed_fallbacks"]["automatically_discovered"]
    legacy = repo / delivery.legacy_local_destination(spec.destination)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    automatic, _ = _repo_instruction_files(repo)
    assert legacy not in automatic | conditional
    inventory = projections.instruction_delivery_inventory(repo, automatic)["categories"]
    assert inventory["legacy_local_bodies"]["count"] == 1
    assert not inventory["legacy_local_bodies"]["automatically_discovered"]
    assert inventory["local_bodies"]["automatically_discovered"]


def test_nested_local_body_paths_preserve_subdirectories() -> None:
    assert delivery.local_destination(
        ".github/instructions/policy/nested/rules.instructions.md"
    ) == ".github/instructions/policy/nested/rules.local.instructions.md"
    assert delivery.fallback_destination(
        ".github/instructions/policy/nested/rules.instructions.md"
    ) == ".github/copilot/context-fallbacks/policy/nested/rules.md"


@pytest.mark.parametrize("name", ["AGENTS", "CLAUDE", "GEMINI", "copilot-instructions"])
def test_fallbacks_cannot_use_automatically_loaded_instruction_names(name: str) -> None:
    assert delivery.local_destination(
        f".github/instructions/policy/{name}.instructions.md"
    ).endswith(f"/{name}.local.instructions.md")
    with pytest.raises(ValueError, match="automatic instruction discovery"):
        delivery.fallback_destination(f".github/instructions/policy/{name}.instructions.md")


@pytest.mark.parametrize("case", ["canonical", "older-version", "edited", "foreign", "tracked", "new-failure"])
def test_legacy_local_outside_reserved_namespace_is_retained_and_reported(
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
    new = repo / delivery.managed_destination(spec.destination, spec.plugin)
    if case == "tracked":
        monkeypatch.setattr(
            projections, "_resolve_git_tracked_paths", lambda root, paths: ({old.relative_to(repo).as_posix()}, False)
        )
    elif case == "new-failure":
        def fail(path: Path, content: bytes) -> None:
            raise OSError("publication failed")

        monkeypatch.setattr(projections.transaction, "atomic_write", fail)
    result = projections.render_local_cache(repo, lambda: [source])
    assert old.read_bytes() == raw
    assert old.relative_to(repo).as_posix() not in result.removed
    assert any(f.check == "projection-local-cache-legacy" for f in result.findings)
    if case == "new-failure":
        assert result.blocking and not new.exists()
    else:
        assert not result.blocking
        assert new.read_bytes() == (source.payload_root / spec.template).read_bytes()


def test_legacy_local_compatibility_bytes_do_not_override_reviewed_fallback(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.fallback_destination(spec.destination)
    old.write_bytes(old.read_bytes().replace(b"authorization.", b"authorization!"))
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert selected["diagnostic"] is None


def test_disabled_legacy_source_is_retained_with_visible_migration_finding(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    raw = projections.render_projection(spec, include_prefer_local=False).content
    old.write_bytes(raw)
    result = projections.render_local_cache(repo, lambda: [])
    assert old.read_bytes() == raw
    assert any(
        f.check == "projection-local-cache-legacy" and old.name in f.path
        for f in result.findings
    )


def test_git_adopter_requires_new_ignore_rule_before_publication(tmp_path: Path) -> None:
    import subprocess
    repo, source, spec = fixture(tmp_path)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    raw = projections.render_projection(spec, include_prefer_local=False).content
    old.write_bytes(raw)
    result = projections.render_local_cache(repo, lambda: [source])
    assert old.read_bytes() == raw
    assert not (repo / delivery.managed_destination(spec.destination, spec.plugin)).exists()
    assert any(f.check == "projection-local-cache-ignore" for f in result.findings)
    (repo / ".gitignore").write_text(".github/instructions/**/*.local.instructions.md\n")
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking
    assert old.read_bytes() == raw
    assert (repo / delivery.managed_destination(spec.destination, spec.plugin)).exists()


def test_ignore_probe_permission_failure_is_not_non_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import instruction_delivery_io as io
    monkeypatch.setattr(io.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 128, stdout=b"", stderr=b"fatal: permission denied"
    ))
    assert not io.ignored_local_paths(tmp_path, [".github/copilot/context-locals/policy/a.local.md"])


def test_unpaired_legacy_siblings_do_not_establish_a_current_managed_copy(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    old.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    new = repo / delivery.local_destination(spec.destination)
    new.parent.mkdir(parents=True)
    new.write_bytes(b"foreign new file")
    with pytest.raises(ValueError, match="no managed copy"):
        projections.resolve_instruction_source(repo, spec.destination, [source])


def test_new_local_staged_after_initial_tracking_probe_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    local = repo / delivery.managed_destination(spec.destination, spec.plugin)
    before = local.read_bytes()
    template = source.payload_root / spec.template
    template.write_bytes(template.read_bytes().replace(b"Never assume", b"Do not assume"))
    calls = 0

    def tracking(root: Path, paths: list[str]) -> tuple[set[str], bool]:
        nonlocal calls
        calls += 1
        return (set(paths) if calls >= 2 else set(), False)

    monkeypatch.setattr(projections, "_resolve_git_tracked_paths", tracking)
    result = projections.render_local_cache(repo, lambda: [source])
    assert result.blocking
    assert local.read_bytes() == before
