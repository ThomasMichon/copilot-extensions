"""Worker identity resolution and parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_identities import _BUILTIN_DIR, load_worker_identity


def test_builtin_identity_resolves():
    builtin = next(_BUILTIN_DIR.glob("*.identity.md"))
    identity = load_worker_identity(builtin.stem.replace(".identity", ""))
    assert identity.name
    assert identity.description
    assert identity.rules


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
