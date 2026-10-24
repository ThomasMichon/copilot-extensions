from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import prepare_bridge_contract_evidence as preparation

COMMIT = "1" * 40
REMOTE = "https://github.com/example/repository.git"


def _packet(root: Path, commit=COMMIT) -> None:
    path = root / preparation.PACKET
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"captured_from": {"commit": commit}}), encoding="utf-8")


def _runner(monkeypatch, results):
    calls = []

    def run(root, *arguments, timeout=10):
        calls.append((arguments, timeout))
        return subprocess.CompletedProcess(arguments, next(results), "", "")

    monkeypatch.setattr(preparation, "_run", run)
    monkeypatch.setattr(preparation, "require_local_object_reads", lambda: None)
    return calls


def test_available_capture_needs_no_fetch(tmp_path, monkeypatch):
    _packet(tmp_path)
    calls = _runner(monkeypatch, iter([0]))
    assert preparation.prepare(tmp_path, REMOTE) == COMMIT
    assert calls == [(("cat-file", "-e", f"{COMMIT}^{{commit}}"), 10)]


def test_missing_capture_fetches_only_exact_object_from_selected_remote(tmp_path, monkeypatch):
    _packet(tmp_path)
    packet = tmp_path / preparation.PACKET
    data = json.loads(packet.read_text("utf-8"))
    data["remote"] = "https://untrusted.example.invalid/substitute.git"
    packet.write_text(json.dumps(data), encoding="utf-8")
    calls = _runner(monkeypatch, iter([1, 0, 0]))
    assert preparation.prepare(tmp_path, REMOTE) == COMMIT
    assert calls == [
        (("cat-file", "-e", f"{COMMIT}^{{commit}}"), 10),
        (("fetch", "--no-tags", "--depth=1", "--no-recurse-submodules",
          "--no-auto-maintenance", "--no-write-fetch-head", REMOTE, COMMIT), 60),
        (("cat-file", "-e", f"{COMMIT}^{{commit}}"), 10),
    ]


@pytest.mark.parametrize("results,diagnostic", [
    ([1, 1], "could not retain"),
    ([1, 0, 1], "still unavailable"),
])
def test_missing_evidence_never_becomes_success(tmp_path, monkeypatch, results, diagnostic):
    _packet(tmp_path)
    _runner(monkeypatch, iter(results))
    with pytest.raises(RuntimeError, match=diagnostic):
        preparation.prepare(tmp_path, REMOTE)


@pytest.mark.parametrize("commit", ["HEAD", "1" * 39, "1" * 40 + ":file", None, 1])
def test_capture_revision_syntax_refuses_before_git(tmp_path, monkeypatch, commit):
    _packet(tmp_path, commit)
    calls = _runner(monkeypatch, iter([]))
    with pytest.raises(ValueError, match="exact full commit"):
        preparation.prepare(tmp_path, REMOTE)
    assert calls == []


@pytest.mark.parametrize("record", [None, [], {}, {"captured_from": None}, {"captured_from": []}])
def test_malformed_capture_container_refuses_before_git(tmp_path, monkeypatch, record):
    _packet(tmp_path)
    (tmp_path / preparation.PACKET).write_text(json.dumps(record), encoding="utf-8")
    calls = _runner(monkeypatch, iter([]))
    with pytest.raises(ValueError, match="exact full commit"):
        preparation.prepare(tmp_path, REMOTE)
    assert calls == []


@pytest.mark.parametrize("remote", [
    "file:///tmp/repository.git", "ext::arbitrary command", "http://example.invalid/repo.git",
    "https://user:secret@example.invalid/repo.git", "https://example.invalid/repo.git?credential=x",
    "https://example.invalid/repo.git#main", "https://@example.invalid/repo.git",
])
def test_untrusted_remote_forms_refuse_before_git(tmp_path, monkeypatch, remote):
    _packet(tmp_path)
    calls = _runner(monkeypatch, iter([]))
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        preparation.prepare(tmp_path, remote)
    assert calls == []


def test_native_git_captured_tree_survives_without_an_advertised_branch(tmp_path, monkeypatch):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def git(*arguments):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *arguments],
            input="", capture_output=True, text=True, check=True, timeout=10, **options,
        ).stdout.strip()

    git("init")
    tree = git("hash-object", "-w", "-t", "tree", "--stdin")
    commit = subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c",
         "user.email=fixture@example.invalid", "commit-tree", tree, "-m", "captured checkpoint"],
        capture_output=True, text=True, check=True, timeout=10, **options,
    ).stdout.strip()
    _packet(tmp_path, commit)
    assert git("for-each-ref") == ""
    with monkeypatch.context() as poisoned:
        poisoned.setenv("GIT_DIR", str(tmp_path / "unrelated"))
        poisoned.setenv("GIT_WORK_TREE", str(tmp_path / "unrelated-tree"))
        assert preparation.prepare(tmp_path, REMOTE) == commit
    assert git("rev-parse", f"{commit}^{{tree}}") == tree
    assert git("for-each-ref") == ""


def test_ci_prepares_checkpoint_before_local_only_contract_validation():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text("utf-8")
    assert source.index("python tools/prepare_bridge_contract_evidence.py") < source.index(
        "run: python tools/check-agent-bridge-contracts.py",
    )
    assert '${{ github.server_url }}/${{ github.repository }}.git' in source
