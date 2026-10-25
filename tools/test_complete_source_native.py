"""Opt-in fresh-Git reconstruction; excluded from the unit-only promotion packet."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from promotion_complete_source import canonical_bytes, verify_source_archive
from promotion_source_archive import prepare_source, read_source_assets
from promotion_unit_tests.test_complete_source import case

pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_SOURCE_ARCHIVE_INTEGRATION") != "1",
    reason="opt-in real Git source reconstruction, not an eligible promotion unit",
)


def test_fresh_git_objects_reconstruct_every_tracked_blob_mode_and_export_attribute(tmp_path: Path):
    repo = tmp_path / "repository"
    repo.mkdir()
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}

    def git(*arguments, data=None):
        result = subprocess.run(
            ["git", "-c", f"core.hooksPath={hooks}", "-c", "core.autocrlf=false",
             "-c", "user.name=Source Test", "-c", "user.email=source@example.invalid", *arguments],
            cwd=repo, env=environment, input=data, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return result.stdout

    git("init")
    git("commit", "--allow-empty", "-m", "base")
    main = git("rev-parse", "HEAD").decode().strip()
    files, _identity = case()
    records = []
    for name, (mode, raw) in files.items():
        blob = git("hash-object", "-w", "--stdin", data=raw).decode().strip()
        records.append(f"{mode} {blob}\t{name}".encode() + b"\0")
    git("update-index", "-z", "--index-info", data=b"".join(records))
    git("commit", "-m", "candidate")
    candidate = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    receipt = {
        "dev_sha": main, "candidate_sha": candidate, "main_before": main, "control_sha": main,
        "tree": tree, "profile": "candidate-units-v3",
    }
    index = {"schema_version": 2, "dev_head": main, "content_tree": tree}
    directory = tmp_path / "subjects"
    manifest = prepare_source(directory, repo=repo, index_raw=canonical_bytes(index), receipt=receipt)
    identity = {
        **receipt, "content_tree": tree, "source_index_sha256": hashlib.sha256(canonical_bytes(index)).hexdigest(),
        "qualification_digest": hashlib.sha256(canonical_bytes(receipt)).hexdigest(),
    }
    del identity["profile"]
    subjects = read_source_assets(directory, identity=identity)
    raw = next(raw for name, raw in subjects.items() if name.endswith(".zip"))
    assert verify_source_archive(raw, manifest, identity=identity) == files
    assert manifest["tree"] == tree
