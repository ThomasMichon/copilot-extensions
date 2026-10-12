"""Auto-discovered local instructions and non-discovered reviewed fallbacks."""

from __future__ import annotations

from pathlib import Path
import pytest

from test_instruction_delivery import delivery, fixture, projections
from context_budget import _repo_instruction_files

pytestmark = pytest.mark.guard


def test_only_reviewed_fallback_is_excluded_from_repository_discovery(tmp_path: Path) -> None:
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
    assert local in automatic
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
    assert inventory["local_bodies"]["automatically_discovered"]
    assert inventory["reviewed_fallbacks"]["count"] == 1
    assert not inventory["reviewed_fallbacks"]["automatically_discovered"]
    legacy = repo / delivery.legacy_local_destination(spec.destination)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(local.read_bytes())
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
            if calls == 1:
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
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.legacy_local_destination(spec.destination)
    old.write_bytes(old.read_bytes().replace(b"authorization.", b"authorization!"))
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert "rejected" in selected["diagnostic"]


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
    assert not (repo / delivery.local_destination(spec.destination)).exists()
    assert any(f.check == "projection-local-cache-ignore" for f in result.findings)
    (repo / ".gitignore").write_text(".github/instructions/**/*.local.instructions.md\n")
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking
    assert not old.exists()
    assert (repo / delivery.local_destination(spec.destination)).exists()


def test_ignore_probe_permission_failure_is_not_non_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import instruction_delivery_io as io
    monkeypatch.setattr(io.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 128, stdout=b"", stderr=b"fatal: permission denied"
    ))
    assert not io.ignored_local_paths(tmp_path, [".github/copilot/context-locals/policy/a.local.md"])


def test_invalid_new_unpaired_local_falls_back_to_authenticated_legacy(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    old.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    new = repo / delivery.local_destination(spec.destination)
    new.parent.mkdir(parents=True)
    new.write_bytes(b"foreign new file")
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.legacy_local_destination(spec.destination)


def test_legacy_index_race_is_rechecked_before_retirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, source, spec = fixture(tmp_path)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    raw = projections.render_projection(spec, include_prefer_local=False).content
    old.write_bytes(raw)
    calls = 0

    def tracked(root: Path, paths: list[str]) -> tuple[set[str], bool]:
        nonlocal calls
        if paths == [delivery.legacy_local_destination(spec.destination)]:
            calls += 1
        return ({old.relative_to(root).as_posix()} if calls >= 2 else set()), False

    monkeypatch.setattr(projections, "_resolve_git_tracked_paths", tracked)
    result = projections.render_local_cache(repo, lambda: [source])
    assert old.read_bytes() == raw and calls == 2
    assert any("became tracked" in f.message for f in result.findings)


def test_legacy_edit_during_final_tracking_probe_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    old = repo / delivery.legacy_local_destination(spec.destination)
    old.parent.mkdir(parents=True)
    old.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    original = projections._resolve_git_tracked_paths
    legacy_probes = 0

    def tracking(root: Path, paths: list[str]) -> tuple[set[str], bool]:
        nonlocal legacy_probes
        if paths == [delivery.legacy_local_destination(spec.destination)]:
            legacy_probes += 1
            if legacy_probes == 2:
                old.write_bytes(b"edited during final tracking probe")
        return original(root, paths)

    monkeypatch.setattr(projections, "_resolve_git_tracked_paths", tracking)
    result = projections.render_local_cache(repo, lambda: [source])
    assert old.read_bytes() == b"edited during final tracking probe"
    assert any("changed during tracking" in f.message for f in result.findings)


def test_new_local_staged_during_render_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    local = repo / delivery.local_destination(spec.destination)
    before = local.read_bytes()
    template = source.payload_root / spec.template
    template.write_bytes(template.read_bytes().replace(b"Never assume", b"Do not assume"))
    original = projections._transactional_write
    tracked_now = False

    def write(*args, **kwargs):
        nonlocal tracked_now
        tracked_now = True
        return original(*args, **kwargs)

    monkeypatch.setattr(projections, "_transactional_write", write)
    monkeypatch.setattr(
        projections, "_resolve_git_tracked_paths",
        lambda root, paths: (set(paths) if tracked_now else set(), False),
    )
    result = projections.render_local_cache(repo, lambda: [source])
    assert result.blocking
    assert local.read_bytes() == before
