"""Host custody survives fatal contained workers without accepting incomplete proof."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def lane():
    path = Path(__file__).resolve().parents[3] / "tools/clean-room/central-local-authority/run.py"
    spec = importlib.util.spec_from_file_location("central_authority_lane", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proof(lane, env):
    owner = lane.read_owner(env)
    evidence = Path(owner["evidence"])
    evidence.mkdir()
    proof = {
        "scenario": "central-local-authority", "tier": "P", "family": "F1",
        "result": "pass", "cleanup": "complete", "jams": [],
        "env": {"image": owner["image"]},
        "resources": {"network": owner["networks"][0], "containers": owner["containers"]},
        "stages": [{"name": name, "status": "pass"} for name in lane.STAGES],
    }
    (evidence / "cr-report.json").write_text(json.dumps(proof), encoding="utf-8")
    return proof, evidence


def test_host_preassigns_every_resource_and_preserves_original_bounds(lane, tmp_path, monkeypatch):
    observed = []

    def run(argv, *, env, log, timeout):
        owner = lane.read_owner(env)
        assert owner["containers"][-1].startswith("authority-proof-uid-")
        assert len(owner["containers"]) == 3 and len(owner["networks"]) == 1
        assert timeout == 360
        for flag, expected in (("--test-timeout", "300"), ("--timeout", "360"), ("--plugin-timeout", "360")):
            assert argv[argv.index(flag) + 1] == expected
        assert "--allow-host-state" not in argv
        observed.append(owner)
        _proof(lane, env)
        return 0

    def cleanup(containers, networks, *, deadline):
        assert list(containers) == observed[0]["containers"]
        assert list(networks) == observed[0]["networks"]
        assert 0 < deadline - lane.time.monotonic() <= 360

    monkeypatch.setattr(lane, "_run_case", run)
    monkeypatch.setattr(lane, "_cleanup_docker", cleanup)
    report = lane.run(tmp_path, central_image="candidate")
    assert report["result"] == "pass" and report["jams"] == []
    assert report["stages"][0]["owned_resource_cleanup"] == "complete"
    assert json.loads((tmp_path / "cr-report.json").read_text()) == report


@pytest.mark.parametrize("failure", [
    "nonzero", "timeout", "skip", "malformed", "stage", "empty", "image", "resource", "cleanup", "jams",
])
def test_outer_report_fails_closed_without_replay(lane, tmp_path, monkeypatch, failure):
    calls = []
    cleaned = []

    def run(argv, *, env, **_kwargs):
        calls.append(argv)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 330)
        if failure not in ("skip", "nonzero"):
            proof, evidence = _proof(lane, env)
            if failure == "stage":
                proof["stages"][0]["status"] = "fail"
            elif failure == "empty":
                proof["stages"] = []
            elif failure == "image":
                proof["env"]["image"] = "foreign"
            elif failure == "resource":
                proof["resources"]["containers"] = []
            elif failure == "cleanup":
                proof["cleanup"] = "pending"
            elif failure == "jams":
                proof["jams"] = [{"category": "actual-failure"}]
            (evidence / "cr-report.json").write_text(
                "{" if failure == "malformed" else json.dumps(proof), encoding="utf-8",
            )
        return 1 if failure == "nonzero" else 0

    monkeypatch.setattr(lane, "_run_case", run)
    monkeypatch.setattr(lane, "_cleanup_docker", lambda *a, **kw: cleaned.append((a, kw)))
    report = lane.run(tmp_path, central_image="candidate")
    assert report["result"] == "fail" and len(calls) == len(cleaned) == 1
    assert report["jams"][0]["category"] == {
        "nonzero": "runner-exit", "timeout": "contained-timeout",
    }.get(failure, "proof-invalid")
    assert report["stages"][0]["owned_resource_cleanup"] == "complete"


def test_cleanup_jam_preserves_primary_failure(lane, tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "_run_case", lambda *a, **kw: 17)

    def cleanup(*_args, **_kwargs):
        raise AssertionError("inventory unavailable")

    monkeypatch.setattr(lane, "_cleanup_docker", cleanup)
    report = lane.run(tmp_path, central_image="candidate")
    assert report["result"] == "fail"
    assert [jam["category"] for jam in report["jams"]] == ["runner-exit", "cleanup-incomplete"]
    assert report["stages"][0]["owned_resource_cleanup"] == "failed"


def test_cleanup_time_counts_inside_original_clock(lane, tmp_path, monkeypatch):
    def run(_argv, *, env, **_kwargs):
        _proof(lane, env)
        return 0

    clock = iter((100.0, 461.0))
    monkeypatch.setattr(lane.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(lane, "_run_case", run)
    monkeypatch.setattr(lane, "_cleanup_docker", lambda *a, **kw: None)
    report = lane.run(tmp_path, central_image="candidate")
    assert report["result"] == "fail"
    assert report["stages"][0]["duration_seconds"] == 361
    assert report["jams"][0]["category"] == "contained-timeout"


@pytest.mark.parametrize("failure", ["identity", "budgets", "malformed"])
def test_invalid_manifest_never_launches(lane, tmp_path, monkeypatch, failure):
    manifest = json.loads(lane.MANIFEST.read_text(encoding="utf-8"))
    if failure == "identity":
        manifest["name"] = "foreign"
    else:
        manifest["deadlines_seconds"]["contained_case"] = 720
    path = tmp_path / "manifest.json"
    path.write_text("{" if failure == "malformed" else json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(lane, "MANIFEST", path)
    monkeypatch.setattr(lane, "_run_case", lambda *a, **kw: pytest.fail("invalid manifest launched"))
    report = lane.run(tmp_path, central_image="candidate")
    assert report["result"] == "fail" and report["stages"] == []
    assert report["jams"][0]["category"] == "manifest-invalid"


@pytest.mark.parametrize("suffix", ["", "../foreign", "ABCDEF012345", "a" * 13])
def test_resource_names_reject_foreign_scope(lane, suffix):
    with pytest.raises(ValueError, match="twelve lowercase hexadecimal"):
        lane.resource_names(suffix)


def test_direct_optin_refuses_before_any_docker(monkeypatch, tmp_path):
    from tests import test_central_authority_docker as proof

    monkeypatch.delenv("AGENT_DISPATCH_AUTHORITY_E2E_OWNER", raising=False)
    monkeypatch.setattr(proof, "_docker", lambda *a: pytest.fail("unowned Docker operation"))
    with pytest.raises(ValueError, match="outer host owner"):
        proof.test_native_central_authority_never_promotes_a_local_queue(tmp_path)


@pytest.mark.parametrize("field", ["image", "evidence", "containers", "networks"])
def test_fixture_refuses_owner_binding_mismatch(lane, tmp_path, monkeypatch, field):
    def run(_argv, *, env, **_kwargs):
        registry = Path(env["AGENT_DISPATCH_AUTHORITY_E2E_OWNER"])
        owner = json.loads(registry.read_text())
        owner[field] = "foreign"
        registry.write_text(json.dumps(owner), encoding="utf-8")
        with pytest.raises(ValueError, match="does not match"):
            lane.read_owner(env)
        return 1

    monkeypatch.setattr(lane, "_run_case", run)
    monkeypatch.setattr(lane, "_cleanup_docker", lambda *a, **kw: None)
    assert lane.run(tmp_path, central_image="candidate")["result"] == "fail"


def test_native_killed_worker_leaves_external_state_for_outer_verified_cleanup(lane, tmp_path, monkeypatch):
    """A file-backed daemon protocol stub is not a real Docker fault proof."""
    tools = Path(lane.__file__).resolve().parents[2]
    daemon = tmp_path / "daemon"
    daemon.mkdir()
    receipt = tmp_path / "worker.json"
    body = tmp_path / "body.py"
    body.write_text(
        "import json,os,time\nfrom pathlib import Path\n"
        "owner=json.loads(Path(os.environ['AGENT_DISPATCH_AUTHORITY_E2E_OWNER']).read_text())\n"
        f"daemon=Path({str(daemon)!r})\n"
        "for kind,key in [('container','containers'),('network','networks')]:\n"
        "    for name in owner[key]:\n        (daemon/(kind+'__'+name)).touch()\n"
        f"Path({str(receipt)!r}).write_text(json.dumps({{'pid':os.getpid()}}))\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    owner_script = tmp_path / "owner.py"
    owner_script.write_text(
        "import os,sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(tools)!r})\n"
        "from plugin_test_containment import Limits,run_contained\n"
        f"raise SystemExit(run_contained([sys.executable,{str(body)!r}],"
        f"cwd=Path({str(tmp_path)!r}),env=os.environ,sandbox=Path({str(tmp_path / 'sandbox')!r}),"
        "limits=Limits(wall_seconds=2,poll_seconds=0.05)))\n", encoding="utf-8",
    )
    stub = tmp_path / "docker.py"
    stub.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"root=Path({str(daemon)!r})\nargs=sys.argv[1:]\n"
        "kind='network' if args[0]=='network' else 'container'\n"
        "if 'ls' in args:\n"
        "    name=args[args.index('--filter')+1].removeprefix('name=^/?').removesuffix('$')\n"
        "    if (root/(kind+'__'+name)).exists():\n        print(json.dumps(name))\n"
        "else:\n    (root/(kind+'__'+args[-1])).unlink(missing_ok=True)\n",
        encoding="utf-8",
    )
    actual_run = subprocess.run
    calls = []
    case_driver = tmp_path / "case-driver.py"
    case_driver.write_text(
        "import importlib.util,os,sys\n"
        f"spec=importlib.util.spec_from_file_location('lane',{lane.__file__!r})\n"
        "lane=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(lane)\n"
        f"raise SystemExit(lane._owner._run_case([sys.executable,{str(owner_script)!r}],"
        "env=dict(os.environ),log=sys.stdout,timeout=lane._owner.CLEANUP_RESERVE_SECONDS+10))\n",
        encoding="utf-8",
    )

    def subprocess_run(argv, **kwargs):
        if argv[0] == "docker":
            calls.append(argv)
            return actual_run([sys.executable, str(stub), *argv[1:]], **kwargs)
        return actual_run(argv, **kwargs)

    def run(_argv, *, env, log, **_kwargs):
        # The real owner must not replace pytest-timeout's POSIX timer.
        result = actual_run(
            [sys.executable, str(case_driver)], env=env, stdout=log, stderr=log, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode
        assert result == 124, "canonical containment did not kill the worker at its deadline"
        return result

    monkeypatch.setattr(lane._owner.subprocess, "run", subprocess_run)
    monkeypatch.setattr(lane, "_run_case", run)
    report = lane.run(tmp_path, central_image="protocol-stub-only")
    assert report["result"] == "fail" and report["jams"][0]["category"] == "runner-exit", report
    assert report["stages"][0]["owned_resource_cleanup"] == "complete"
    assert "wall-clock limit exceeded (2s)" in (tmp_path / "runner.log").read_text()
    assert not (tmp_path / "sandbox" / ".containment-ready").exists()
    assert not list(daemon.iterdir())
    owner = json.loads((tmp_path / "owned-resources.json").read_text())
    assert len(calls) == 8
    assert {argv[-1] for argv in calls if "ls" not in argv} == {
        *owner["containers"], *owner["networks"],
    }
    pid = json.loads(receipt.read_text())["pid"]
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                exit_code = wintypes.DWORD()
                assert kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                assert exit_code.value != 259, "contained worker survived its owner's cleanup"
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87, "worker termination could not be verified"
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
