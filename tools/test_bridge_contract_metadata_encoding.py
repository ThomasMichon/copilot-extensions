"""Committed metadata retains its UTF-8 bytes even on legacy-code-page hosts."""
from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

import agent_bridge_contract_git as evidence
import agent_bridge_contract_registry as codec
import prepare_bridge_contract_evidence as preparation


@pytest.mark.parametrize("reader", ["authority", "preparation"])
def test_committed_provenance_is_not_locale_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str,
) -> None:
    run = subprocess.run

    def git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
        return run(
            ["git", "-C", str(tmp_path), *arguments], capture_output=True, check=True,
            timeout=10, env=evidence.clean_git_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    chunk = codec.encode({
        "schema": codec.CHUNK_SCHEMA, "schema_version": 1, "contract": "example",
        "record_offset": 0, "provenance": [{"note": "caf\u00e9 \u65e5\u672c\u8a9e"}],
    })
    sha256 = codec.digest(chunk)
    relative = f"{codec.PROVENANCE}example/{sha256}.json"
    index = {
        "schema_version": 2, "deferred_contracts": [],
        "contracts": [{
            "id": "example", "provenance": [], "provenance_chunks": [{
                "contract": "example", "path": relative, "sha256": sha256, "record_count": 1,
            }],
        }],
    }
    for path, data in {codec.REGISTRY: codec.encode(index), relative: chunk}.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for args in [
        ["init", "-q"], ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-qm", "Seed Unicode metadata"],
    ]:
        git(*args)
    commit = git("rev-parse", "HEAD").stdout.decode("ascii").strip()

    def legacy_locale(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        if kwargs.get("text"):
            kwargs.setdefault("encoding", "cp1252")
        return run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", legacy_locale)
    if reader == "authority":
        capture = importlib.import_module("capture_bridge_current_authority")
        monkeypatch.setattr(evidence, "REPO", tmp_path)
        loaded = codec.load(
            tmp_path, read=lambda path: capture.committed(commit, path).encode("utf-8"),
        )
        assert loaded.inputs[relative] == chunk
        assert loaded.materialized["contracts"][0]["provenance"] == [
            {"note": "caf\u00e9 \u65e5\u672c\u8a9e"},
        ]
    else:
        packet = tmp_path / preparation.PACKET
        packet.parent.mkdir(parents=True, exist_ok=True)
        packet.write_bytes(codec.encode({"captured_from": {"commit": commit}}))
        assert preparation.prepare(tmp_path, "https://example.invalid/repository.git") == commit
