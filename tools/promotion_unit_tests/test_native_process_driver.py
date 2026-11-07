"""Original outside-owner adapter contracts with all native actors substituted."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import native_process_qualification as driver
from tools import native_process_sources as sources
from tools import native_qualification_contract as contract
from tools import native_qualification_receiver as receiver
from tools import plugin_test_containment as owner

UTC = "2026-10-31T08:00:00+00:00"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "source"
    tools = root / "tools"
    tools.mkdir(parents=True)
    paths = [tools / name for name in sources.TOOLS]
    paths += [root / "libs/agent-procutil/tests" / name for name in sources.SDK_HELPERS]
    paths += [root / "libs/agent-procutil/src/agent_procutil/__init__.py"]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# reviewed source\n")
    manifest = {
        "schema": sources.SCHEMA, "version": 1, "nonce": "c" * 32,
        "case_packets": [16, 16, 8, 4], "files": [
            {"name": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths
        ],
    }
    monkeypatch.setattr(sources, "__file__", str(tools / "native_process_sources.py"))
    monkeypatch.setattr(sources, "sys", SimpleNamespace(modules={}))
    return root, manifest


def publish(root, manifest):
    data = json.dumps(manifest).encode()
    (root / "tools/prepared-manifest.json").write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_process_receiver_keeps_four_request_fields_and_nineteen_powershell_arguments(tree, tmp_path):
    root, manifest = tree
    digest = publish(root, manifest)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    command = [
        "powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
        str(root / "tools" / sources.LAUNCHER), "-ExecuteAcceptedScope", "-ExecutionScope", "7",
        "-ParentAcceptanceReference", "accepted", "-ExpectedPreparedManifestSha256", digest,
    ]
    result = receiver._command(
        command, 7, digest, contract.Boundary(10, UTC), lambda: 20, runtime_root=runtime,
    )
    assert len(result) == 19 and result[:13] == command
    assert result[13:] == [
        "-OriginalRuntimeRoot", str(runtime), "-OriginalStartMonotonic", "10", "-OriginalStartUtc", UTC,
    ]


@pytest.mark.parametrize("failure", [
    "missing-owner", "changed", "traversal", "duplicate", "foreign", "late", "orphan", "bytecode",
    "missing-observation", "changed-observation", "foreign-observation",
])
def test_process_preparation_refuses_unbound_source_before_launch(tree, monkeypatch, failure):
    root, manifest = tree
    if failure in {"missing-owner", "missing-observation"}:
        missing = "plugin_test_containment.py" if failure == "missing-owner" else "plugin_test_containment_observation.py"
        manifest["files"] = [row for row in manifest["files"] if not row["name"].endswith(missing)]
    elif failure == "traversal":
        manifest["files"][0]["name"] = "../outside.py"
    elif failure == "duplicate":
        manifest["files"].append(dict(manifest["files"][0]))
    elif failure in {"foreign", "foreign-observation"}:
        name = "native_process_stage" if failure == "foreign" else "plugin_test_containment_observation"
        monkeypatch.setattr(sources, "sys", SimpleNamespace(modules={
            name: SimpleNamespace(__file__="/foreign/" + name + ".py"),
        }))
    digest = publish(root, manifest)
    if failure == "changed":
        (root / "tools/native_process_stage.py").write_bytes(b"changed")
    elif failure == "changed-observation":
        (root / "tools/plugin_test_containment_observation.py").write_bytes(b"changed")
    elif failure == "orphan":
        (root / "tools/undeclared.py").write_bytes(b"# undeclared")
    elif failure == "bytecode":
        (root / "tools/__pycache__").mkdir()
    with pytest.raises(contract.QualificationError):
        sources.load(
            root / "tools" / sources.LAUNCHER, digest, contract.Boundary(10, UTC),
            lambda: 190 if failure == "late" else 20,
        )


def test_required_tool_closure_contains_actual_owner_startup_imports(tree):
    root, manifest = tree
    expected = publish(root, manifest)
    verified, _, _ = sources.load(
        root / "tools" / sources.LAUNCHER, expected, contract.Boundary(10, UTC), lambda: 20,
    )
    assert verified == root
    tools = Path(__file__).resolve().parents[1]
    names = {name for name in sources.TOOLS if name.endswith(".py")}

    def imports(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module.split(".")[0]
            else:
                yield from (alias.name for alias in node.names)
        for child in ast.iter_child_nodes(node):
            yield from imports(child)

    for name in names:
        tree = ast.parse((tools / name).read_bytes())
        local = {module + ".py" for module in imports(tree) if (tools / (module + ".py")).is_file()}
        assert local <= names, (name, sorted(local - names))


@pytest.fixture
def driver_fixture(tree, tmp_path, monkeypatch):
    root, manifest = tree
    digest = publish(root, manifest)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    request = {
        "command": [
            "powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(root / "tools" / sources.LAUNCHER), "-ExecuteAcceptedScope", "-ExecutionScope", "7",
            "-ParentAcceptanceReference", "accepted", "-ExpectedPreparedManifestSha256", digest,
        ],
        "scope": 7, "cwd": str(tmp_path), "manifest_sha256": digest,
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    events, fault = [], {"missing": ""}
    monkeypatch.setattr(driver, "__file__", str(root / "tools/native_process_qualification.py"))
    monkeypatch.setattr(driver, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=lambda: 20))
    monkeypatch.setattr(driver.sources, "sdk", lambda *a: (None, []))
    monkeypatch.setattr(driver, "require_capacity", lambda: events.append("capacity"))
    monkeypatch.setattr(owner, "isolated_environment", lambda env, sandbox: {"ISOLATED": str(sandbox)})

    def contained(argv, **kwargs):
        events.append("original-owner")
        qualification = kwargs["qualification"]
        assert qualification.mode == contract.PROCESS_MODE and qualification.boundary.start == 10
        assert kwargs["limits"] == owner.Limits(wall_seconds=165)
        assert kwargs["sandbox"] == runtime and kwargs["cwd"] == tmp_path
        assert kwargs["env"]["ISOLATED"] == str(runtime)
        assert argv[-6:] == [
            "--original-start-monotonic", "10", "--original-start-utc", UTC, "--runtime-root", str(runtime),
        ]
        sealed = json.loads((runtime / "evidence/receiver-request.json").read_bytes())
        assert sealed == request
        contract.publish_work_completion(qualification, candidate_exit=0, workers_exited=True, clock=lambda: 20)
        qualification.native_exit = 0
        for field in (
            "worker_reaped", "worker_handle_closed", "job_zero_observed", "job_handle_closed",
            "lifecycle_settled", "registry_unchanged",
        ):
            setattr(qualification, field, field != fault["missing"])
        qualification.capture.settled = qualification.capture.closed = True
        qualification.receiver = {"passed": True, "qualification": contract.PROCESS_MODE}
        return 0

    monkeypatch.setattr(owner, "run_contained", contained)
    return path, runtime, events, fault


def execute(fixture):
    path, runtime, _, _ = fixture
    return driver.execute(
        path, runtime_root=runtime, original_start_monotonic=10, original_start_utc=UTC,
    )


def test_real_driver_uses_original_owner_once_and_publishes_only_after_settlement(driver_fixture):
    result = execute(driver_fixture)
    _, runtime, events, _ = driver_fixture
    assert events == ["capacity", "original-owner"]
    assert result["passed"] and result["original_job_drained"] and result["original_job_handle_closed"]
    assert result["desktop_observed"] is False
    assert json.loads((runtime / "evidence/owner-result.json").read_bytes()) == result
    with pytest.raises(contract.QualificationError, match="empty"):
        execute(driver_fixture)
    assert events == ["capacity", "original-owner"]


@pytest.mark.parametrize("missing", [
    "job_zero_observed", "job_handle_closed", "worker_handle_closed",
    "worker_reaped", "lifecycle_settled", "registry_unchanged",
])
def test_driver_cannot_publish_success_with_missing_original_settlement(driver_fixture, missing):
    _, runtime, events, fault = driver_fixture
    fault["missing"] = missing
    with pytest.raises(contract.QualificationError, match="settlement"):
        execute(driver_fixture)
    assert events == ["capacity", "original-owner"]
    assert not (runtime / "evidence/owner-result.json").exists()
