"""Worker identity resolution and parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_identities import load_worker_identity


def test_builtin_identity_resolves():
    identity = load_worker_identity("odsp-web-harness-backlog")
    assert identity.name == "odsp-web-harness-backlog"
    assert identity.description
    assert "blocked-on-external-pr" in identity.rules


def test_unknown_identity_raises():
    with pytest.raises(RegistrarError, match="no identity file found"):
        load_worker_identity("no-such-identity")


def test_empty_name_raises():
    with pytest.raises(RegistrarError, match="non-empty string"):
        load_worker_identity("")


def test_repo_local_identity_overrides_builtin(tmp_path: Path):
    local_dir = tmp_path / ".agent-dispatch" / "identities"
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


def test_missing_frontmatter_raises(tmp_path: Path):
    local_dir = tmp_path / ".agent-dispatch" / "identities"
    local_dir.mkdir(parents=True)
    (local_dir / "broken.identity.md").write_text(
        "no frontmatter here", encoding="utf-8"
    )
    with pytest.raises(RegistrarError, match="frontmatter"):
        load_worker_identity("broken", cwd=tmp_path)


def test_empty_body_raises(tmp_path: Path):
    local_dir = tmp_path / ".agent-dispatch" / "identities"
    local_dir.mkdir(parents=True)
    (local_dir / "empty.identity.md").write_text(
        "---\nname: empty\ndescription: x\n---\n\n", encoding="utf-8"
    )
    with pytest.raises(RegistrarError, match="body .the rules. must not be empty"):
        load_worker_identity("empty", cwd=tmp_path)
