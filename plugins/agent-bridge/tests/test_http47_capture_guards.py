"""Real temporary Git commits exercise capture guards; never commit this checkout."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import bridge_http47_evidence as evidence
import capture_bridge_http47 as capture

ROOT = Path(__file__).resolve().parents[3]
SEED_STATES = pytest.mark.parametrize(
    "repository", ["initial", "registered-refresh"], indirect=True
)


def read_registry(root: Path) -> dict:
    return evidence.registry_codec.load(root).materialized


def write_registry(root: Path, registry: dict) -> None:
    snapshot = evidence.registry_codec.load(root)
    for relative, data in evidence.registry_codec.plan(root, snapshot, registry).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def fixture_git(root: Path, *args: str) -> str:
    return evidence.git(root, *args).decode("utf-8").strip()


def test_http47_registry_encoding_is_lossless_and_bounded() -> None:
    registry = evidence.registry_codec.load(ROOT).index
    compact = evidence.encode(registry, indent=None)
    assert json.loads(compact) == registry
    assert len(compact) <= 1024 * 1024
    assert len(compact) < len(evidence.encode(registry, indent=1))


@pytest.fixture(scope="module")
def source_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    seed = tmp_path_factory.mktemp("http47-registered-seed")
    metadata = evidence.registry_codec.load(ROOT)
    registry = metadata.materialized
    paths = evidence.proof_paths(ROOT, registry) | {
        evidence.REGISTRY,
        evidence.CONTRACT + "registry.schema.json",
    }
    paths.update(f["path"] for c in registry["contracts"] for f in c["fixtures"])
    paths.update(
        item["reference_path"]
        for item in registry["deferred_contracts"]
        if "reference_path" in item
    )
    paths.update(metadata.inputs)
    for path in paths:
        destination = seed / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    http = next(c for c in registry["contracts"] if c["id"] == "agent-bridge.http-wire")
    if http["declared_range"]["current"] > 47:
        # This is a composed, disposable HTTP-47 test cohort, not a rewrite of
        # the current receiver or a claim to reproduce a historical commit.
        protocol = seed / evidence.PREFIX / "protocol.py"
        text = protocol.read_text(encoding="utf-8")
        text = text.replace("RETAINED_INBOX_PROTOCOL_VERSION = 48\n", "")
        text = text.replace("HTTP_PROTOCOL_VERSION = 48\n", "HTTP_PROTOCOL_VERSION = 47\n")
        protocol.write_text(text, encoding="utf-8")
        http["declared_range"].update(current=47, previous_generation=46)
        http["capability_versions"].pop("retained_inbox", None)
        for row in http["source_paths"]:
            if row["path"] == evidence.PREFIX + "protocol.py":
                row["sha256"] = evidence.digest(protocol.read_bytes())
            elif row["path"] not in evidence.AFFECTED and row["semantic"]:
                covered = any(
                    f["generation"] < 48
                    and (captured := json.loads((seed / f["path"]).read_bytes()).get(
                        "captured_from", {},
                    )).get("source_path") == row["path"]
                    and captured.get("source_sha256") == row["sha256"]
                    for f in http["fixtures"]
                )
                if not covered:
                    row.update(semantic=False, non_semantic_reason=(
                        "Disposable HTTP-47 test cohort excludes the later adapter's semantics."
                    ))
        write_registry(seed, registry)
    evidence.check_history(seed, registry)
    fixture_git(seed, "init", "-q")
    commit = commit_fixture(seed, "Synthetic capture source seed")
    proof = evidence.source_snapshot(seed, commit, registry)
    expected = capture.expected_input(seed)
    # This fixture models registered ownership, not a real probe execution.
    outputs = evidence.build_outputs(seed, commit, registry, proof, expected["expected"])
    for path, data in outputs.items():
        target = seed / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return seed


def commit_fixture(root: Path, message: str) -> str:
    fixture_git(root, "add", ".")
    fixture_git(
        root,
        "-c",
        "user.name=CaptureFixture",
        "-c",
        "user.email=capture@example.invalid",
        "commit",
        "-qm",
        message,
    )
    commit = fixture_git(root, "rev-parse", "HEAD")
    assert len(commit) == 40 and fixture_git(root, "cat-file", "-t", commit) == "commit"
    return commit


@pytest.fixture
def repository(
    request: pytest.FixtureRequest, tmp_path: Path, source_seed: Path
) -> tuple[Path, str]:
    root = tmp_path / "repository"
    state = getattr(request, "param", "registered-refresh")
    assert state in {"initial", "registered-refresh"}
    registry = read_registry(source_seed)
    owned = evidence.output_paths(registry) - {evidence.REGISTRY}

    def omit_owned(directory: str, names: list[str]) -> list[str]:
        return [
            name for name in names
            if (Path(directory) / name).relative_to(source_seed).as_posix() in owned
        ]

    shutil.copytree(source_seed, root, ignore=omit_owned if state == "initial" else None)
    if state == "initial":
        # Preserve every unplanned/historical packet and foreign contract.
        for contract in registry["contracts"]:
            if contract["id"] == "agent-bridge.http-wire":
                contract["fixtures"] = [
                    item for item in contract["fixtures"]
                    if item["path"] not in owned or item["generation"] != 47
                ]
        write_registry(root, registry)
    assert (root / evidence.PRIMARY).exists() == (state == "registered-refresh")
    evidence.check_history(root, registry)
    fixture_git(root, "init", "-q")
    commit = commit_fixture(root, "Synthetic capture source")
    return root, commit


def output_bytes(root: Path, paths: set[str] | None = None) -> dict[str, bytes | None]:
    if paths is None:
        paths = evidence.output_paths(read_registry(root))
    return {
        path: (root / path).read_bytes() if (root / path).is_file() else None
        for path in sorted(paths)
    }


def test_http47_generated_registry_is_lossless_and_bounded(
    repository: tuple[Path, str],
) -> None:
    root, commit = repository
    original = evidence.baseline(root, commit)
    proof = evidence.source_snapshot(root, commit, original)
    outputs = evidence.build_outputs(
        root, commit, original, proof, capture.expected_input(root)["expected"],
    )
    data = outputs[evidence.REGISTRY]
    assert len(data) <= 1024 * 1024
    generated = evidence.registry_codec.load(
        root, read=lambda path: outputs[path] if path in outputs
        else evidence.registry_codec.read_file(root, path),
    ).materialized
    for before, after in zip(original["contracts"], generated["contracts"], strict=True):
        assert after["provenance"][:len(before["provenance"])] == before["provenance"]
    assert generated["deferred_contracts"] == original["deferred_contracts"]


@pytest.mark.parametrize("fault", ["none", "dirty", "unregistered", "foreign"])
def test_http47_capture_semantic_upgrade_requires_registered_same_source(
    repository: tuple[Path, str], fault: str,
) -> None:
    root, commit = repository
    source = evidence.PREFIX + "routes/sessions.py"
    path = evidence.source_fixture_path(source)
    registry = evidence.baseline(root, commit)
    packet = json.loads((root / path).read_bytes())
    packet["evidence_family"] = "http47-unchanged-source-link-v1"
    if fault == "foreign":
        packet["captured_from"]["source_path"] = evidence.PREFIX + "app.py"
    data = evidence.encode(packet)
    (root / path).write_bytes(data)
    http = next(c for c in registry["contracts"] if c["id"] == "agent-bridge.http-wire")
    record = next(item for item in http["fixtures"] if item["path"] == path)
    record["sha256"] = evidence.digest(data)
    write_registry(root, registry)
    commit = commit_fixture(root, "Synthetic prior current-family ownership")
    proof = evidence.source_snapshot(root, commit, registry)
    outputs = evidence.build_outputs(
        root, commit, registry, proof, capture.expected_input(root)["expected"],
    )
    if fault == "dirty":
        (root / path).write_bytes(data + b"\n")
    elif fault == "unregistered":
        http["fixtures"].remove(record)
    if fault == "none":
        evidence.check_history(root, registry, outputs)
    else:
        with pytest.raises(ValueError, match="capture output"):
            evidence.check_history(root, registry, outputs)


def checker_environment() -> dict[str, str]:
    environment = evidence.git_evidence.clean_git_environment()
    # Static checker controls do not contribute native-operation observations.
    # Keep their subprocesses outside any enclosing portfolio capture context.
    environment.pop(capture.REQUEST_ENV, None)
    environment.pop(capture.REQUEST_SHA_ENV, None)
    return environment


def public_hashes(root: Path) -> dict[str, str]:
    paths = [root / evidence.REGISTRY, *(root / evidence.CONTRACT / "fixtures").rglob("*.json")]
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if not path.is_symlink()
    }


def stub_transport(monkeypatch, callback=None):
    calls = []

    def runner(root, proof, expected):
        calls.append((root, proof))
        if callback is not None:
            return callback(root, proof, expected)
        return copy.deepcopy(expected["expected"])

    # Only the external runner transport is stubbed in guard/persistence tests.
    # Real ASGI/native observations have their own contained probe test.
    monkeypatch.setattr(capture, "run_probes", runner)
    return calls


@SEED_STATES
def test_http47_capture_reproducible_real_commit_preserves_history(repository, monkeypatch):
    root, commit = repository
    initial_commit = commit
    before = public_hashes(root)
    calls = stub_transport(monkeypatch)
    outputs = capture.capture(root, commit, write=True)
    registry = json.loads(outputs[evidence.REGISTRY])
    assert outputs[evidence.REGISTRY] == evidence.encode(registry, indent=None)
    after = public_hashes(root)
    for path, sha in before.items():
        if path not in outputs:
            assert after[path] == sha
    bytes_before = output_bytes(root)
    times_before = {path: (root / path).stat().st_mtime_ns for path in outputs}
    assert capture.capture(root, commit, write=False) == outputs
    assert output_bytes(root) == bytes_before
    assert times_before == {path: (root / path).stat().st_mtime_ns for path in outputs}
    assert len(calls) == 2
    for path in outputs:
        if path == evidence.REGISTRY or path.startswith(evidence.registry_codec.PROVENANCE):
            continue
        packet = json.loads(outputs[path])
        proof = packet["captured_from"]
        assert proof["commit"] == commit and proof["protocol_generation"] == 47
        assert (
            fixture_git(root, "rev-parse", commit + ":" + proof["source_path"])
            == proof["source_git_blob"]
        )
    updated = read_registry(root)
    old = evidence.baseline(root, initial_commit)
    current = next(c for c in updated["contracts"] if c["id"] == "agent-bridge.http-wire")
    previous = next(c for c in old["contracts"] if c["id"] == "agent-bridge.http-wire")
    assert current["capability_versions"] == previous["capability_versions"]
    assert current["declared_range"] == previous["declared_range"]
    assert all(
        item in current["fixtures"]
        for item in previous["fixtures"] if item["path"] not in outputs
    )
    assert all(item in current["provenance"] for item in previous["provenance"])
    assert updated["contracts"][1:] == old["contracts"][1:]
    checked = subprocess.run(
        [sys.executable, str(root / "tools/check-agent-bridge-contracts.py")],
        capture_output=True,
        text=True,
        timeout=90,
        env=checker_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize(
    "path",
    [
        evidence.PREFIX + "protocol.py",
        evidence.INPUT,
        "tools/capture_bridge_http47.py",
    ],
)
def test_http47_capture_dirty_source_and_input_refuse_before_probes(repository, monkeypatch, path):
    root, commit = repository
    node = root / path
    node.write_bytes(node.read_bytes() + b"\n")
    before = public_hashes(root)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="dirty/unproven"):
        capture.capture(root, commit, write=True)
    assert calls == [] and public_hashes(root) == before


@pytest.mark.parametrize(
    "constant", ["HTTP_PROTOCOL_VERSION", "NEGOTIATED_DRAIN_PROTOCOL_VERSION"]
)
def test_http47_capture_wrong_generation_genuine_commit_refuses(repository, monkeypatch, constant):
    root, _commit = repository
    protocol = root / evidence.PREFIX / "protocol.py"
    protocol.write_text(protocol.read_text().replace(constant + " = 47", constant + " = 45"))
    fixture_git(root, "add", ".")
    fixture_git(
        root,
        "-c",
        "user.name=CaptureFixture",
        "-c",
        "user.email=capture@example.invalid",
        "commit",
        "-qm",
        "Synthetic generation 45",
    )
    commit = fixture_git(root, "rev-parse", "HEAD")
    before = public_hashes(root)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="generation 47|capability floor"):
        capture.capture(root, commit, write=True)
    assert calls == [] and public_hashes(root) == before


@pytest.mark.parametrize("mutation", ["source", "input", "report", "boolean-type", "history"])
@SEED_STATES
def test_http47_capture_post_probe_substitution_cannot_write(repository, monkeypatch, mutation):
    root, commit = repository
    before = public_hashes(root)
    outputs_before = output_bytes(root)
    owned = set(outputs_before)
    history_path = None

    def altered(root, _proof, expected):
        nonlocal history_path
        observed = copy.deepcopy(expected["expected"])
        if mutation in {"source", "input"}:
            path = root / (
                evidence.PREFIX + "client.py" if mutation == "source" else evidence.INPUT
            )
            path.write_bytes(path.read_bytes() + b"\n")
        elif mutation == "history":
            original = read_registry(root)
            history_path = next(
                f["path"] for f in original["contracts"][0]["fixtures"]
                if f["path"] not in owned
            )
            packet = root / history_path
            packet.write_bytes(packet.read_bytes() + b"\n")
        elif mutation == "report":
            observed["original_exit"]["child"] = False
        else:
            observed["cold"]["clean"] = 0
        return observed

    stub_transport(monkeypatch, altered)
    with pytest.raises(ValueError):
        capture.capture(root, commit, write=True)
    after = public_hashes(root)
    assert output_bytes(root) == outputs_before
    assert {p: sha for p, sha in after.items() if p != history_path} == {
        p: sha for p, sha in before.items() if p != history_path
    }


@pytest.mark.parametrize("target", [evidence.PRIMARY, evidence.REGISTRY])
@SEED_STATES
def test_http47_capture_hardlink_output_refuses_before_write(repository, monkeypatch, target):
    root, commit = repository
    paths = evidence.output_paths(read_registry(root))
    node = root / target
    if target == evidence.PRIMARY:
        node.unlink(missing_ok=True)  # Only this isolated fixture's owned output.
        node.hardlink_to(root / evidence.REGISTRY)
    else:
        (root / "registry-alias.json").hardlink_to(node)
    before = public_hashes(root)
    outputs_before = output_bytes(root, paths)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="aliased"):
        capture.capture(root, commit, write=True)
    assert calls == [] and public_hashes(root) == before
    assert output_bytes(root, paths) == outputs_before
    assert node.stat().st_nlink == 2


@pytest.mark.skipif(
    os.name == "nt", reason="no Windows symlink privilege ceremony in capture tests"
)
@SEED_STATES
def test_http47_capture_symlink_output_refuses_without_external_write(
    repository, monkeypatch, tmp_path
):
    root, commit = repository
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"untouched")
    node = root / evidence.PRIMARY
    node.unlink(missing_ok=True)  # Only this isolated fixture's owned output.
    node.symlink_to(outside)
    outputs_before = output_bytes(root)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="linked"):
        capture.capture(root, commit, write=True)
    assert calls == [] and outside.read_bytes() == b"untouched"
    assert node.is_symlink() and output_bytes(root) == outputs_before


@pytest.mark.parametrize("kind", ["unowned", "registry-boolean", "foreign-owner"])
@SEED_STATES
def test_http47_capture_unowned_or_dirty_output_is_not_overwritten(repository, monkeypatch, kind):
    root, commit = repository
    if kind == "unowned":
        (root / evidence.PRIMARY).write_text('{"unowned":true}')
    elif kind == "registry-boolean":
        registry = read_registry(root)
        registry["contracts"][0]["declared_range"]["minimum"] = True
        write_registry(root, registry)
    else:
        (root / evidence.PRIMARY).write_text(
            json.dumps(
                {
                    "evidence_family": evidence.FAMILY,
                    "captured_from": {
                        "protocol_generation": 47,
                        "source_path": evidence.PREFIX + "models.py",
                    },
                }
            )
        )
    if kind != "registry-boolean":
        registry = read_registry(root)
        registered = next(
            (f for c in registry["contracts"] for f in c["fixtures"]
             if f["path"] == evidence.PRIMARY), None
        )
        if registered is not None:
            registered["sha256"] = evidence.digest((root / evidence.PRIMARY).read_bytes())
            write_registry(root, registry)
            commit = commit_fixture(root, "Commit a coherent negative ownership baseline")
        evidence.check_history(root, evidence.baseline(root, commit))
    before = public_hashes(root)
    outputs_before = output_bytes(root)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="unowned|dirty capture registry"):
        capture.capture(root, commit, write=True)
    assert calls == [] and public_hashes(root) == before
    assert output_bytes(root) == outputs_before


@SEED_STATES
def test_http47_capture_no_write_drift_never_materializes_or_rewrites_outputs(
    repository, monkeypatch
):
    root, commit = repository
    before = public_hashes(root)
    outputs_before = output_bytes(root)
    stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="capture output drift"):
        capture.capture(root, commit, write=False)
    assert public_hashes(root) == before and output_bytes(root) == outputs_before


@pytest.mark.parametrize("owner", ["other-contract", "deferred", "http-historical"])
@SEED_STATES
def test_http47_capture_immutable_reference_collision_precedes_probes(
    repository, monkeypatch, owner, source_seed
):
    root, _commit = repository
    primary = root / evidence.PRIMARY
    if not primary.exists():
        shutil.copyfile(source_seed / evidence.PRIMARY, primary)
    registry = read_registry(root)
    sha = evidence.git_evidence.sha256_bytes(primary.read_bytes())
    error = "collision.*immutable reference"
    if owner == "deferred":
        reference = next(
            item for item in registry["deferred_contracts"] if "reference_path" in item
        )
        reference.update(reference_path=evidence.PRIMARY, reference_sha256=sha)
    else:
        contract = next(
            c for c in registry["contracts"]
            if (c["id"] != "agent-bridge.http-wire") == (owner == "other-contract")
        )
        if owner == "http-historical" and not any(
            f["path"] == evidence.PRIMARY for f in contract["fixtures"]
        ):
            error = "capture output would replace historical evidence"
        record = copy.deepcopy(contract["fixtures"][0])
        record.update(
            path=evidence.PRIMARY, sha256=sha,
            generation=45 if owner == "http-historical" else record["generation"],
        )
        contract["fixtures"].append(record)
    write_registry(root, registry)
    commit = commit_fixture(root, "Commit a coherent conflicting evidence reference")
    original = evidence.baseline(root, commit)
    evidence.check_history(root, original)
    before = public_hashes(root)
    outputs_before = output_bytes(root)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match=error):
        capture.capture(root, commit, write=True)
    assert calls == [] and public_hashes(root) == before
    assert output_bytes(root) == outputs_before


@SEED_STATES
def test_http47_capture_unplanned_prior_generation_packet_remains_immutable(
    repository, monkeypatch
):
    root, commit = repository
    path = evidence.CURRENT + "startup-observation-generation-46.json"
    registry = read_registry(root)
    assert path not in evidence.output_paths(registry)
    assert any(
        f["path"] == path and f["generation"] == 46
        for c in registry["contracts"] for f in c["fixtures"]
    )
    node = root / path
    node.write_bytes(node.read_bytes() + b"\n")
    before = public_hashes(root)
    outputs_before = output_bytes(root)
    calls = stub_transport(monkeypatch)
    with pytest.raises(ValueError, match="baseline fixture drift"):
        capture.capture(root, commit, write=True)
    assert calls == [] and public_hashes(root) == before
    assert output_bytes(root) == outputs_before


@SEED_STATES
def test_http47_capture_history_accepts_only_the_committed_closed_plan(repository):
    root, commit = repository
    original = evidence.baseline(root, commit)
    proof = evidence.source_snapshot(root, commit, original)
    expected = capture.expected_input(root)
    outputs = evidence.build_outputs(root, commit, original, proof, expected["expected"])
    unplanned = evidence.CURRENT + "startup-observation-generation-46.json"
    assert unplanned not in outputs
    outputs[unplanned] = (root / unplanned).read_bytes()
    before = public_hashes(root)
    with pytest.raises(ValueError, match="plan differs from committed ownership"):
        evidence.check_history(root, original, outputs)
    assert public_hashes(root) == before


@pytest.mark.parametrize("mutation", ["dirty-packet", "exact-planned-packet", "exact-planned-registry"])
@SEED_STATES
def test_http47_capture_owned_output_probe_mutation_is_never_accepted(
    repository, monkeypatch, mutation
):
    root, commit = repository
    before = output_bytes(root)
    expected_after = dict(before)

    def mutate(root: Path, proof: dict, expected: dict) -> dict:
        original = evidence.baseline(root, commit)
        observed = json.loads(capture.canonical(expected["expected"]))
        planned = evidence.build_outputs(root, commit, original, proof, observed)
        path = evidence.REGISTRY if mutation == "exact-planned-registry" else evidence.PRIMARY
        data = planned[path]
        if mutation == "dirty-packet":
            packet = json.loads(data)
            packet["scope"] = "Unadmitted concurrent output."
            data = evidence.encode(packet)
        (root / path).write_bytes(data)
        expected_after[path] = data
        return copy.deepcopy(expected["expected"])

    calls = stub_transport(monkeypatch, mutate)
    with pytest.raises(ValueError, match="dirty capture output|evidence changed during capture"):
        capture.capture(root, commit, write=True)
    assert len(calls) == 1 and output_bytes(root) == expected_after


@pytest.mark.parametrize("status", ["skipped", "failure", "missing", "duplicate"])
def test_http47_capture_requires_complete_unskipped_probe_portfolio(tmp_path, status):
    from xml.etree.ElementTree import Element, SubElement, ElementTree

    suite = Element("testsuite")
    for name, count in evidence.PROBES.items():
        for index in range(count):
            case = SubElement(suite, "testcase", name=f"{name}[{index}]")
            if status in {"skipped", "failure"}:
                SubElement(case, status)
    if status == "missing":
        suite.remove(suite[0])
    if status == "duplicate":
        SubElement(suite, "testcase", name=next(iter(evidence.PROBES)))
    path = tmp_path / "junit.xml"
    ElementTree(suite).write(path)
    with pytest.raises(ValueError, match="portfolio"):
        capture.verify_junit(path)


@pytest.mark.parametrize("fault", ["none", "input", "missing-report", "skipped"])
def test_http47_capture_contained_dispatch_binds_input_report_and_portfolio(
    repository,
    monkeypatch,
    fault,
):
    from agent_procutil import owned_process
    from xml.etree.ElementTree import Element, SubElement, ElementTree

    root, _commit = repository
    expected = capture.expected_input(root)
    calls = []

    def contained(argv, **limits):
        calls.append(argv)
        assert argv[:3] == [
            sys.executable,
            str(root / "tools/run-plugin-tests.py"),
            "agent-bridge",
        ]
        assert limits["timeout"] == 240
        assert argv[3:-2] == [
            "--test-file", "tests/test_http47_capture_probes.py",
            "--test-file", "tests/test_cold_drain.py",
            "--test-file", "tests/test_cold_recovery_races.py",
            "--test-file", "tests/test_cold_recovery_drain.py",
            "--test-file", "tests/test_cold_recovery_handoff_fence.py",
            "--test-file", "tests/test_cold_recovery_stop.py",
            "--test-file", "tests/test_cold_recovery_launch_cancellation.py",
        ]
        assert argv[-2] == "-k"
        assert set(argv[-1].split(" or ")) == set(evidence.PROBES)
        request = Path(os.environ[capture.REQUEST_ENV])
        raw = request.read_bytes()
        assert evidence.digest(raw) == os.environ[capture.REQUEST_SHA_ENV]
        manifest = json.loads(raw)
        assert manifest["root"] == str(root)
        assert manifest["input_sha256"] == evidence.digest((root / evidence.INPUT).read_bytes())
        report = Path(manifest["report"])
        if fault != "missing-report":
            report.write_bytes(evidence.encode(expected["expected"]))
        suite = Element("testsuite")
        for name, count in evidence.PROBES.items():
            for index in range(count):
                case = SubElement(suite, "testcase", name=f"{name}[{index}]")
                if fault == "skipped":
                    SubElement(case, "skipped")
        junit = Path(os.environ["PYTEST_ADDOPTS"].removeprefix("--junitxml="))
        ElementTree(suite).write(junit)
        if fault == "input":
            request.write_bytes(raw + b"\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(owned_process, "run_caller_owned", contained)
    import bridge_http47_runtime
    # Dispatch unit test only; real interpreter receipts are exercised by the
    # separately selected owned-process portfolio and substitution regressions.
    monkeypatch.setattr(
        bridge_http47_runtime, "verify_interpreters",
        lambda *_: {"policy": "every-owned-interpreter-source-and-entrypoint-v1"},
    )
    if fault == "none":
        assert capture.run_probes(root, {}, expected) == expected["expected"]
        assert capture.run_probes(root, {}, expected) == expected["expected"]
    else:
        with pytest.raises(ValueError):
            capture.run_probes(root, {}, expected)
    assert len(calls) == (2 if fault == "none" else 1)


@pytest.mark.parametrize("fault", ["none", "digest", "output", "input"])
def test_http47_capture_probe_request_binding_refuses_substitution(repository, monkeypatch, fault):
    import test_http47_capture_probes as probes

    root, commit = repository
    monkeypatch.setattr(probes, "ROOT", root)
    original = evidence.baseline(root, commit)
    proof = evidence.source_snapshot(root, commit, original)
    directory = root / ".test-venvs/http47-capture-request-test"
    directory.mkdir(parents=True)
    path = directory / "request.json"
    request = {
        "root": str(root),
        "source_proof": proof,
        "report": str(directory / "observed.json"),
        "input_sha256": evidence.digest((root / evidence.INPUT).read_bytes()),
        "control_sha256": evidence.digest((root / evidence.CONTROL_INPUT).read_bytes()),
    }
    if fault == "output":
        request["report"] = str(root.parent / "outside.json")
    raw = evidence.encode(request)
    path.write_bytes(raw)
    monkeypatch.setenv(capture.REQUEST_ENV, str(path))
    monkeypatch.setenv(capture.REQUEST_SHA_ENV, evidence.digest(raw))
    if fault == "digest":
        path.write_bytes(raw + b"\n")
    if fault == "input":
        (root / evidence.INPUT).write_bytes((root / evidence.INPUT).read_bytes() + b"\n")
    if fault == "none":
        assert probes.bound_request() == request
    else:
        with pytest.raises(ValueError):
            probes.bound_request()
    assert not (root.parent / "outside.json").exists()


def test_http47_capture_loaded_production_bytes_are_not_an_import_label(
    monkeypatch, tmp_path
):
    import test_http47_capture_probes as probes
    from agent_bridge import protocol

    # Check the currently imported cohort, not the deliberately composed
    # HTTP-47 publication fixture's different protocol implementation.
    registry = evidence.registry_codec.load(ROOT).materialized
    proof = {
        path: {"sha256": evidence.git_evidence.sha256_bytes((ROOT / path).read_bytes())}
        for path in evidence.proof_paths(ROOT, registry)
    }
    probes.loaded_source_proof(proof)
    wrong = tmp_path / "unproven-protocol.py"
    wrong.write_text("HTTP_PROTOCOL_VERSION = 45\n")
    monkeypatch.setattr(protocol, "__file__", str(wrong))
    with pytest.raises(ValueError, match="unproven production bytes"):
        probes.loaded_source_proof(proof)


@pytest.mark.parametrize("fault", ["missing-termination", "wrong-blob", "wrong-sha", "missing-manifest"])
def test_http47_capture_canonical_closure_rejects_self_consistent_tamper(
    repository, monkeypatch, fault
):
    root, commit = repository
    stub_transport(monkeypatch)
    outputs = capture.capture(root, commit, write=True)
    packet = json.loads(outputs[evidence.PRIMARY])
    closure = packet["execution_closure"]
    termination = evidence.PREFIX + "session_cold_drain.py"
    assert termination in closure
    source_packet = json.loads(outputs[evidence.source_fixture_path(termination)])
    assert source_packet["shared_operation_evidence"]["path"] == evidence.PRIMARY
    if fault == "missing-termination":
        del closure[termination]
    elif fault == "wrong-blob":
        closure[termination]["blob"] = closure[evidence.PREFIX + "protocol.py"]["blob"]
    elif fault == "wrong-sha":
        closure[termination]["sha256"] = "0" * 64
    else:
        del packet["execution_closure"]
    packet["execution_closure_sha256"] = evidence.digest(evidence.encode(closure))
    (root / evidence.PRIMARY).write_bytes(evidence.encode(packet))
    registry = json.loads(outputs[evidence.REGISTRY])
    primary = next(
        f for c in registry["contracts"] for f in c["fixtures"]
        if f["path"] == evidence.PRIMARY
    )
    primary["sha256"] = evidence.digest(evidence.encode(packet))
    # Keep all public fixture links consistent, so rejection is specifically
    # canonical manifest verification rather than an outer packet hash.
    for fixture in registry["contracts"][0]["fixtures"]:
        path = root / fixture["path"]
        value = json.loads(path.read_bytes())
        if "shared_operation_evidence" in value:
            value["shared_operation_evidence"]["sha256"] = primary["sha256"]
            path.write_bytes(evidence.encode(value))
            fixture["sha256"] = evidence.digest(path.read_bytes())
    (root / evidence.REGISTRY).write_bytes(evidence.registry_codec.encode(registry))
    checked = subprocess.run(
        [sys.executable, str(root / "tools/check-agent-bridge-contracts.py")],
        capture_output=True, text=True, timeout=90,
        env=checker_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert checked.returncode != 0
    assert "HTTP-47 execution closure" in checked.stdout + checked.stderr


@pytest.mark.parametrize("fault", ["none", "host-only-module", "host-entry", "worker-entry"])
def test_http47_capture_real_subprocess_source_and_entrypoint_binding(
    repository, tmp_path, fault
):
    root, commit = repository
    proof = evidence.source_snapshot(root, commit, evidence.baseline(root, commit))
    directory = root / ".test-venvs/http47-capture-child-test"
    directory.mkdir(parents=True)
    request = directory / "request.json"
    raw = evidence.encode({"root": str(root), "source_proof": proof})
    request.write_bytes(raw)
    from agent_bridge import session_host
    package = Path(session_host.__file__).parent.parent
    foreign = tmp_path / "foreign"
    shutil.copytree(package, foreign / "agent_bridge", ignore=shutil.ignore_patterns("__pycache__"))
    marker = tmp_path / "must-not-execute"
    poison = f"\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    # Execute the genuine entrypoint under its own request, distinct from the
    # enclosing portfolio's production launch receipts during a combined run.
    argv = [
        sys.executable, "-c",
        "import runpy; runpy.run_module('agent_bridge.session_host', run_name='__main__', alter_sys=True)",
        "--help",
    ]
    if fault == "host-only-module":
        (foreign / "agent_bridge/session_host/launcher.py").write_text(poison)
    elif fault == "host-entry":
        (foreign / "agent_bridge/session_host/__main__.py").write_text(poison)
    elif fault == "worker-entry":
        script = foreign / "cold_recovery_worker.py"
        script.write_text(poison)
        argv = [
            sys.executable, "-c",
            f"import runpy; runpy.run_path({str(script)!r}, run_name='__main__')",
        ]
    env = dict(os.environ)
    env.update({
        capture.REQUEST_ENV: str(request),
        capture.REQUEST_SHA_ENV: evidence.digest(raw),
        "PYTHONPATH": os.pathsep.join([
            str(root / "tools/http47_capture_bootstrap"),
            str(root / "tools"), str(foreign), env.get("PYTHONPATH", ""),
        ]),
    })
    checked = subprocess.run(
        argv, capture_output=True, text=True, timeout=30, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if fault == "none":
        assert checked.returncode == 0, checked.stdout + checked.stderr
        events = [
            json.loads(line) for p in (directory / "interpreters").glob("*.jsonl")
            for line in p.read_text().splitlines()
        ]
        assert any(e.get("role") == "host" and e["kind"] == "exec" for e in events)
        assert any(e.get("source") == evidence.PREFIX + "session_host/launcher.py" for e in events)
    else:
        assert checked.returncode != 0, checked.stdout + checked.stderr
        assert "HTTP-47" in checked.stdout + checked.stderr
    assert not marker.exists()
    assert evidence.source_snapshot(root, commit, evidence.baseline(root, commit)) == proof


@pytest.mark.parametrize("fault", ["none", "missing-child", "duplicate-launch", "refusal"])
def test_http47_capture_every_launch_requires_its_own_entrypoint_receipt(tmp_path, fault):
    from bridge_http47_runtime import verify_interpreters

    directory = tmp_path / "interpreters"
    directory.mkdir()
    observer = [
        {"kind": "start", "pid": 10, "parent": 1, "observer": True},
        {"kind": "observer-entry", "distribution": "pytest"},
        *({"kind": "launch", "role": role} for role in ("worker", "host", "peer")),
    ]
    if fault == "duplicate-launch":
        observer.append({"kind": "launch", "role": "host"})
    (directory / "10.jsonl").write_text("".join(json.dumps(e) + "\n" for e in observer))
    proof = {}
    for pid, role in enumerate(("worker", "host", "peer"), start=11):
        if fault == "missing-child" and role == "host":
            continue
        source = "fixture-" + role
        proof[source] = {}
        events = [
            {"kind": "start", "pid": pid, "parent": 10, "observer": False},
            {"kind": "exec", "source": source, "role": role},
        ]
        if fault == "refusal" and role == "worker":
            events.append({"kind": "refused", "source": source})
        (directory / f"{pid}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    if fault == "none":
        assert verify_interpreters(directory, proof)["roles"] == ["observer", "worker", "host", "peer"]
    else:
        with pytest.raises(ValueError, match="coverage mismatch|rejected unproven"):
            verify_interpreters(directory, proof)
