"""Implementation-only updates do not require re-baselining the golden corpus."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard
REPO = Path(__file__).resolve().parents[3]
PLUGIN = Path("plugins/agent-bridge")
CONTRACT = PLUGIN / "contract"
CONSUMERS = PLUGIN / "tests/test_http_targets_contract.py"


@pytest.fixture
def batch_evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    module = importlib.import_module("agent_bridge_contract_git")
    monkeypatch.setattr(module, "_OBJECT_TYPES", {})
    monkeypatch.setattr(module, "_BLOB_HASHES", {})
    monkeypatch.setattr(module, "_SOURCE_BLOBS", {})
    monkeypatch.setattr(module, "_SOURCE_CONTENTS", {})
    monkeypatch.setattr(module, "require_local_object_reads", lambda: None)
    module.blob_sha256.cache_clear()
    module.git_file_sha256.cache_clear()
    yield module
    module.blob_sha256.cache_clear()
    module.git_file_sha256.cache_clear()


def test_local_evidence_batch_preserves_blob_hashes_and_missing_objects(batch_evidence, monkeypatch):
    blobs = {"a" * 40: b"line\r\nnext\r", "b" * 40: b"\xff\r\n"}
    missing = "c" * 40
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[-2:] == ["cat-file", "--batch"]
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["timeout"] == 10 and kwargs["check"] is True
        output = b""
        for oid in kwargs["input"].decode("ascii").splitlines():
            data = blobs.get(oid)
            output += (
                (oid + " missing\n").encode() if data is None else
                f"{oid} blob {len(data)}\n".encode() + data + b"\n"
            )
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(batch_evidence.subprocess, "run", run)
    batch_evidence.prefetch_objects([*blobs, missing, *blobs])
    assert batch_evidence.blob_sha256("a" * 40) == hashlib.sha256(b"line\nnext\n").hexdigest()
    assert batch_evidence.blob_sha256("b" * 40) == hashlib.sha256(blobs["b" * 40]).hexdigest()
    assert batch_evidence.blob_sha256(missing) is None
    assert batch_evidence.ensure_commit_available(missing) is False
    assert len(calls) == 1


def test_local_evidence_batch_keeps_actual_commit_path_bindings(batch_evidence, monkeypatch):
    commit, expected, actual = (value * 40 for value in ("c", "a", "b"))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert argv[-2:] == ["cat-file", "--batch"]
        output = b""
        for query in kwargs["input"].decode().splitlines():
            if query == commit + ":missing.py":
                output += (query + " missing\n").encode()
                continue
            oid = actual if ":" in query else query
            kind, data = ("commit", b"commit") if oid == commit else ("blob", oid.encode())
            output += f"{oid} {kind} {len(data)}\n".encode() + data + b"\n"
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(batch_evidence.subprocess, "run", run)
    batch_evidence.prefetch_sources([
        (commit, path, expected) for path in [*(f"path-{i}.py" for i in range(100)), "missing.py"]
    ])
    assert batch_evidence.git_blob(commit, "path-1.py") == actual
    assert batch_evidence.git_blob(commit, "missing.py") is None
    assert batch_evidence.git_file_sha256(commit, "path-1.py") == hashlib.sha256(actual.encode()).hexdigest()
    assert len(calls) == 2


@pytest.mark.parametrize("packet", [
    b"", b"b" * 40 + b" blob 1\nx\n", b"a" * 40 + b" blob -1\n",
    b"a" * 40 + b" blob 3\nab", b"a" * 40 + b" blob 1\nx\nextra",
])
def test_local_evidence_batch_refuses_malformed_framing(batch_evidence, monkeypatch, packet):
    monkeypatch.setattr(
        batch_evidence.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, packet, b""),
    )
    with pytest.raises(RuntimeError, match="(?i)local Git object batch"):
        batch_evidence.prefetch_objects(["a" * 40])
    assert batch_evidence._OBJECT_TYPES == {}
    assert batch_evidence._BLOB_HASHES == {}


def test_local_evidence_batch_refuses_invalid_ids_before_process(batch_evidence, monkeypatch):
    monkeypatch.setattr(
        batch_evidence.subprocess, "run", lambda *a, **kw: pytest.fail("invalid id reached Git"),
    )
    with pytest.raises(ValueError, match="Invalid object id"):
        batch_evidence.prefetch_objects(["not-an-object"])


def test_capture_git_show_reads_exact_object_under_long_checkout(batch_evidence, tmp_path):
    evidence = importlib.import_module("bridge_http47_evidence")
    root = tmp_path / ("workspace-" + "x" * 40)
    environment = batch_evidence.clean_git_environment()

    def run(*arguments, data=None):
        result = subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
             "-C", str(root), *arguments],
            input=data, capture_output=True, check=True, timeout=10, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.stdout.decode().strip()

    root.mkdir()
    run("init", "--quiet")
    content = b'{"evidence":"retained"}\r\n'
    blob = run("hash-object", "-w", "--stdin", data=content)
    filename, directory = "e" * 100 + ".json", "directory-" + "d" * 48
    child = run("mktree", data=f"100644 blob {blob}\t{filename}\n".encode())
    tree = run("mktree", data=f"040000 tree {child}\t{directory}\n".encode())
    commit = run("commit-tree", tree, "-m", "capture evidence")
    expression = f"{commit}:{directory}/{filename}"
    assert len(str(root)) + len(expression) > 260
    assert evidence.git(root, "show", expression) == content
    with pytest.raises(ValueError, match="local capture Git evidence unavailable"):
        evidence.git(root, "show", commit + ":missing.json")


def test_http52_inventory_uses_captured_owner_tree_not_future_checkout(batch_evidence):
    evidence = importlib.import_module("bridge_http52_evidence")
    retained = importlib.import_module("bridge_http_retained_evidence")
    worktrees = "plugins/agent-worktrees/src/agent_worktrees/"
    codespaces = "plugins/agent-codespaces/src/agent_codespaces/"
    fixed = "plugins/agent-bridge/tests/fixture.json"
    inventory = retained.captured_inventory(
        evidence.SPEC,
        {fixed, worktrees + "future.py", codespaces + "future.py"},
        {worktrees + "captured.py": (), codespaces + "captured.py": ()},
    )
    assert inventory == {fixed, worktrees + "captured.py", codespaces + "captured.py"}
    assert retained.captured_inventory(evidence.SPEC, set(), {
        worktrees + "__pycache__/cached.py": (),
    }) == set()


def test_http52_metadata_only_owners_exclude_future_checkout_modules(batch_evidence):
    evidence = importlib.import_module("bridge_http52_evidence")
    retained = importlib.import_module("bridge_http_retained_evidence")
    future = "plugins/agent-worktrees/src/agent_worktrees/future.py"
    spec = dataclasses.replace(evidence.SPEC, extra_paths=(*evidence.SPEC.extra_paths, future))
    owners = retained.historical_owners(spec)
    assert future not in owners
    assert set(evidence.SPEC.affected) <= owners
    assert evidence.INPUT in owners and evidence.CAPTURE in owners


@pytest.mark.timeout(180)
def test_flow_update_leaves_golden_corpus_unchanged(tmp_path):
    snapshot = tmp_path / "repo"
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(snapshot / PLUGIN / "src"), str(snapshot / "tools"))),
        "GIT_NO_LAZY_FETCH": "1",
    }
    shutil.copytree(REPO / CONTRACT, snapshot / CONTRACT)
    shutil.copytree(
        REPO / PLUGIN / "src", snapshot / PLUGIN / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    import agent_bridge_contract_registry as registry_codec
    registry_snapshot = registry_codec.load(snapshot)
    registry = registry_snapshot.materialized
    relocated = {
        row["path"]: row["current_path"]
        for owner in registry["contracts"] for row in owner["source_paths"]
        if "current_path" in row
    }
    fixture_paths = {
        fixture["path"]
        for contract in registry["contracts"] for fixture in contract["fixtures"]
    }
    before = {path: (snapshot / path).read_bytes() for path in fixture_paths}
    for contract in registry["contracts"]:
        for record in contract["source_paths"]:
            current_path = record.get("current_path", record["path"])
            destination = snapshot / current_path
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / current_path, destination)
    for deferred in registry["deferred_contracts"]:
        if "reference_path" in deferred:
            destination = snapshot / deferred["reference_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / deferred["reference_path"], destination)
    for packet in (
        "http47-owned-cold-drain.json", "http48-retained-inbox.json", "http49-retained-producer.json",
        "http50-retained-recovery.json",
    ):
        source = REPO / CONTRACT / "fixtures/http/current" / packet
        if not source.exists():
            continue
        closure = json.loads(source.read_text(encoding="utf-8"))["execution_closure"]
        for relative in closure:
            current_path = relocated.get(relative, relative)
            destination = snapshot / current_path
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                source_path = REPO / current_path
                if source_path.is_file():
                    shutil.copy2(source_path, destination)
                else:
                    historical = subprocess.run(
                        ["git", "-C", str(REPO), "cat-file", "blob", closure[relative]["blob"]],
                        capture_output=True, check=True, timeout=10, env=environment,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    ).stdout
                    canonical = historical.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                    assert hashlib.sha256(canonical).hexdigest() == closure[relative]["sha256"]
                    destination.write_bytes(historical)
    for name in ("check-agent-bridge-contracts.py", "agent_bridge_contract_git.py",
                 "bridge_http45_evidence.py", "bridge_http47_evidence.py",
                 "bridge_http47_validation.py", "bridge_http48_evidence.py",
                 "bridge_http49_evidence.py", "bridge_http_retained_evidence.py",
                 "bridge_http50_evidence.py",
                 "bridge_http51_evidence.py", "bridge_http52_evidence.py",
                 "bridge_capture_metadata.py", "bridge_http47_runtime.py",
                 "bridge_http48_installation.py",
                 "agent_bridge_contract_registry.py",
                 "agent_bridge_contract_paths.py", "agent_bridge_contract_publication.py",
                 "agent_bridge_contract_families.py"):
        destination = snapshot / "tools" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "tools" / name, destination)
    for name in ("plugin.json", "packaged-assets.json", "launch-wrapper-assets.json"):
        shutil.copy2(REPO / PLUGIN / name, snapshot / PLUGIN / name)
    (snapshot / CONSUMERS).parent.mkdir(parents=True, exist_ok=True)
    for path in (
        CONSUMERS, PLUGIN / "tests/conftest.py", PLUGIN / "tests/http47_binding_validation.py",
    ):
        shutil.copy2(REPO / path, snapshot / path)

    def run(arguments):
        result = subprocess.run(
            arguments, cwd=snapshot, env=environment, capture_output=True,
            text=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    run(["git", "init", "-q"])
    objects = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-path", "objects"],
        capture_output=True, text=True, check=True, timeout=15,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    # Borrow immutable capture history read-only; this fixture still owns its Git writes.
    (snapshot / ".git/objects/info/alternates").write_bytes(
        (Path(objects).as_posix() + "\n").encode("utf-8"),
    )
    assert run(["git", "cat-file", "-t", closure[
        "libs/agent-procutil/src/agent_procutil/_caller_owned.py"
    ]["blob"]]) == "blob"
    run(["git", "config", "user.name", "Fixture Test"])
    run(["git", "config", "user.email", "fixture@example.com"])
    run(["git", "config", "core.autocrlf", "false"])
    run(["git", "config", "core.longpaths", "true"])
    run(["git", "add", "-A"])
    run(["git", "commit", "-qm", "contract baseline"])
    baseline = run(["git", "rev-parse", "HEAD"])
    wire = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    record = next(
        item for item in wire["source_paths"]
        if item["path"] == (PLUGIN / "src/agent_bridge/session_start_cli.py").as_posix()
    )
    source = snapshot / record["path"]
    source.write_bytes(source.read_bytes() + b"\n# Implementation-only flow maintenance.\n")
    canonical = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    record.update(
        sha256=hashlib.sha256(canonical).hexdigest(), semantic=False,
        non_semantic_reason="Comment-only flow maintenance; wire behavior and floors are unchanged.",
    )
    for relative, data in registry_codec.plan(snapshot, registry_snapshot, registry).items():
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    run(["git", "add", "-A"])
    run(["git", "commit", "-qm", "implementation-only update"])
    assert set(run(["git", "diff", "--name-only", baseline, "HEAD"]).splitlines()) == {
        record["path"], (CONTRACT / "registry.json").as_posix(),
    }
    run([sys.executable, "tools/check-agent-bridge-contracts.py", "--base", baseline])
    run([
        sys.executable, "-m", "pytest", str(snapshot / CONSUMERS), "-q", "--timeout=30",
        "--rootdir", str(snapshot), "--confcutdir", str(snapshot),
        "-o", "markers=guard: contract guard", "--basetemp", str(tmp_path / "consumer-tests"),
    ])
    assert {path: (snapshot / path).read_bytes() for path in fixture_paths} == before
