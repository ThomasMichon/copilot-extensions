"""Worker identity resolution and parsing."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_identities import (
    _BUILTIN_DIR,
    describe_worker_identity,
    list_worker_identities,
    load_worker_identity,
)


def test_builtin_identity_resolves():
    builtin = next(_BUILTIN_DIR.glob("*.identity.md"))
    identity = load_worker_identity(builtin.stem.replace(".identity", ""))
    assert identity.name
    assert identity.description
    assert identity.rules


@pytest.mark.guard
def test_no_repo_specific_builtin_identity_shipped(tmp_path):
    """Regression guard for the odsp-web-harness-backlog contamination.

    The built-in tier is meant to be generic, package-owned content; an
    adopter-private identity belongs in that adopter's own repo-local
    override, not here. This asserts the removed adopter-specific identity
    stays removed, rather than relying on the glob-based
    ``test_builtin_identity_resolves`` above, which would still pass if a
    private built-in were reintroduced alongside the generic default.

    ``cwd=tmp_path`` (an empty directory) makes this deterministic: without
    it, resolution defaults to the caller's own ``Path.cwd()``, and an
    adopter that legitimately keeps its own repo-local
    ``odsp-web-harness-backlog.identity.md`` override (tier 1 -- exactly what
    this contamination fix expects such an adopter to do) would make this
    guard pass or fail depending on which directory pytest happens to run
    from, rather than on the package's own built-in tier.
    """
    names = {p.stem.replace(".identity", "") for p in _BUILTIN_DIR.glob("*.identity.md")}
    assert "odsp-web-harness-backlog" not in names
    with pytest.raises(RegistrarError, match="no identity file found"):
        load_worker_identity("odsp-web-harness-backlog", cwd=tmp_path)


def test_unknown_identity_raises():
    with pytest.raises(RegistrarError, match="no identity file found"):
        load_worker_identity("no-such-identity")


def test_empty_name_raises():
    with pytest.raises(RegistrarError, match="non-empty string"):
        load_worker_identity("")


def test_repo_local_identity_overrides_builtin(tmp_path: Path):
    local_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    local_dir.mkdir(parents=True)
    (local_dir / "custom.identity.md").write_text(
        "---\nname: custom\ndescription: A custom identity.\n---\n\n"
        "Follow the custom rules.\n",
        encoding="utf-8",
    )
    identity = load_worker_identity("custom", cwd=tmp_path)
    assert identity.name == "custom"
    assert identity.description == "A custom identity."
    assert identity.rules == "Follow the custom rules."


def test_legacy_repo_local_identity_falls_back(tmp_path: Path):
    local_dir = tmp_path / ".agent-dispatch" / "identities"
    local_dir.mkdir(parents=True)
    (local_dir / "legacy.identity.md").write_text(
        "---\nname: legacy\ndescription: Legacy identity.\n---\n\n"
        "Follow the legacy rules.\n",
        encoding="utf-8",
    )
    identity = load_worker_identity("legacy", cwd=tmp_path)
    assert identity.name == "legacy"
    assert identity.description == "Legacy identity."
    assert identity.rules == "Follow the legacy rules."


def test_marketplace_overlay_identity_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    overlay_dir = (
        tmp_path
        / ".copilot-extensions"
        / "agent-dispatch"
        / "marketplaces"
        / "mp-test"
        / "identities"
    )
    base_dir.mkdir(parents=True)
    overlay_dir.mkdir(parents=True)
    (base_dir / "custom.identity.md").write_text(
        "---\nname: custom\ndescription: Base identity.\n---\n\n"
        "Follow the base rules.\n",
        encoding="utf-8",
    )
    (overlay_dir / "custom.identity.md").write_text(
        "---\nname: custom\ndescription: Overlay identity.\n---\n\n"
        "Follow the overlay rules.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        '{"marketplaceId":"mp-test"}',
    )
    identity = load_worker_identity("custom", cwd=tmp_path)
    assert identity.description == "Overlay identity."
    assert identity.rules == "Follow the overlay rules."


def test_missing_frontmatter_raises(tmp_path: Path):
    local_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    local_dir.mkdir(parents=True)
    (local_dir / "broken.identity.md").write_text(
        "no frontmatter here", encoding="utf-8"
    )
    with pytest.raises(RegistrarError, match="frontmatter"):
        load_worker_identity("broken", cwd=tmp_path)


def test_empty_body_raises(tmp_path: Path):
    local_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    local_dir.mkdir(parents=True)
    (local_dir / "empty.identity.md").write_text(
        "---\nname: empty\ndescription: x\n---\n\n", encoding="utf-8"
    )
    with pytest.raises(RegistrarError, match="body .the rules. must not be empty"):
        load_worker_identity("empty", cwd=tmp_path)


def _write_identity(directory: Path, name: str, description: str, rules: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.identity.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{rules}\n",
        encoding="utf-8",
    )
    return path


def _packaged_names() -> set[str]:
    return {p.name[: -len(".identity.md")] for p in _BUILTIN_DIR.glob("*.identity.md")}


@pytest.fixture
def no_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)


def test_list_packaged_only(tmp_path: Path, no_overlay):
    entries = list_worker_identities(cwd=tmp_path)
    assert [e.name for e in entries] == sorted(_packaged_names())
    for entry in entries:
        assert entry.source == "packaged"
        assert entry.shadows == ()
        assert entry.error is None
        assert entry.identity is not None and entry.identity.description


def test_list_repo_override_shadows_packaged(tmp_path: Path, no_overlay):
    packaged = sorted(_packaged_names())[0]
    local = _write_identity(
        tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities",
        packaged, "Repo override.", "Repo rules.",
    )
    entries = {e.name: e for e in list_worker_identities(cwd=tmp_path)}
    entry = entries[packaged]
    assert entry.source == "repository"
    assert entry.path == str(local)
    assert entry.identity.description == "Repo override."
    assert [s for s, _ in entry.shadows] == ["packaged"]
    # Parity with runtime resolution.
    assert load_worker_identity(packaged, cwd=tmp_path).source_path == entry.path


def test_list_legacy_dir_and_malformed_entry(tmp_path: Path, no_overlay):
    _write_identity(tmp_path / ".agent-dispatch" / "identities", "legacy", "Legacy.", "Rules.")
    broken_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    broken_dir.mkdir(parents=True)
    (broken_dir / "broken.identity.md").write_text("no frontmatter", encoding="utf-8")
    entries = {e.name: e for e in list_worker_identities(cwd=tmp_path)}
    assert entries["legacy"].source == "legacy-repository"
    assert entries["legacy"].identity.rules == "Rules."
    assert entries["broken"].identity is None
    assert "frontmatter" in entries["broken"].error


def test_list_reports_yaml_value_error_without_hiding_others(tmp_path: Path, no_overlay):
    repo_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    repo_dir.mkdir(parents=True)
    (repo_dir / "bad.identity.md").write_text(
        "---\nname: bad\ndescription: 2026-13-01\n---\n\nbody\n", encoding="utf-8"
    )
    entries = {e.name: e for e in list_worker_identities(cwd=tmp_path)}
    assert entries["bad"].identity is None
    assert entries["bad"].error
    assert _packaged_names() <= set(entries)


def test_list_case_variants_follow_runtime_resolution(tmp_path: Path, no_overlay):
    repo_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    legacy_dir = tmp_path / ".agent-dispatch" / "identities"
    _write_identity(repo_dir, "Custom-Name", "Repo.", "Repo rules.")
    _write_identity(legacy_dir, "custom-name", "Legacy.", "Legacy rules.")
    packaged = sorted(_packaged_names())[0]
    _write_identity(repo_dir, packaged.upper(), "Upper override.", "Rules.")
    case_insensitive = (repo_dir / "custom-name.identity.md").is_file()

    entries = list_worker_identities(cwd=tmp_path)
    for entry in entries:
        assert load_worker_identity(entry.name, cwd=tmp_path).source_path == entry.path
    group = [e for e in entries if e.name.lower() == "custom-name"]
    packaged_group = [e for e in entries if e.name.lower() == packaged.lower()]
    if case_insensitive:
        assert len(group) == 1
        assert group[0].source == "repository"
        assert group[0].identity.description == "Repo."
        assert [s for s, _ in group[0].shadows] == ["legacy-repository"]
        assert [e.name for e in packaged_group] == [packaged]
        assert packaged_group[0].source == "repository"
        assert [s for s, _ in packaged_group[0].shadows] == ["packaged"]
    else:
        assert sorted(e.name for e in group) == ["Custom-Name", "custom-name"]
        assert all(e.shadows == () for e in group)
        assert sorted(e.name for e in packaged_group) == sorted([packaged, packaged.upper()])


@pytest.mark.parametrize("link", ["hardlink", "symlink"])
def test_list_keeps_linked_files_under_distinct_names(tmp_path: Path, no_overlay, link):
    repo_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    alpha = _write_identity(repo_dir, "alpha", "Shared.", "Rules.")
    beta = repo_dir / "beta.identity.md"
    try:
        if link == "hardlink":
            os.link(alpha, beta)
        else:
            beta.symlink_to(alpha)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create {link}: {exc}")
    entries = {e.name: e for e in list_worker_identities(cwd=tmp_path)}
    for name in ("alpha", "beta"):
        assert entries[name].source == "repository"
        assert load_worker_identity(name, cwd=tmp_path).source_path == entries[name].path


def test_describe_returns_rules_and_provenance(tmp_path: Path, no_overlay):
    _write_identity(tmp_path / ".agent-dispatch" / "identities", "custom", "Legacy one.", "Old rules.")
    path = _write_identity(
        tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities",
        "custom", "Current one.", "New rules.",
    )
    data = describe_worker_identity("custom", cwd=tmp_path).to_dict(include_rules=True)
    assert data == {
        "name": "custom",
        "description": "Current one.",
        "source": "repository",
        "path": str(path),
        "shadows": [
            {
                "source": "legacy-repository",
                "path": str(tmp_path / ".agent-dispatch" / "identities" / "custom.identity.md"),
            }
        ],
        "error": None,
        "rules": "New rules.",
    }


def test_cli_identities_list_and_show(tmp_path: Path, no_overlay, capsys):
    from agent_dispatch.__main__ import main

    _write_identity(
        tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities",
        "custom", "Custom.", "Custom rules.",
    )
    assert main(["identities", "list", "--repo-root", str(tmp_path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    names = [entry["name"] for entry in listed]
    assert names == sorted(_packaged_names() | {"custom"})
    assert set(listed[0]) == {"name", "description", "source", "path", "shadows", "error"}

    assert main(["identities", "show", "custom", "--repo-root", str(tmp_path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["source"] == "repository"
    assert shown["rules"] == "Custom rules."


def test_cli_identities_show_unknown_name_fails(tmp_path: Path, no_overlay, capsys):
    from agent_dispatch.__main__ import main

    assert main(["identities", "show", "no-such-identity", "--repo-root", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no identity file found" in captured.err


def test_cli_identities_list_rejects_missing_repo_root(tmp_path: Path, capsys):
    from agent_dispatch.__main__ import main

    missing = tmp_path / "missing"
    assert main(["identities", "list", "--repo-root", str(missing)]) == 2
    assert "is not a directory" in capsys.readouterr().err
