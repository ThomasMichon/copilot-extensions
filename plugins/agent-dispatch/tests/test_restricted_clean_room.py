"""The host-driven report must not accept skipped or incomplete Docker proof."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def lane(monkeypatch):
    script = Path(__file__).resolve().parents[3] / "tools/clean-room/restricted-dispatch/run.py"
    spec = importlib.util.spec_from_file_location("restricted_clean_room", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_cleanup_docker", lambda *_args, **_kwargs: None)
    return module


def _proof(root, outcome):
    root.mkdir()
    proof = {
        "outcome": outcome, "status": "completed" if outcome == "complete" else "abandoned",
        "native": "native-1", "owner_native": "native-1", "bridge_native": "native-1",
        "prompts": 1, "rotated": True, "terminal_credential_status": 403,
        "refused": [{"status": 403} for _ in range(5)],
    }
    (root / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    (root / "stages.jsonl").write_text(
        '{"stage":"acceptance","event":"complete"}\n'
        '{"stage":"cleanup","event":"complete"}\n', encoding="utf-8",
    )
    return proof


def test_clean_room_retains_two_independent_cases_and_original_limits(lane, tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        outcome = argv[argv.index("-k") + 1]
        evidence = Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"])
        assert not evidence.exists()
        _proof(evidence, outcome)
        return 0

    monkeypatch.setattr(lane, "_run_case", run)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "pass" and report["jams"] == []
    assert [row["case"] for row in report["stages"]] == ["complete", "abandon"]
    for argv, kwargs in calls:
        assert argv[argv.index("--test-timeout") + 1] == "300"
        assert argv[argv.index("--timeout") + 1] == "360"
        assert argv[argv.index("--plugin-timeout") + 1] == "360"
        assert kwargs["timeout"] == 360
        assert "--allow-host-state" not in argv
    assert calls[0][1]["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"] != (
        calls[1][1]["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"]
    )
    assert json.loads((tmp_path / "cr-report.json").read_text())["result"] == "pass"


@pytest.mark.parametrize("failure", [
    "nonzero", "skip", "malformed", "revocation", "cleanup", "refusals", "native", "rotation",
    "timeout", "io", "runner-value",
])
def test_clean_room_fails_closed_and_never_resamples(lane, tmp_path, monkeypatch, failure):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if failure == "timeout":
            raise lane.subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "io":
            raise PermissionError("runner unavailable")
        if failure == "runner-value":
            raise ValueError("invalid runner argument")
        root = Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"])
        if failure != "skip":
            proof = _proof(root, "complete")
            if failure == "revocation":
                proof["terminal_credential_status"] = 200
            elif failure == "refusals":
                proof["refused"] = []
            elif failure == "native":
                proof["bridge_native"] = "replacement-native"
            elif failure == "rotation":
                proof["rotated"] = False
            (root / "proof.json").write_text(
                "{" if failure == "malformed" else json.dumps(proof), encoding="utf-8",
            )
            if failure == "cleanup":
                (root / "stages.jsonl").write_text(
                    '{"stage":"acceptance","event":"complete"}\n'
                    '{"stage":"cleanup","event":"start"}\n', encoding="utf-8",
                )
        return 1 if failure == "nonzero" else 0

    monkeypatch.setattr(lane, "_run_case", run)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail"
    assert len(calls) == 1 and len(report["stages"]) == 1
    assert report["jams"][0]["evidence"] == "complete/runner.log"
    expected = {
        "nonzero": "runner-exit", "timeout": "contained-timeout", "io": "runner-io",
        "runner-value": "runner-io",
    }.get(failure, "proof-invalid")
    assert report["jams"][0]["category"] == expected
    assert report["jams"][0]["error_type"] == report["stages"][0]["error_type"]
    assert report["jams"][0]["error"] == report["stages"][0]["error"]


@pytest.mark.parametrize("failure", ["json", "identity", "empty-cases", "budgets"])
def test_manifest_failure_never_launches_or_passes_without_original_cases(lane, tmp_path, monkeypatch, failure):
    manifest = json.loads(lane.MANIFEST.read_text(encoding="utf-8"))
    if failure == "identity":
        manifest["name"] = "another-lane"
    elif failure == "empty-cases":
        manifest["cases"] = []
    elif failure == "budgets":
        manifest["deadlines_seconds"]["contained_case"] = 720
    path = tmp_path / "manifest.json"
    path.write_text("{" if failure == "json" else json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(lane, "MANIFEST", path)
    monkeypatch.setattr(lane, "_run_case", lambda *a, **kw: pytest.fail("invalid manifest launched"))
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail" and report["stages"] == []
    assert report["jams"][0]["category"] == "manifest-invalid"
    assert json.loads((tmp_path / "cr-report.json").read_text())["result"] == "fail"


def test_pytest_retains_acceptance_failure_and_reports_cleanup_error_separately(tmp_path):
    script = Path(__file__).with_name("test_restricted_supervisor_docker.py")
    source = tmp_path / "test_unwind.py"
    source.write_text(
        "import importlib.util\nimport pytest\n"
        f"spec = importlib.util.spec_from_file_location('docker_fixture', {str(script)!r})\n"
        "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
        "def fail_cleanup(*args):\n    raise AssertionError('secondary-cleanup-failure')\n"
        "module._cleanup_docker = fail_cleanup\n"
        "@pytest.fixture\n"
        "def resources(request):\n"
        "    module._register_docker_cleanup(request, (), (), lambda *args: None)\n"
        "def test_primary(resources):\n    raise ValueError('primary-acceptance-failure')\n",
        encoding="utf-8",
    )
    junit = tmp_path / "pytest-results.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--junitxml", str(junit), str(source)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "primary-acceptance-failure" in result.stdout
    assert "secondary-cleanup-failure" in result.stdout
    suites = ET.parse(junit).getroot().iter("testsuite")
    counts = [(int(suite.attrib["failures"]), int(suite.attrib["errors"])) for suite in suites]
    assert sum(failures for failures, _errors in counts) == 1
    assert sum(errors for _failures, errors in counts) == 1


def test_case_directory_failure_is_reported_without_launch(lane, tmp_path, monkeypatch):
    (tmp_path / "complete").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(lane.subprocess, "run", lambda *a, **kw: pytest.fail("invalid directory launched"))
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail"
    assert report["jams"][0]["category"] == "runner-io"
    assert len(report["stages"]) == 1


def test_timeout_reconciles_only_preassigned_resources_within_original_clock(lane, tmp_path, monkeypatch):
    observed = []

    def timeout(argv, **kwargs):
        suffix = kwargs["env"]["AGENT_DISPATCH_E2E_RESOURCE_SUFFIX"]
        observed.append(lane.resource_names(suffix))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    def cleanup(containers, networks, *, deadline):
        assert (containers, networks) == observed[0]
        assert 0 < deadline - lane.time.monotonic() <= 360
        observed.append("confirmed-absent")

    monkeypatch.setattr(lane, "_run_case", timeout)
    monkeypatch.setattr(lane, "_cleanup_docker", cleanup)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail"
    assert report["jams"][0]["category"] == "contained-timeout"
    assert report["stages"][0]["owned_resource_cleanup"] == "complete"
    assert observed[-1] == "confirmed-absent"


def test_cleanup_failure_preserves_primary_runner_failure(lane, tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "_run_case", lambda *_args, **_kwargs: 1)

    def cleanup(*_args, **_kwargs):
        raise AssertionError("owned resource remains")

    monkeypatch.setattr(lane, "_cleanup_docker", cleanup)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail"
    assert [jam["category"] for jam in report["jams"]] == ["runner-exit", "cleanup-incomplete"]
    assert report["stages"][0]["owned_resource_cleanup"] == "failed"
    assert "owned resource remains" in report["jams"][1]["error"]


def test_original_case_clock_includes_successful_cleanup(lane, tmp_path, monkeypatch):
    def run(_argv, **kwargs):
        _proof(Path(kwargs["env"]["AGENT_DISPATCH_E2E_EVIDENCE_DIR"]), "complete")
        return 0

    clock = iter((100.0, 461.0))
    monkeypatch.setattr(lane.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(lane, "_run_case", run)
    report = lane.run(
        tmp_path, central_image="candidate", bridge_python=Path("bridge"), provider_python=Path("provider"),
    )
    assert report["result"] == "fail"
    assert report["stages"][0]["duration_seconds"] == 361
    assert report["stages"][0]["owned_resource_cleanup"] == "complete"
    assert report["jams"][0]["category"] == "contained-timeout"
    assert len(report["stages"]) == 1


@pytest.mark.parametrize("suffix", ["", "../foreign", "ABCDEF012345", "0" * 13])
def test_resource_names_refuse_foreign_cleanup_scope(lane, suffix):
    with pytest.raises(ValueError, match="twelve lowercase hexadecimal"):
        lane.resource_names(suffix)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-interrupt regression")
def test_posix_deadline_unwinds_actual_containment_owner_and_reaps_grandchild(lane, tmp_path):
    tools = Path(lane.__file__).resolve().parents[2]
    receipt = tmp_path / "pids.json"
    body = tmp_path / "body.py"
    body.write_text(
        "import json,os,signal,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "def stop(*_args):\n    child.wait(timeout=5)\n    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        f"with open({str(receipt)!r},'w') as out:\n"
        "    json.dump({'parent':os.getpid(),'child':child.pid,'group':os.getpgrp()},out)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    owner = tmp_path / "owner.py"
    owner.write_text(
        "import os,sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(tools)!r})\n"
        "from plugin_test_containment import Limits,run_contained\n"
        f"raise SystemExit(run_contained([sys.executable,{str(body)!r}],"
        f"cwd=Path({str(tmp_path)!r}),env=os.environ,sandbox=Path({str(tmp_path / 'sandbox')!r}),"
        "limits=Limits(wall_seconds=60,poll_seconds=0.05)))\n",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import importlib.util,json,os,subprocess,sys,time\nfrom pathlib import Path\n"
        "if sys.platform=='linux':\n"
        "    import ctypes\n"
        "    libc=ctypes.CDLL(None,use_errno=True)\n"
        "    PR_SET_CHILD_SUBREAPER=36\n"
        "    assert libc.prctl(PR_SET_CHILD_SUBREAPER,1,0,0,0)==0, 'test subreaper admission failed'\n"
        f"spec=importlib.util.spec_from_file_location('lane',{lane.__file__!r})\n"
        "lane=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(lane)\n"
        f"lane.REPO=Path({str(tmp_path)!r})\n"
        f"with open({str(tmp_path / 'runner.log')!r},'w') as log:\n"
        "    try:\n"
        f"        lane._run_case([sys.executable,{str(owner)!r}],env=dict(os.environ),log=log,"
        "timeout=lane.CLEANUP_RESERVE_SECONDS+3)\n"
        "    except subprocess.TimeoutExpired:\n        pass\n"
        "    else:\n        raise AssertionError('deadline did not interrupt owner')\n"
        f"pids=json.loads(Path({str(receipt)!r}).read_text())\n"
        "if sys.platform=='linux':\n"
        # The controller may already have reaped the body during TERM grace.
        # Reap any adopted descendants, then require actual PID absence below.
        "    while True:\n"
        "        try:\n            os.waitpid(-1,0)\n"
        "        except ChildProcessError:\n            break\n"
        "for pid in (pids['parent'],pids['child']):\n"
        "    try:\n        os.kill(pid,0)\n"
        "    except ProcessLookupError:\n        continue\n"
        "    raise AssertionError(f'descendant {pid} survived owner unwind')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(driver)], cwd=tmp_path,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "interrupted; reaping contained process tree" in (tmp_path / "runner.log").read_text()
    assert not (tmp_path / "sandbox" / ".containment-ready").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not prove a Windows ACL")
def test_result_directory_is_created_owner_only_on_posix(lane, tmp_path, monkeypatch):
    def run(root, **_kwargs):
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert root.stat().st_uid == os.getuid()
        return {"result": "pass"}

    monkeypatch.setattr(lane, "run", run)
    monkeypatch.setattr(sys, "argv", [
        "run.py", "--central-image", "candidate",
        "--bridge-python", sys.executable, "--provider-python", sys.executable,
        "--results-root", str(tmp_path),
    ])
    assert lane.main() == 0


@pytest.mark.parametrize("failure", [
    None, "substring-only", "remaining", "inventory", "malformed", "nonstring", "timeout",
])
def test_docker_cleanup_confirms_absence_and_attempts_every_resource(monkeypatch, failure):
    script = Path(__file__).with_name("test_restricted_supervisor_docker.py")
    spec = importlib.util.spec_from_file_location("restricted_docker_case", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        inventory = "ls" in argv
        name = (
            argv[argv.index("--filter") + 1].removeprefix("name=^/?").removesuffix("$")
            if inventory else argv[-1]
        )
        if not inventory:
            if name == "worker" and failure == "timeout":
                raise module.subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return SimpleNamespace(returncode=1, stdout="", stderr="arbitrary localized removal diagnostic")
        if name == "worker":
            if failure == "timeout":
                raise module.subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if failure == "inventory":
                return SimpleNamespace(returncode=1, stderr="daemon unavailable", stdout="")
            if failure == "remaining":
                return SimpleNamespace(returncode=0, stderr="", stdout='"worker"\n')
            if failure == "malformed":
                return SimpleNamespace(returncode=0, stderr="", stdout="{")
            if failure == "nonstring":
                return SimpleNamespace(returncode=0, stderr="", stdout="null")
            if failure == "substring-only":
                return SimpleNamespace(returncode=0, stderr="", stdout='"worker-suffix"\n')
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(module.subprocess, "run", run)
    if failure not in (None, "substring-only"):
        with pytest.raises(AssertionError, match="cleanup|inventory|still exists"):
            module._cleanup_docker(("worker", "central"), ("internal", "management"))
    else:
        module._cleanup_docker(("worker", "central"), ("internal", "management"))
    assert {argv[-1] for argv in calls if "ls" not in argv} == {
        "worker", "central", "internal", "management",
    }
