from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import prepare_bridge_contract_evidence as preparation
import agent_bridge_contract_registry as codec
import bridge_http47_evidence as evidence
import bridge_http47_validation as validation

COMMIT = "1" * 40
REMOTE = "https://github.com/example/repository.git"


def _packet(root: Path, commit=COMMIT) -> None:
    for relative in preparation.PACKETS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"captured_from": {"commit": commit}}), encoding="utf-8")


def _registry() -> dict:
    return {
        "schema_version": 1,
        "contracts": [{"id": "example", "provenance": [{"commit": COMMIT}]}],
        "deferred_contracts": [],
    }


def _runner(monkeypatch, results, metadata=None):
    calls = []
    metadata = {codec.REGISTRY: codec.encode(_registry()).decode()} if metadata is None else metadata

    def run(root, *arguments, timeout=10):
        calls.append((arguments, timeout))
        if arguments[0] == "show":
            path = arguments[1].split(":", 1)[1]
            return subprocess.CompletedProcess(
                arguments, 0 if path in metadata else 1, metadata.get(path, ""), "",
            )
        return subprocess.CompletedProcess(arguments, next(results), "", "")

    monkeypatch.setattr(preparation, "_run", run)
    monkeypatch.setattr(preparation, "require_local_object_reads", lambda: None)
    return calls


def test_available_capture_needs_no_fetch(tmp_path, monkeypatch):
    _packet(tmp_path)
    calls = _runner(monkeypatch, iter([0]))
    assert preparation.prepare(tmp_path, REMOTE) == COMMIT
    assert calls == [
        (("cat-file", "-e", f"{COMMIT}^{{commit}}"), 10),
        (("show", f"{COMMIT}:{codec.REGISTRY}"), 10),
    ]


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
        (("show", f"{COMMIT}:{codec.REGISTRY}"), 10),
    ]


def test_each_distinct_required_capture_is_prepared_and_validated(tmp_path, monkeypatch):
    _packet(tmp_path)
    second = "2" * 40
    for relative in preparation.PACKETS[1:]:
        (tmp_path / relative).write_text(json.dumps({
            "captured_from": {"commit": second},
        }), encoding="utf-8")
    calls = _runner(monkeypatch, iter([0, 1, 0, 0]))
    assert preparation.prepare(tmp_path, REMOTE) == COMMIT
    assert calls == [
        (("cat-file", "-e", f"{COMMIT}^{{commit}}"), 10),
        (("show", f"{COMMIT}:{codec.REGISTRY}"), 10),
        (("cat-file", "-e", f"{second}^{{commit}}"), 10),
        (("fetch", "--no-tags", "--depth=1", "--no-recurse-submodules",
          "--no-auto-maintenance", "--no-write-fetch-head", REMOTE, second), 60),
        (("cat-file", "-e", f"{second}^{{commit}}"), 10),
        (("show", f"{second}:{codec.REGISTRY}"), 10),
    ]


@pytest.mark.parametrize("relative", preparation.PACKETS[1:])
@pytest.mark.parametrize("fault", ["missing", "malformed"])
def test_required_later_capture_refuses_before_git(tmp_path, monkeypatch, relative, fault):
    _packet(tmp_path)
    path = tmp_path / relative
    if fault == "missing":
        path.unlink()
    else:
        path.write_text('{"captured_from":{"commit":"HEAD"}}', encoding="utf-8")
    calls = _runner(monkeypatch, iter([]))
    with pytest.raises((FileNotFoundError, ValueError)):
        preparation.prepare(tmp_path, REMOTE)
    assert calls == []


@pytest.mark.parametrize("fault", ["none", "missing", "hash"])
def test_preparation_reads_complete_captured_provenance_chunks(tmp_path, monkeypatch, fault):
    _packet(tmp_path)
    chunk = codec.encode({
        "schema": codec.CHUNK_SCHEMA, "schema_version": 1, "contract": "example",
        "record_offset": 0, "provenance": [{"commit": COMMIT}],
    })
    sha256 = codec.digest(chunk)
    relative = f"{codec.PROVENANCE}example/{sha256}.json"
    registry = _registry()
    registry["schema_version"] = 2
    registry["contracts"][0].update(provenance=[], provenance_chunks=[{
        "contract": "example", "path": relative, "sha256": sha256, "record_count": 1,
    }])
    metadata = {codec.REGISTRY: codec.encode(registry).decode(), relative: chunk.decode()}
    if fault == "missing":
        metadata.pop(relative)
    elif fault == "hash":
        metadata[relative] += " "
    calls = _runner(monkeypatch, iter([0]), metadata)
    if fault == "none":
        assert preparation.prepare(tmp_path, REMOTE) == COMMIT
    else:
        with pytest.raises(ValueError, match="metadata is unavailable|hash mismatch"):
            preparation.prepare(tmp_path, REMOTE)
    assert calls[-1] == (("show", f"{COMMIT}:{relative}"), 10)


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
    registry = tmp_path / codec.REGISTRY
    registry.parent.mkdir(parents=True)
    registry.write_bytes(codec.encode(_registry()))
    git("add", ".")
    tree = git("write-tree")
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


def _published_orphaned_closure(root, monkeypatch, *, chunked):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    subprocess.run(
        ["git", "init", str(root)], capture_output=True, check=True, timeout=10,
        env=evidence.git_evidence.clean_git_environment(), **options,
    )
    monkeypatch.setattr(evidence.git_evidence, "REPO", root)
    monkeypatch.setattr(evidence.git_evidence, "git", evidence.git_evidence.git.__wrapped__)
    monkeypatch.setattr(evidence.git_evidence, "_OBJECT_TYPES", {})
    monkeypatch.setattr(evidence.git_evidence, "_BLOB_HASHES", {})
    monkeypatch.setattr(evidence, "AFFECTED", (evidence.PREFIX + "protocol.py",))
    monkeypatch.setattr(evidence, "TESTS", ())

    def write(path, data):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    rows = [{"commit": COMMIT, "detail": "x" * (96 << 10)} for _ in range(8)]
    contract = {
        "id": "agent-bridge.http-wire",
        "declared_range": {"current": 47},
        "source_paths": [{
            "path": evidence.AFFECTED[0], "sha256": "", "semantic": True,
        }],
        "provenance": rows,
    }
    original = {"schema_version": 1, "contracts": [contract], "deferred_contracts": []}
    if chunked:
        chunk = codec.encode({
            "schema": codec.CHUNK_SCHEMA, "schema_version": 1,
            "contract": contract["id"], "record_offset": 0,
            "provenance": [{"commit": COMMIT}],
        })
        sha256 = codec.digest(chunk)
        path = f"{codec.PROVENANCE}{contract['id']}/{sha256}.json"
        write(path, chunk)
        original["schema_version"] = 2
        contract["provenance_chunks"] = [{
            "contract": contract["id"], "path": path,
            "sha256": sha256, "record_count": 1,
        }]
    write(codec.REGISTRY, codec.encode(original))
    snapshot = codec.load(root)
    write("tools/probe.py", b"VALUE = 1\n")
    paths = evidence.proof_paths(
        root, snapshot.materialized, metadata_paths=set(snapshot.inputs) - {codec.REGISTRY},
    )
    for path in paths - set(snapshot.inputs) - {"tools/probe.py"}:
        write(path, b"fixture\n")
    proof = {}
    for path in paths:
        data = (root / path).read_bytes()
        proof[path] = {
            "blob": hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False,
            ).hexdigest(),
            "sha256": evidence.digest(data),
        }
    packet = {
        "runtime_binding_policy": "every-owned-interpreter-source-and-entrypoint-v1",
        "execution_closure": proof,
        "execution_closure_sha256": evidence.digest(evidence.encode(proof)),
        "captured_from": {
            "commit": COMMIT, "source_path": evidence.AFFECTED[0],
            "source_git_blob": proof[evidence.AFFECTED[0]]["blob"],
            "source_sha256": proof[evidence.AFFECTED[0]]["sha256"],
        },
    }
    updated = copy.deepcopy(snapshot.materialized)
    updated["contracts"][0]["provenance"].extend(rows[:4])
    updated["contracts"][0]["source_paths"][0]["sha256"] = proof[
        evidence.AFFECTED[0]
    ]["sha256"]
    assert len(codec.encode(updated)) > codec.MAX_ARTIFACT_BYTES
    for path, data in codec.plan(root, snapshot, updated).items():
        write(path, data)
    published = codec.load(root)
    assert set(snapshot.inputs) < set(published.inputs)
    assert preparation._run(root, "cat-file", "-e", f"{COMMIT}^{{commit}}").returncode != 0
    return packet, published


@pytest.mark.parametrize("chunked", [False, True], ids=["inline-conversion", "new-chunks"])
def test_orphaned_closure_uses_captured_metadata_before_size_triggered_publication(
    tmp_path, monkeypatch, chunked,
):
    packet, published = _published_orphaned_closure(tmp_path, monkeypatch, chunked=chunked)
    validation.validate_closure(tmp_path, packet, published.materialized)


@pytest.mark.parametrize("fault,diagnostic", [
    ("missing-source", "inventory is unprovable"),
    ("extra-source", "inventory is unprovable"),
    ("extra-chunk", "inventory is unprovable"),
    ("chunk-prefix-gap", "inventory is unprovable"),
    ("invalid-identity", "invalid identity"),
    ("chunk-digest", "content mismatch"),
    ("chunk-blob", "blob is unprovable"),
    ("source-digest", "content mismatch"),
    ("source-blob", "blob is unprovable"),
    ("malformed-index", "invalid provenance chunk identity"),
    ("chunk-bytes", "hash mismatch"),
    ("missing-chunk", "captured chunk unavailable"),
])
def test_orphaned_closure_publication_preserves_fail_closed_inventory(
    tmp_path, monkeypatch, fault, diagnostic,
):
    packet, published = _published_orphaned_closure(tmp_path, monkeypatch, chunked=True)
    proof = packet["execution_closure"]
    chunks = set(published.inputs) - {codec.REGISTRY}
    captured_chunk = next(path for path in proof if path in chunks)
    if fault == "missing-source":
        proof.pop("tools/probe.py")
    elif fault == "extra-source":
        proof["tools/not-captured.py"] = proof["tools/probe.py"].copy()
    elif fault == "extra-chunk":
        proof[f"{codec.PROVENANCE}unknown/{'f' * 64}.json"] = proof[captured_chunk].copy()
    elif fault == "chunk-prefix-gap":
        proof.pop(captured_chunk)
        later = published.index["contracts"][0]["provenance_chunks"][1]["path"]
        data = published.inputs[later]
        proof[later] = {
            "blob": hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False,
            ).hexdigest(),
            "sha256": evidence.digest(data),
        }
    elif fault == "invalid-identity":
        proof[captured_chunk].pop("sha256")
    elif fault.endswith("-digest"):
        path = captured_chunk if fault.startswith("chunk") else "tools/probe.py"
        proof[path]["sha256"] = "f" * 64
    elif fault.endswith("-blob"):
        path = captured_chunk if fault.startswith("chunk") else "tools/probe.py"
        proof[path]["blob"] = "f" * 40
    elif fault == "malformed-index":
        published.index["contracts"][0]["provenance_chunks"][0]["path"] = "../outside.json"
        (tmp_path / codec.REGISTRY).write_bytes(codec.encode(published.index))
    elif fault in {"missing-chunk", "chunk-bytes"}:
        def read(root, path):
            if path == captured_chunk:
                if fault == "missing-chunk":
                    raise FileNotFoundError("captured chunk unavailable")
                return published.inputs[path] + b" "
            return published.inputs[path]

        monkeypatch.setattr(codec, "read_file", read)
    packet["execution_closure_sha256"] = evidence.digest(evidence.encode(proof))
    with pytest.raises((ValueError, FileNotFoundError), match=diagnostic):
        validation.validate_closure(tmp_path, packet, published.materialized)
