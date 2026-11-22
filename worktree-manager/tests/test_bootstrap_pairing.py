import hashlib
import json
from pathlib import Path

import pytest

from worktree_manager.bootstrap_pairing import remember_bound, select
from worktree_manager.bootstrap_harness import _git


def test_precedence_and_foreign_receipt(tmp_path: Path) -> None:
    remote = "https://github.com/example/harness"
    local = tmp_path / "machine" / "config.yaml"
    key = hashlib.sha256(remote.encode()).hexdigest()
    saved = tmp_path / "Apps/.copilot-extensions/pairings" / (key + ".json")
    assert select(local, remote, None, tmp_path) == ("absent", None)
    assert select(local, "", None, tmp_path) == ("absent", None)
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({"version": 1, "harness": remote, "knowledge": "example/notes"}))
    assert select(local, remote, None, tmp_path) == ("portable", "https://github.com/example/notes")
    saved.write_text(json.dumps({"version": 1, "harness": "other", "knowledge": "example/notes"}))
    with pytest.raises(ValueError, match="Invalid portable"):
        select(local, remote, None, tmp_path)
    assert select(local, remote, "example/selected", tmp_path)[0] == "explicit"
    local.parent.mkdir()
    local.write_text("knowledge_repo: retained\n")
    assert select(local, remote, "example/selected", tmp_path) == ("local", "retained")


def test_remember_requires_local_binding_and_preserves_existing_default(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    remote = "example/harness"
    checkout = tmp_path / "notes"
    checkout.mkdir()
    _git(checkout, "init", "--quiet")
    _git(checkout, "remote", "add", "origin", "https://github.com/example/notes")
    with pytest.raises(ValueError, match="matching local binding"):
        remember_bound(config, remote, "example/notes", tmp_path, registered_knowledge=checkout)
    config.write_text("knowledge_repo: notes\n")
    with pytest.raises(ValueError, match="different origin"):
        remember_bound(config, remote, "another/notes", tmp_path, registered_knowledge=checkout)
    remember_bound(config, remote, "example/notes", tmp_path, registered_knowledge=checkout)
    remember_bound(config, remote, "example/notes", tmp_path, registered_knowledge=checkout)
    second_machine = tmp_path / "elsewhere" / "config.yaml"
    assert select(second_machine, remote, None, tmp_path) == ("portable", "https://github.com/example/notes")
    _git(checkout, "remote", "set-url", "origin", "https://github.com/another/notes")
    with pytest.raises(ValueError, match="explicit replacement"):
        remember_bound(config, remote, "another/notes", tmp_path, registered_knowledge=checkout)
    assert select(second_machine, remote, None, tmp_path)[1] == "https://github.com/example/notes"
