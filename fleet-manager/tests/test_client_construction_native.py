"""Additional real process/install boundaries, selected independently on demand."""

from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from fleet_manager import artifact_staging
from fleet_manager import client_construction as construction
from fleet_manager.planning import PlanError, canonical, digest
from test_artifact_staging import primitive as primitive  # noqa: F401 - pytest fixture
from test_client_admission import database_bytes, identity, independently_sign, root_at, selection
from test_client_construction import (
    admit, candidate, construct, signed_wheel as signed_wheel, target,
)
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.client_distribution_full


@contextmanager
def stage(fixture, parent):
    parent.mkdir(mode=0o700, exist_ok=True)
    with artifact_staging.stage_authenticated_artifacts(
        canonical(fixture["manifest"]), fixture["proof"], fixture["policy"], fixture["sources"],
        authority=identity()["authority"], installation=identity()["installation"], role="client",
        staging_parent=parent, clock=lambda: 150,
    ) as staged:
        yield staged


@pytest.mark.timeout(90)
def test_actual_failed_doctor_preserves_previous_selector_config_and_admitted_floor(
    tmp_path, primitive, signed_wheel,
):
    fixture = copy.deepcopy(signed_wheel)
    source = Path(fixture["sources"]["components"]["cli"])
    output = io.BytesIO()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(output, "w") as modified:
        for item in archive.infolist():
            content = archive.read(item)
            if item.filename == "fleet_manager/client_diagnostics.py":
                content = b"def diagnose_client():\n    raise SystemExit(23)\n"
            modified.writestr(item, content)
    wheel = tmp_path / source.name
    wheel.write_bytes(output.getvalue())
    fixture["sources"]["components"]["cli"] = str(wheel)
    fixture["manifest"]["components"]["cli"]["artifact_sha256"] = hashlib.sha256(
        wheel.read_bytes(),
    ).hexdigest()
    payload = {**fixture["proof"]["payload"], "release_digest": digest(fixture["manifest"])}
    signed = independently_sign([payload], tmp_path)
    fixture["proof"] = {**fixture["proof"], **signed["proofs"][0]}
    fixture["policy"]["keys"][0]["public_key"] = signed["public_key"]
    root = root_at(tmp_path)
    with stage(fixture, tmp_path / "stage") as staged:
        admit(root, fixture, staged, fixture["policy"])
        namespace = construction._namespace(root, identity())
        previous = primitive.slot(namespace, "previous")
        sentinel = previous / "keep"
        sentinel.write_bytes(b"previous healthy runtime")
        primitive.mark_complete(namespace, "previous", payload_hash="0" * 64)
        for selector in (primitive.CURRENT_VERSION_FILE, primitive.LAST_KNOWN_GOOD_FILE):
            (namespace / selector).write_text("previous\n", encoding="utf-8")
            (namespace / selector).chmod(0o600)
        selectors = {name: (namespace / name).read_bytes() for name in (
            primitive.CURRENT_VERSION_FILE, primitive.LAST_KNOWN_GOOD_FILE,
        )}
        user = root / "user-config"
        user.write_bytes(b"custom config")
        before = database_bytes(root)
        with pytest.raises(PlanError, match="local diagnostics failed with exit status 23"):
            construct(root, fixture, staged, fixture["policy"])
        failed = candidate(root, fixture, primitive)
        assert failed.is_dir() and not (failed / primitive.COMPLETE_MARKER).exists()
        assert database_bytes(root) == before
        assert sentinel.read_bytes() == b"previous healthy runtime"
        assert user.read_bytes() == b"custom config"
        assert all((namespace / name).read_bytes() == content for name, content in selectors.items())


INSTALLED = r"""
import json,sys
from pathlib import Path
from fleet_manager.artifact_staging import stage_authenticated_artifacts
from fleet_manager.client_construction import construct_client_release
from fleet_manager.planning import canonical
request=json.loads(sys.stdin.readline())
with stage_authenticated_artifacts(
    canonical(request["manifest"]),request["proof"],request["policy"],request["sources"],
    authority=request["identity"]["authority"],installation=request["identity"]["installation"],
    role="client",staging_parent=Path(request["stage"]),clock=lambda:150,
) as staged:
    print(json.dumps(construct_client_release(
        Path(request["root"]),request["identity"],request["selection"],staged,
        current_policy=lambda:request["policy"],current_target=lambda:request["target"],
        clock=lambda:150,
    )),flush=True)
"""


def request(root, fixture, area):
    staging = area / "stage"
    staging.mkdir(mode=0o700)
    return {
        **fixture, "root": str(root), "identity": identity(), "selection": selection(fixture),
        "target": target(fixture), "stage": str(staging),
    }


@pytest.mark.timeout(110)
@pytest.mark.parametrize("transition", ["construct", "activate-runtime", "register-commands"])
def test_actual_installed_console_client_state_transitions(
    tmp_path, primitive, signed_wheel, transition,
):
    fixture = copy.deepcopy(signed_wheel)
    now = int(time.time())
    fixture["proof"]["payload"].update(issued_at=now - 10, expires_at=now + 300)
    fixture["policy"].update(valid_from=now - 20, valid_until=now + 600)
    fixture["policy"]["keys"][0].update(valid_from=now - 20, valid_until=now + 600)
    signed = independently_sign([fixture["proof"]["payload"]], tmp_path)
    fixture["proof"] = {**fixture["proof"], **signed["proofs"][0]}
    fixture["policy"]["keys"][0]["public_key"] = signed["public_key"]
    client_home = tmp_path
    if transition == "register-commands":
        client_home = tmp_path / "client \u00e9\u4e2d"
        client_home.mkdir(mode=0o700)
    root = root_at(client_home)
    staging = tmp_path / "cli-stage"
    staging.mkdir(mode=0o700)
    values = {
        "identity": identity(), "selection": selection(fixture),
        "release": fixture["manifest"], "proof": fixture["proof"],
        "policy": fixture["policy"], "sources": fixture["sources"], "target": target(fixture),
    }
    paths = {}
    for name, value in values.items():
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_bytes(canonical(value))
    console = Path(sys.executable).with_name(
        "fleet-manager.exe" if os.name == "nt" else "fleet-manager",
    )
    assert console.is_file(), "the actual installed owner console is required"
    base = ["--root", str(root), "--identity", str(paths["identity"])]
    inputs = [
        value for name in ("selection", "release", "proof", "policy", "sources")
        for value in ("--" + name, str(paths[name]))
    ] + ["--staging-parent", str(staging)]
    reports = []
    for operation, arguments in (
        ("initialize", base), ("admit", base + inputs),
        ("construct", base + inputs + ["--target", str(paths["target"])]),
    ):
        result = subprocess.run(
            [str(console), "client-state", operation, *arguments], cwd=tmp_path,
            capture_output=True, text=True, timeout=90, **FLAGS,
        )
        assert result.returncode == 0, result.stderr
        reports.append(json.loads(result.stdout))
    assert reports[0]["status"] == "initialized"
    assert reports[1]["status"] == "admitted" and not reports[1]["constructed"]
    assert reports[2]["healthy"] and not reports[2]["active"]
    assert reports[2]["target_digest"] == digest(target(fixture))
    assert reports[2]["admitted"] == reports[1]["admitted"]
    assert not primitive.current_version(root / construction.NAMESPACE)
    if transition != "construct":
        result = subprocess.run(
            [str(console), "client-state", "activate-runtime", *base, *inputs,
             "--target", str(paths["target"])], cwd=tmp_path,
            capture_output=True, text=True, timeout=90, **FLAGS,
        )
        assert result.returncode == 0, result.stderr
        activated = json.loads(result.stdout)
        assert activated["active"] and activated["health"]["status"] == "installed"
        assert not activated["frontdoor_registered"] and not activated["live_readiness_verified"]
        assert primitive.current_version(root / construction.NAMESPACE) == reports[2]["candidate"]
        if transition == "register-commands":
            directory = tmp_path / "registered commands \u00e9\u4e2d"
            directory.mkdir(mode=0o700)
            floor = database_bytes(root)
            result = subprocess.run(
                [str(console), "client-state", "register-commands", *base, *inputs,
                 "--target", str(paths["target"]), "--command-directory", str(directory)],
                cwd=tmp_path, capture_output=True, text=True, timeout=90, **FLAGS,
            )
            assert result.returncode == 0, result.stderr
            registered = json.loads(result.stdout)
            assert registered["frontdoor_registered"] and not registered["path_modified"]
            assert not registered["live_readiness_verified"]
            assert registered["candidate"] == reports[2]["candidate"]
            assert database_bytes(root) == floor


@pytest.mark.timeout(110)
def test_two_independent_installed_owners_serialize_construction_and_health_reuse(
    tmp_path, primitive, signed_wheel,
):
    root = root_at(tmp_path)
    fixture = signed_wheel
    with stage(fixture, tmp_path / "admission-stage") as staged:
        admit(root, fixture, staged, fixture["policy"])
    before = database_bytes(root)
    processes = []
    try:
        for index in range(2):
            area = tmp_path / f"worker-{index}"
            area.mkdir(mode=0o700)
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", INSTALLED], cwd=area,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **FLAGS,
            )
            process.stdin.write(json.dumps(request(root, fixture, area)) + "\n")
            process.stdin.close()
            process.stdin = None
            processes.append(process)
        reports = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=90)
            assert process.returncode == 0, stderr
            reports.append(json.loads(stdout))
        assert sorted(item["reused"] for item in reports) == [False, True]
        assert reports[0]["runtime_sha256"] == reports[1]["runtime_sha256"]
        assert database_bytes(root) == before
        assert not primitive.current_version(root / construction.NAMESPACE)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def _wait(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, "owned process boundary did not settle"
        time.sleep(0.05)


def _dead(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87
            return True
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)
    process = Path("/proc") / str(pid) / "stat"
    try:
        return process.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


@pytest.mark.timeout(45)
@pytest.mark.skipif(sys.platform not in ("win32", "linux"),
                    reason="native process-exit witness requires Windows handles or Linux procfs")
def test_abrupt_owner_death_fences_cleanup_until_child_tree_settles(tmp_path, primitive):
    namespace = tmp_path / "custody"
    namespace.mkdir(mode=0o700)
    root = primitive.slot(namespace, "probe")
    signal = root / "ready.json"
    child = """
import json,os,subprocess,sys,time
from pathlib import Path
kwargs={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
descendant=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],**kwargs)
ready=Path(sys.argv[1])
temporary=ready.with_suffix(".tmp")
temporary.write_text(json.dumps([os.getpid(),descendant.pid]))
os.replace(temporary,ready)
time.sleep(60)
"""
    owner = """
import sys
from pathlib import Path
from fleet_manager import artifact_staging,client_construction
root=Path(sys.argv[1])
with artifact_staging._primitive() as runtime:
    with runtime.construction_lease(root.parent.parent,root.name) as descriptor:
        client_construction._run(
            [sys.executable,"-I","-c",sys.argv[2],sys.argv[3]],root,"custody probe",
            descriptor=descriptor,
        )
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", owner, str(root), child, str(signal)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **FLAGS,
    )
    try:
        _wait(lambda: signal.exists() or process.poll() is not None)
        assert signal.exists(), process.communicate(timeout=10)[1].decode()
        pids = json.loads(signal.read_bytes())
        assert all(not _dead(pid) for pid in pids)
        with pytest.raises(primitive.ConstructionTimeout):
            with primitive.construction_lease(namespace, "probe", timeout=0):
                pytest.fail("live constructor lost ownership")
        process.kill()
        process.wait(timeout=10)
        with primitive.construction_lease(namespace, "probe", timeout=10):
            with primitive._cleanup_lease(namespace, "probe", allow_construction_owner=True) as admitted:
                if admitted:
                    assert all(_dead(pid) for pid in pids)
        _wait(lambda: all(_dead(pid) for pid in pids))
        with primitive.construction_lease(namespace, "probe", timeout=10):
            with primitive._cleanup_lease(namespace, "probe", allow_construction_owner=True) as admitted:
                assert admitted
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)
