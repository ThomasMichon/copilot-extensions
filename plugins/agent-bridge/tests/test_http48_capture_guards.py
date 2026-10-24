"""Composed Git fixtures qualify publication guards, not ASGI execution."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

import pytest

import bridge_http48_evidence as evidence
import bridge_http49_evidence as producer_evidence
import bridge_http50_evidence as recovery_evidence
import bridge_http47_validation as historical
import capture_bridge_http48 as capture
import capture_bridge_http49 as producer_capture
import capture_bridge_http50 as recovery_capture
import test_http47_capture_guards as old_guards
from test_http47_capture_guards import (
    commit_fixture, fixture_git, source_seed as source_seed,
)

ROOT = Path(__file__).resolve().parents[3]
historical_repository = old_guards.repository


@pytest.fixture(params=["http48", "http49", "http50"])
def capture_case(request):
    return {
        "http48": (evidence, capture), "http49": (producer_evidence, producer_capture),
        "http50": (recovery_evidence, recovery_capture),
    }[request.param]


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture_case) -> tuple[Path, str]:
    evidence, _ = capture_case
    root = tmp_path / f"http{evidence.GENERATION}-source"
    original = evidence.previous.registry_codec.load(ROOT).materialized
    registry = copy.deepcopy(original)
    http = evidence.contract(registry)
    http["source_paths"] = [row for row in http["source_paths"] if row["path"] in evidence.AFFECTED]
    assert {row["path"] for row in http["source_paths"]} == set(evidence.AFFECTED)
    http["fixtures"] = []
    http["provenance"] = http["provenance"][:1]
    http["evidence_window"]["generations"] = [evidence.GENERATION]
    http["declared_range"].update(
        current=evidence.GENERATION, previous_generation=evidence.GENERATION - 1,
    )
    registry["contracts"], registry["deferred_contracts"] = [http], []
    paths = (evidence.previous.proof_paths(ROOT, registry) | set(evidence.EXTRA_PATHS)
             | set(evidence.installation.paths(ROOT)))
    for path in paths:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    # Composed publication-only cohorts, never an original historical receiver.
    protocol = root / evidence.previous.PREFIX / "protocol.py"
    text = protocol.read_text(encoding="utf-8")
    text = re.sub(
        r"(?m)^HTTP_PROTOCOL_VERSION = \d+$",
        f"HTTP_PROTOCOL_VERSION = {evidence.GENERATION}", text,
    )
    if evidence.GENERATION == 48:
        text = text.replace("RETAINED_INBOX_PRODUCER_PROTOCOL_VERSION = 49\n", "")
        http["capability_versions"].pop("retained_inbox_producer", None)
    if evidence.GENERATION < 50:
        text = text.replace("RETAINED_INBOX_RECOVERY_PROTOCOL_VERSION = 50\n", "")
        http["capability_versions"].pop("retained_inbox_recovery", None)
    protocol.write_text(text, encoding="utf-8", newline="\n")
    next(row for row in http["source_paths"]
         if row["path"] == evidence.previous.PREFIX + "protocol.py")["sha256"] = evidence.digest(
             protocol.read_bytes(),
         )
    prior_path = evidence.CONTRACT + "fixtures/publication-only/previous-generation.json"
    prior_data = evidence.encode({
        "scope": "Synthetic historical publication guard; not runtime evidence.",
    })
    prior = root / prior_path
    prior.parent.mkdir(parents=True, exist_ok=True)
    prior.write_bytes(prior_data)
    http["fixtures"].append({
        "path": prior_path, "sha256": evidence.digest(prior_data),
        "role": "Synthetic historical publication guard", "generation": evidence.GENERATION - 1,
    })
    (root / evidence.REGISTRY).write_bytes(evidence.previous.registry_codec.encode(registry))
    fixture_git(root, "init", "-q")
    commit = commit_fixture(root, f"Synthetic HTTP-{evidence.GENERATION} publication-guard cohort")
    monkeypatch.setattr(evidence.previous.git_evidence, "REPO", root)
    evidence.previous.git_evidence.prefetch_sources([])
    yield root, commit
    evidence.previous.git_evidence.prefetch_sources([])


def expected(root: Path, evidence) -> dict:
    vector = json.loads((root / evidence.INPUT).read_bytes())
    return {case: vector["expected"] for case in ("legacy", "namespaced")}


def planned(root: Path, commit: str, evidence) -> tuple[dict, dict, dict]:
    snapshot = evidence.baseline(root, commit)
    proof = evidence.source_snapshot(root, commit, snapshot.materialized)
    outputs = evidence.build_outputs(root, commit, snapshot, proof, expected(root, evidence))
    loaded = {
        path: (evidence.digest(data), json.loads(data))
        for path, data in outputs.items() if path != evidence.REGISTRY
        and path.startswith(evidence.previous.CURRENT)
    }
    return snapshot.materialized, outputs, loaded


def test_http48_capture_write_and_no_write_preserve_the_source(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capture_case,
) -> None:
    evidence, capture = capture_case
    root, commit = repository
    calls = []

    def controlled_probe(source: Path, proof: dict, vector: dict) -> dict:
        calls.append((source, proof))
        return expected(root, evidence)  # Publication-only seam, never an execution claim.

    monkeypatch.setattr(capture, "run_probes", controlled_probe)
    source = {p: (root / p).read_bytes() for p in evidence.AFFECTED}
    historical = {
        row["path"]: (root / row["path"]).read_bytes()
        for row in evidence.contract(
            evidence.previous.registry_codec.load(root).materialized,
        )["fixtures"]
    }
    outputs = capture.capture(root, commit, write=True)
    before = {p: (root / p).read_bytes() for p in outputs}
    assert capture.capture(root, commit) == outputs
    assert len(calls) == 2
    assert {p: (root / p).read_bytes() for p in before} == before
    assert {p: (root / p).read_bytes() for p in source} == source
    assert {p: (root / p).read_bytes() for p in historical} == historical


def test_http48_capture_refreshes_only_registered_current_outputs(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capture_case,
) -> None:
    evidence, capture = capture_case
    root, commit = repository
    monkeypatch.setattr(capture, "run_probes", lambda *args: expected(root, evidence))
    first = capture.capture(root, commit, write=True)
    old = evidence.previous.registry_codec.load(root).materialized
    path = root / evidence.CAPTURE
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


def test_http48_capture_ignores_observation_object_key_order(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capture_case,
) -> None:
    evidence, capture = capture_case
    root, commit = repository
    observed = expected(root, evidence)
    for case in observed:
        observed[case] = dict(reversed(list(observed[case].items())))
        nested = "recovery" if evidence.GENERATION == 50 else "ordinary_producer"
        observed[case][nested] = dict(reversed(list(
            observed[case][nested].items(),
        )))
    assert observed == expected(root, evidence)
    monkeypatch.setattr(capture, "run_probes", lambda *args: observed)
    outputs = capture.capture(root, commit, write=True)
    assert capture.capture(root, commit) == outputs


@pytest.mark.parametrize("fault", ["dirty-output", "foreign-owner", "dirty-registry"])
def test_http48_capture_refresh_refuses_unowned_changes(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, fault: str, capture_case,
) -> None:
    evidence, capture = capture_case
    root, commit = repository
    monkeypatch.setattr(capture, "run_probes", lambda *args: expected(root, evidence))
    capture.capture(root, commit, write=True)
    next_commit = commit_fixture(root, f"Synthetic registered HTTP-{evidence.GENERATION} evidence")
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
            packet["observations"]["legacy"]["begin"] = [{"begin": False}]
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
    "missing-installation-fragment", "missing-installation-execution",
    "wrong-generation", "wrong-child-family",
])
def test_http48_capture_rejects_self_consistent_evidence_tampering(
    repository: tuple[Path, str], fault: str, capture_case,
) -> None:
    evidence, _ = capture_case
    root, commit = repository
    registry, outputs, loaded = planned(root, commit, evidence)
    packet = loaded[evidence.PRIMARY][1]
    source = evidence.previous.PREFIX + "routes/live_inbox.py"
    if fault == "missing-source":
        del packet["execution_closure"][source]
    elif fault == "missing-installation-fragment":
        fragment = evidence.installation.INSTALLATION_HELPER_PREFIX + "_installation_context_resolution.py"
        del packet["execution_closure"][fragment]
    elif fault == "missing-installation-execution":
        packet["required_execution_sources"].pop()
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
    elif fault == "wrong-generation":
        packet["captured_from"]["protocol_generation"] = evidence.GENERATION - 1
    elif fault == "wrong-child-family":
        loaded[evidence.source_fixture_path(source)][1]["evidence_family"] = "foreign"
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
    evidence.validate(root, registry, loaded, errors, require_tree=True)
    assert errors and f"HTTP-{evidence.GENERATION} execution closure" in errors[0]
    assert not (root / evidence.PRIMARY).exists()


def test_http48_capture_unexpected_observation_writes_nothing(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capture_case,
) -> None:
    evidence, capture = capture_case
    root, commit = repository
    before = (root / evidence.REGISTRY).read_bytes()
    wrong = expected(root, evidence)
    wrong["namespaced"]["begin"] = [{"begin": False}]
    monkeypatch.setattr(capture, "run_probes", lambda *args: wrong)
    with pytest.raises(ValueError, match="plan changed"):
        capture.capture(root, commit, write=True)
    assert (root / evidence.REGISTRY).read_bytes() == before
    assert not (root / evidence.PRIMARY).exists()


@pytest.mark.parametrize("source", [
    evidence.previous.PREFIX + "routes/live_inbox.py", evidence.installation.ENTRY,
    evidence.installation.INSTALLATION_HELPER_PREFIX + "_installation_context_resolution.py",
])
def test_http48_capture_dirty_source_refuses_before_probes(
    repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, source: str, capture_case,
) -> None:
    evidence, capture = capture_case
    root, commit = repository
    target = root / source
    target.write_bytes(target.read_bytes() + b"\n")
    monkeypatch.setattr(capture, "run_probes", lambda *args: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="dirty/unproven"):
        capture.capture(root, commit, write=True)
    assert not (root / evidence.PRIMARY).exists()


@pytest.mark.parametrize("fault", [
    "none", "entry-bytes", "fragment-bytes", "forged-code", "foreign-entry", "foreign-suffix",
])
def test_http48_actual_installation_helper_execution_is_source_bound(
    repository: tuple[Path, str], fault: str, capture_case,
) -> None:
    evidence, _ = capture_case
    from agent_procutil.owned_process import run_caller_owned
    import bridge_http47_runtime as runtime

    root, commit = repository
    proof = evidence.source_snapshot(root, commit, evidence.baseline(root, commit).materialized)
    directory = root / ".test-venvs/http47-capture-installation-control"
    directory.mkdir(parents=True)
    request = directory / "request.json"
    payload = evidence.encode({
        "root": str(root), "capture_generation": evidence.GENERATION, "source_proof": proof,
    })
    request.write_bytes(payload)
    entry = root / evidence.installation.ENTRY
    marker = directory / "must-not-execute"
    poison = f"\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    refused_source = evidence.installation.ENTRY
    if fault == "entry-bytes":
        entry.write_text(entry.read_text(encoding="utf-8") + poison, encoding="utf-8")
    elif fault == "fragment-bytes":
        refused_source = (
            evidence.installation.INSTALLATION_HELPER_PREFIX + "_installation_context_resolution.py"
        )
        fragment = root / refused_source
        fragment.write_text(fragment.read_text(encoding="utf-8") + poison, encoding="utf-8")
    elif fault in ("foreign-entry", "foreign-suffix"):
        data = entry.read_bytes()
        entry = (directory / "installation_context.py" if fault == "foreign-entry"
                 else directory / "foreign" / evidence.installation.ENTRY)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(data)
    program = (
        "import importlib.util,sys\n"
        "from pathlib import Path\n"
        "path=Path(sys.argv[1])\n"
        "spec=importlib.util.spec_from_file_location('installation_probe',path)\n"
        "module=importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name]=module\n"
        "if sys.argv[2]=='forged-code':\n"
        " exec(compile(path.read_text(encoding='utf-8')+sys.argv[3],str(path),'exec'),module.__dict__)\n"
        "else:\n"
        " spec.loader.exec_module(module)\n"
    )
    environment = {
        **evidence.previous.git_evidence.clean_git_environment(),
        runtime.REQUEST: str(request), runtime.REQUEST_SHA: evidence.digest(payload),
        "PYTHONPATH": os.pathsep.join((
            str(root / "tools/http47_capture_bootstrap"), str(root / "tools"),
        )),
    }
    result = run_caller_owned(
        [sys.executable, "-c", program, str(entry), fault, poison],
        env=environment, timeout=20, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    events = [
        json.loads(line) for path in (directory / "interpreters").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert not marker.exists()
    if fault == "none":
        assert result.returncode == 0, result.stderr
        required = evidence.installation.required_sources(entry.read_bytes())
        assert required <= {event["source"] for event in events if event["kind"] == "exec"}
    else:
        assert result.returncode != 0 and "unproven" in result.stderr
        assert any(event["kind"] == "refused" and event["source"] == refused_source for event in events)


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
    with pytest.raises(ValueError, match="runtime source execution is incomplete"):
        verify_interpreters(
            tmp_path, {}, expected_roles=frozenset(),
            required_sources=frozenset({evidence.installation.ENTRY}),
        )
    with pytest.raises(ValueError, match="coverage mismatch"):
        verify_interpreters(tmp_path, {})


@pytest.mark.parametrize("future_generation", [48, 49, 50])
def test_http47_historical_current_hashes_are_not_future_source_inputs(
    historical_repository: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, future_generation,
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
    http["declared_range"]["current"] = future_generation
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
