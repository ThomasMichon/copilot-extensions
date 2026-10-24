"""Composed Git fixtures qualify publication guards, not ASGI execution."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

import bridge_http48_evidence as evidence
import bridge_http47_validation as historical
import capture_bridge_http48 as capture
import test_http47_capture_guards as old_guards
from test_http47_capture_guards import (
    commit_fixture, fixture_git, source_seed as source_seed,
)

ROOT = Path(__file__).resolve().parents[3]
historical_repository = old_guards.repository


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    root = tmp_path / "http48-source"
    original = evidence.previous.registry_codec.load(ROOT).materialized
    registry = copy.deepcopy(original)
    http = evidence.contract(registry)
    http["source_paths"] = [row for row in http["source_paths"] if row["path"] in evidence.AFFECTED]
    assert {row["path"] for row in http["source_paths"]} == set(evidence.AFFECTED)
    http["fixtures"] = []
    http["provenance"] = http["provenance"][:1]
    http["evidence_window"]["generations"] = [48]
    registry["contracts"], registry["deferred_contracts"] = [http], []
    paths = evidence.previous.proof_paths(ROOT, registry) | set(evidence.EXTRA_PATHS)
    for path in paths:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    (root / evidence.REGISTRY).write_bytes(evidence.previous.registry_codec.encode(registry))
    fixture_git(root, "init", "-q")
    commit = commit_fixture(root, "Synthetic HTTP-48 publication-guard cohort")
    monkeypatch.setattr(evidence.previous.git_evidence, "REPO", root)
    evidence.previous.git_evidence.prefetch_sources([])
    yield root, commit
    evidence.previous.git_evidence.prefetch_sources([])


def expected(root: Path) -> dict:
    vector = json.loads((root / evidence.INPUT).read_bytes())
    return {case: vector["expected"] for case in ("legacy", "namespaced")}


def planned(root: Path, commit: str) -> tuple[dict, dict, dict]:
    snapshot = evidence.baseline(root, commit)
    proof = evidence.source_snapshot(root, commit, snapshot.materialized)
    outputs = evidence.build_outputs(root, commit, snapshot, proof, expected(root))
    loaded = {
        path: (evidence.digest(data), json.loads(data))
        for path, data in outputs.items() if path != evidence.REGISTRY
        and path.startswith(evidence.previous.CURRENT)
    }
    return snapshot.materialized, outputs, loaded


def test_http48_capture_write_and_no_write_preserve_the_source(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commit = repository
    calls = []

    def controlled_probe(source: Path, proof: dict, vector: dict) -> dict:
        calls.append((source, proof))
        return expected(root)  # Explicit publication-only seam, never an execution claim.

    monkeypatch.setattr(capture, "run_probes", controlled_probe)
    source = {p: (root / p).read_bytes() for p in evidence.AFFECTED}
    outputs = capture.capture(root, commit, write=True)
    before = {p: (root / p).read_bytes() for p in outputs}
    assert capture.capture(root, commit) == outputs
    assert len(calls) == 2
    assert {p: (root / p).read_bytes() for p in before} == before
    assert {p: (root / p).read_bytes() for p in source} == source


def test_http48_capture_refreshes_only_registered_current_outputs(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commit = repository
    monkeypatch.setattr(capture, "run_probes", lambda *args: expected(root))
    first = capture.capture(root, commit, write=True)
    old = evidence.previous.registry_codec.load(root).materialized
    path = root / "tools/capture_bridge_http48.py"
    path.write_bytes(path.read_bytes() + b"\n")
    next_commit = commit_fixture(root, "Synthetic next source checkpoint")
    second = capture.capture(root, next_commit, write=True)
    assert first[evidence.PRIMARY] != second[evidence.PRIMARY]
    assert capture.capture(root, next_commit) == second
    new = evidence.previous.registry_codec.load(root).materialized
    before, after = evidence.contract(old), evidence.contract(new)
    assert after["provenance"][:len(before["provenance"])] == before["provenance"]
    assert len(after["fixtures"]) == len(before["fixtures"])
    assert {f["path"] for f in after["fixtures"]} == {f["path"] for f in before["fixtures"]}


@pytest.mark.parametrize("fault", ["dirty-output", "foreign-owner", "dirty-registry"])
def test_http48_capture_refresh_refuses_unowned_changes(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    root, commit = repository
    monkeypatch.setattr(capture, "run_probes", lambda *args: expected(root))
    capture.capture(root, commit, write=True)
    next_commit = commit_fixture(root, "Synthetic registered HTTP-48 evidence")
    if fault == "dirty-registry":
        snapshot = evidence.previous.registry_codec.load(root)
        changed = copy.deepcopy(snapshot.materialized)
        evidence.contract(changed)["durable_records"].append("foreign concurrent edit")
        for name, data in evidence.previous.registry_codec.plan(root, snapshot, changed).items():
            (root / name).write_bytes(data)
    else:
        path = root / evidence.PRIMARY
        packet = json.loads(path.read_bytes())
        if fault == "dirty-output":
            packet["observations"]["legacy"]["poll"]["body"] = "foreign"
        else:
            packet["captured_from"]["source_path"] = "foreign.py"
        path.write_bytes(evidence.encode(packet))
    before = (root / evidence.REGISTRY).read_bytes()
    monkeypatch.setattr(capture, "run_probes", lambda *args: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="dirty|not owned"):
        capture.capture(root, next_commit, write=True)
    assert (root / evidence.REGISTRY).read_bytes() == before


@pytest.mark.parametrize("fault", [
    "missing-source", "wrong-blob", "wrong-sha", "wrong-outcome", "wrong-child", "wrong-roles",
])
def test_http48_capture_rejects_self_consistent_evidence_tampering(
    repository: tuple[Path, str], fault: str,
) -> None:
    root, commit = repository
    registry, outputs, loaded = planned(root, commit)
    packet = loaded[evidence.PRIMARY][1]
    source = evidence.previous.PREFIX + "routes/live_inbox.py"
    if fault == "missing-source":
        del packet["execution_closure"][source]
    elif fault == "wrong-blob":
        packet["execution_closure"][source]["blob"] = "0" * 40
    elif fault == "wrong-sha":
        packet["execution_closure"][source]["sha256"] = "0" * 64
    elif fault == "wrong-outcome":
        packet["observations"]["legacy"]["begin"] = [{"begin": True}, {"begin": True}]
    elif fault == "wrong-child":
        loaded[evidence.source_fixture_path(source)][1]["captured_from"]["source_path"] = (
            evidence.previous.PREFIX + "protocol.py"
        )
    else:
        packet["runtime_roles"].append("native-sdk")
    packet["execution_closure_sha256"] = evidence.digest(
        evidence.encode(packet["execution_closure"]),
    )
    # Keep the outer hash and every shared link consistent: require the
    # canonical source/operation validator, not an incidental hash mismatch.
    sha = evidence.digest(evidence.encode(packet))
    loaded[evidence.PRIMARY] = (sha, packet)
    for _, value in loaded.values():
        if "shared_operation_evidence" in value:
            value["shared_operation_evidence"]["sha256"] = sha
    errors: list[str] = []
    evidence.validate_http48(root, registry, loaded, errors, require_tree=True)
    assert errors and "HTTP-48 execution closure" in errors[0]
    assert not (root / evidence.PRIMARY).exists()


def test_http48_capture_unexpected_observation_writes_nothing(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commit = repository
    before = (root / evidence.REGISTRY).read_bytes()
    wrong = expected(root)
    wrong["namespaced"]["poll"]["body"] = "substituted"
    monkeypatch.setattr(capture, "run_probes", lambda *args: wrong)
    with pytest.raises(ValueError, match="plan changed"):
        capture.capture(root, commit, write=True)
    assert (root / evidence.REGISTRY).read_bytes() == before
    assert not (root / evidence.PRIMARY).exists()


def test_http48_capture_dirty_source_refuses_before_probes(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commit = repository
    source = root / evidence.AFFECTED[1]
    source.write_bytes(source.read_bytes() + b"\n")
    monkeypatch.setattr(capture, "run_probes", lambda *args: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="dirty/unproven"):
        capture.capture(root, commit, write=True)
    assert not (root / evidence.PRIMARY).exists()


def test_http48_observer_scope_does_not_relax_the_http47_child_portfolio(tmp_path: Path) -> None:
    from bridge_http47_runtime import verify_interpreters

    events = [
        {"kind": "start", "pid": 1, "parent": 0, "observer": True},
        {"kind": "observer-entry", "distribution": "pytest", "source": "pytest/__main__.py"},
    ]
    (tmp_path / "1.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
    )
    assert verify_interpreters(tmp_path, {}, expected_roles=frozenset())["roles"] == ["observer"]
    with pytest.raises(ValueError, match="coverage mismatch"):
        verify_interpreters(tmp_path, {})


def test_http47_historical_current_hashes_are_not_future_source_inputs(
    historical_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from capture_bridge_http47 import expected_input

    root, commit = historical_repository
    monkeypatch.setattr(evidence.previous.git_evidence, "REPO", root)
    evidence.previous.git_evidence.prefetch_sources([])
    metadata = evidence.previous.baseline(root, commit)
    proof = evidence.previous.source_snapshot(root, commit, metadata)
    packet = json.loads(evidence.previous.build_outputs(
        root, commit, metadata, proof, expected_input(root)["expected"],
    )[evidence.previous.PRIMARY])
    http = evidence.contract(metadata)
    http["declared_range"]["current"] = 48
    row = next(r for r in http["source_paths"] if r["path"] == evidence.previous.PREFIX + "protocol.py")
    row["sha256"] = "f" * 64
    assert row["sha256"] != packet["execution_closure"][row["path"]]["sha256"]
    (root / "tools/future_runtime.py").write_text("# Not a historical input.\n", encoding="utf-8")
    historical.validate_closure(root, packet, metadata)
    original_git = evidence.previous.git_evidence.git

    def unavailable_tree(*args: str):
        if args[:2] == ("ls-tree", "-r"):
            return SimpleNamespace(returncode=1)
        return original_git(*args)

    monkeypatch.setattr(evidence.previous.git_evidence, "git", unavailable_tree)
    historical.validate_closure(root, packet, metadata)
    broken = copy.deepcopy(packet)
    del broken["execution_closure"][evidence.previous.AFFECTED[0]]
    broken["execution_closure_sha256"] = evidence.digest(evidence.encode(broken["execution_closure"]))
    with pytest.raises(ValueError, match="inventory is unprovable"):
        historical.validate_closure(root, broken, metadata)
    evidence.previous.git_evidence.prefetch_sources([])
