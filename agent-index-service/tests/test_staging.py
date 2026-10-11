"""Pure transaction tests: all build execution and packaged primitives are explicit fakes."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_release as wheels
import yaml
import zdd

from agent_index_service import staging
from agent_index_service.staging import (
    CandidateError,
    NativeBuildConfig,
    inspect_candidates,
    stage_candidate,
)


class Primitive:
    def version_dir(self, root, version):
        return root / "versions" / version

    def slot(self, root, version, *, clean_incomplete):
        assert clean_incomplete is False
        return self.version_dir(root, version)

    def slot_python(self, root, version):
        path = self.version_dir(root, version) / "Scripts" / "python.exe"
        return path if path.is_file() else None

    def read_marker(self, root, version):
        path = self.version_dir(root, version) / ".install-complete.json"
        return json.loads(path.read_text()) if path.exists() else None

    def is_complete(self, root, version, *, expect_hash):
        value = self.read_marker(root, version)
        return bool(value and value["payload_hash"] == expect_hash)

    def mark_complete(self, root, version, *, payload_hash):
        assert (self.version_dir(root, version) / "candidate.json").is_file()
        path = self.version_dir(root, version) / ".install-complete.json"
        path.write_text(json.dumps({"version": version, "payload_hash": payload_hash}))
        return path


@pytest.fixture
def case(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name, version in wheels.VERSIONS.items():
        wheels._make_wheel(bundle, name, version)
    descriptor = wheels.build_descriptor(bundle, source_commit=wheels.COMMIT)
    descriptor_path = wheels._save(bundle, descriptor)
    host_config = tmp_path / "host.yaml"
    host_config.write_text(yaml.safe_dump({
        "schema_version": 1, "home": str(tmp_path / "host-state"),
        "sources": [{"name": "git:fixture", "path": str(tmp_path / "fixture")}],
    }))
    tools = tmp_path / "tools"
    tools.mkdir()
    python, uv = tools / "python", tools / "uv"
    python.write_bytes(b"fake-python")
    uv.write_bytes(b"fake-uv")
    lock, config = tools / "requirements.txt", tools / "uv.toml"
    lock.write_text("packaging==26.3 --hash=sha256:" + "a" * 64 + "\n")
    config.write_text('index-url = "https://governed.invalid/simple"\n')
    build = NativeBuildConfig(python, uv, lock, config)
    primitive = Primitive()
    monkeypatch.setattr(staging, "_primitive", lambda: primitive)
    calls = []

    def run(argv, *, cwd, env, deadline):
        assert deadline > 0
        assert "AGENT_INDEX_REPO" not in env
        assert not (cwd / ".install-complete.json").exists()
        assert str(tmp_path / "host-state") not in env.values()
        calls.append((argv, env.copy()))
        if "venv" in argv:
            interpreter = cwd / "Scripts" / "python.exe"
            interpreter.parent.mkdir()
            interpreter.write_bytes(b"fake-slot-python")
        if "-c" in argv:
            return json.dumps({"validated": True,
                               "versions": json.loads(env["STAGING_EXPECTED_PINS"])})
        return ""

    monkeypatch.setattr(staging, "_run", run)
    return SimpleNamespace(
        root=tmp_path / "runtime", host=host_config, descriptor=descriptor_path,
        bundle=bundle, build=build, primitive=primitive, calls=calls, tmp=tmp_path,
    )


def stage(case, **overrides):
    kwargs = {
        "expected_source_commit": wheels.COMMIT, "install_root": case.root,
        "host_config_path": case.host, "build": case.build,
    }
    kwargs.update(overrides)
    return stage_candidate(case.descriptor, **kwargs)


def tree(path):
    return {str(file.relative_to(path)): file.read_bytes()
            for file in path.rglob("*") if file.is_file()}


def test_first_touch_final_path_snapshot_and_completion(case, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_REPO", "ambient-must-not-leak")
    monkeypatch.setenv("UV_INDEX_URL", "ambient-must-not-leak")
    result = stage(case)
    assert result == {
        "schema": "agent-index-service.candidate", "schema_version": 1, "state": "staged",
        "version": "0.1.1.dev1", "source_commit": wheels.COMMIT,
        "identity": result["identity"],
        "slot": str(case.root / "versions" / "0.1.1.dev1"),
        "python": str(case.root / "versions" / "0.1.1.dev1" / "Scripts" / "python.exe"),
    }
    assert len(case.calls) == 4
    slot = Path(result["slot"])
    assert not (slot / "build-state").exists()
    assert case.calls[0][0][-1] == str(slot)
    assert "--require-hashes" in case.calls[1][0]
    assert "--no-index" in case.calls[2][0]
    assert any(item.endswith(".whl[native]") for item in case.calls[2][0])
    assert "-I" in case.calls[3][0]
    assert case.calls[3][1]["AGENT_INDEX_HOME"].startswith(str(slot))
    assert "UV_INDEX_URL" not in case.calls[3][1]
    snapshot = slot / "artifacts" / "release.json"
    assert wheels.verify_descriptor(snapshot, expected_source_commit=wheels.COMMIT)
    receipt = json.loads((slot / "candidate.json").read_text())
    assert set(receipt) == {
        "schema", "schema_version", "version", "source_commit", "identity",
        "descriptor_sha256", "lock_sha256", "build_policy_sha256",
    }
    assert "governed.invalid" not in json.dumps(receipt)
    assert not (case.tmp / "host-state").exists()
    for forbidden in ("current-version", "last-known-good", "active.json", "venv", ".venv"):
        assert not (case.root / forbidden).exists()


def test_exact_replay_keeps_completed_slot_bytes(case):
    first = stage(case)
    before = tree(Path(first["slot"]))
    second = stage(case)
    assert second["state"] == "already_staged"
    assert second["identity"] == first["identity"]
    assert len(case.calls) == 4
    assert tree(Path(first["slot"])) == before


@pytest.mark.parametrize("change", [
    "lock", "policy", "commit", "receipt", "interpreter", "artifact", "tool",
])
def test_same_version_conflict_preserved(case, change):
    result = stage(case)
    slot = Path(result["slot"])
    if change == "lock":
        case.build.third_party_lock.write_text("packaging==26.2 --hash=sha256:" + "a" * 64)
    elif change == "policy":
        case.build.uv_config.write_text('index-url = "https://different.invalid/simple"')
    elif change == "commit":
        data = json.loads(case.descriptor.read_text())
        data["source_commit"] = "b" * 40
        case.descriptor.write_text(json.dumps(data))
    elif change == "receipt":
        (slot / "candidate.json").write_text("{}")
    elif change == "artifact":
        next((slot / "artifacts").glob("*.whl")).unlink()
    elif change == "tool":
        case.build.uv.write_bytes(b"changed uv")
    else:
        Path(result["python"]).unlink()
    before = tree(slot)
    with pytest.raises(CandidateError):
        stage(case, expected_source_commit="b" * 40 if change == "commit" else wheels.COMMIT)
    assert tree(slot) == before
    assert len(case.calls) == 4


def test_preexisting_incomplete_preserved(case):
    slot = case.root / "versions" / "0.1.1.dev1"
    slot.mkdir(parents=True)
    (slot / "unfinished").write_bytes(b"previous-owner")
    with pytest.raises(CandidateError, match="incomplete"):
        stage(case)
    assert (slot / "unfinished").read_bytes() == b"previous-owner"
    assert not case.calls


def test_wrong_revision_zero_mutation(case):
    with pytest.raises(CandidateError):
        stage(case, expected_source_commit="c" * 40)
    assert not case.root.exists()
    assert not case.calls


@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, True, "10"])
def test_timeout_config_strict(value):
    with pytest.raises(CandidateError):
        NativeBuildConfig(Path("a"), Path("b"), Path("c"), Path("d"), value)


@pytest.mark.parametrize("text", [
    "", "packaging>=26 --hash=sha256:" + "a" * 64,
    "packaging==26", "-r other.txt", "-e ./editable",
    "packaging @ git+https://invalid.example/repo --hash=sha256:" + "a" * 64,
    "agent-index==1 --hash=sha256:" + "a" * 64,
    "packaging==26; sys_platform=='linux' --hash=sha256:" + "a" * 64,
    "packaging==26 --hash=md5:" + "a" * 32,
    "packaging==26 --hash=sha256:" + "a" * 64 + " --index-url=raw",
    ("packaging==26 --hash=sha256:" + "a" * 64 + "\n") * 2,
])
def test_bad_lock_zero_mutation(case, text):
    case.build.third_party_lock.write_text(text)
    with pytest.raises(CandidateError):
        stage(case)
    assert not case.root.exists()
    assert not case.calls


@pytest.mark.parametrize("text", ["", "[broken", "index-url = 1\nindex-url = 2"])
def test_bad_uv_configuration_zero_mutation(case, text):
    case.build.uv_config.write_text(text)
    with pytest.raises(CandidateError):
        stage(case)
    assert not case.root.exists()


@pytest.mark.parametrize("kind", [
    "home", "data", "routing", "bundle", "tool", "relative", "ancestor",
])
def test_overlap_or_relative_root_rejected(case, kind):
    root = {
        "home": case.tmp / "host-state",
        "data": case.tmp / "host-state" / "data",
        "routing": case.tmp / "host-state" / "routing",
        "bundle": case.bundle / "runtime",
        "tool": case.build.uv.parent,
        "relative": Path("relative"),
        "ancestor": case.tmp,
    }[kind]
    before = tree(case.tmp)
    with pytest.raises(CandidateError):
        stage(case, install_root=root)
    assert tree(case.tmp) == before


def test_symlink_ancestor_rejected(case):
    target = case.tmp / "target"
    target.mkdir()
    link = case.tmp / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink not supported: {exc}")
    with pytest.raises(CandidateError, match="symlink"):
        stage(case, install_root=link / "runtime")
    assert not (target / "runtime").exists()


@pytest.mark.parametrize("step", [0, 1, 2, 3])
def test_build_failures_cleanup_only_owned_slot(case, monkeypatch, step):
    other = case.root / "versions" / "previous"
    other.mkdir(parents=True)
    (other / "keep").write_bytes(b"untouched")
    original = staging._run
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        if len(calls) - 1 == step:
            raise CandidateError("fixture dependency/probe/timeout failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(staging, "_run", fail)
    with pytest.raises(CandidateError):
        stage(case)
    assert not (case.root / "versions" / "0.1.1.dev1").exists()
    assert (other / "keep").read_bytes() == b"untouched"
    assert not (case.tmp / "host-state").exists()


@pytest.mark.parametrize("output", [
    "not json", '{"validated":false}', '{"validated":true,"versions":{}}',
    '{"validated":true,"validated":true,"versions":{}}',
])
def test_invalid_probe_no_completion(case, monkeypatch, output):
    original = staging._run

    def probe(argv, **kwargs):
        return output if "-c" in argv else original(argv, **kwargs)

    monkeypatch.setattr(staging, "_run", probe)
    with pytest.raises(CandidateError):
        stage(case)
    assert not (case.root / "versions" / "0.1.1.dev1").exists()


def test_source_snapshot_tamper_cleanup(case, monkeypatch):
    original = staging._copy

    def changed(source, target, expected):
        original(source, target, expected)
        with target.open("ab") as stream:
            stream.write(b"changed")

    monkeypatch.setattr(staging, "_copy", changed)
    with pytest.raises(CandidateError):
        stage(case)
    assert not case.calls
    assert not (case.root / "versions" / "0.1.1.dev1").exists()


def test_lock_contention_no_build(case, monkeypatch):
    class Busy:
        def __init__(self, root):
            pass

        def __enter__(self):
            raise RuntimeError("contending stager")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(zdd, "CutoverLock", Busy)
    with pytest.raises(CandidateError):
        stage(case)
    assert not case.calls
    assert not (case.root / "versions").exists()


def test_lease_recheck_preserves_other_completed_candidate(case, monkeypatch):
    real_lease = zdd.CutoverLock
    completed = []

    class Lease:
        def __init__(self, root):
            pass

        def __enter__(self):
            # Another stager finished after the caller's preflight, before its lease.
            with monkeypatch.context() as context:
                context.setattr(zdd, "CutoverLock", real_lease)
                completed.append(stage(case))
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(zdd, "CutoverLock", Lease)
    assert stage(case)["state"] == "already_staged"
    assert len(case.calls) == 4
    assert completed[0]["state"] == "staged"


def test_packaged_primitive_absent_explicit_error(case, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(staging.importlib, "import_module", missing)
    monkeypatch.setattr(staging, "_primitive", original_primitive)
    with pytest.raises(CandidateError, match="build/install a wheel"):
        stage(case)
    assert not case.root.exists()


original_primitive = staging._primitive


def test_inspect_missing_root_readonly(case):
    assert inspect_candidates(case.root, host_config_path=case.host) == {
        "schema": "agent-index-service.candidate", "schema_version": 1,
        "install_root": str(case.root), "candidates": [],
    }
    assert not case.root.exists()


@pytest.mark.parametrize("layout", ["root-only", "empty-versions", "incomplete"])
def test_inspection_without_receipts_does_not_require_built_primitive(case, monkeypatch, layout):
    case.root.mkdir()
    if layout != "root-only":
        (case.root / "versions").mkdir()
    if layout == "incomplete":
        (case.root / "versions" / "0.1.0.dev1").mkdir()
    monkeypatch.setattr(staging, "_primitive", lambda: pytest.fail("unneeded primitive import"))
    result = inspect_candidates(case.root, host_config_path=case.host)
    assert len(result["candidates"]) == (1 if layout == "incomplete" else 0)
    if layout == "incomplete":
        assert result["candidates"][0]["state"] == "incomplete"


def test_inspect_complete_and_incomplete_readonly(case):
    stage(case)
    other = case.root / "versions" / "unfinished"
    other.mkdir()
    before = tree(case.root)
    result = inspect_candidates(case.root, host_config_path=case.host)
    assert [candidate["state"] for candidate in result["candidates"]] == ["staged", "incomplete"]
    assert tree(case.root) == before
    assert len(case.calls) == 4


def test_uv_policy_change_during_build_cleanup(case, monkeypatch):
    original = staging._run

    def mutate(argv, **kwargs):
        output = original(argv, **kwargs)
        if "-c" in argv:
            case.build.uv_config.write_text('index-url = "https://changed.invalid"')
        return output

    monkeypatch.setattr(staging, "_run", mutate)
    with pytest.raises(CandidateError, match="policy changed"):
        stage(case)
    assert not (case.root / "versions" / "0.1.1.dev1").exists()


def test_interrupted_build_cleanup(case, monkeypatch):
    monkeypatch.setattr(staging, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(
        KeyboardInterrupt(),
    ))
    with pytest.raises(KeyboardInterrupt):
        stage(case)
    assert not (case.root / "versions" / "0.1.1.dev1").exists()


def test_probe_does_not_serve_or_load_model_stack():
    for forbidden in ("agent_index_engine", "torch", "sentence_transformers",
                      "server.serve", "EngineClient", "spinup"):
        assert forbidden not in staging._PROBE
    assert "req.marker.evaluate" in staging._PROBE
    assert "provided" in staging._PROBE


def test_build_environment_sanitized(case, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "secret")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "cell")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MODE", "subprocess")
    env = staging._environment(case.tmp / "scratch")
    assert "PIP_INDEX_URL" not in env
    assert "COPILOT_EXTENSIONS_CONTEXT" not in env
    assert env["AGENT_INDEX_ENGINE_MODE"] == "external"
    assert env["HOME"] == str(case.tmp / "scratch")


def test_missing_descriptor_or_tool_zero_mutation(case):
    case.build.uv.unlink()
    with pytest.raises(CandidateError):
        stage(case)
    assert not case.root.exists()


def test_lock_extras_and_continuations_are_preserved(case):
    case.build.third_party_lock.write_text(
        "packaging[fixture-extra]==26.3 \\\n  --hash=sha256:" + "a" * 64 + "\n",
    )
    stage(case)
    assert json.loads(case.calls[-1][1]["STAGING_EXPECTED_EXTRAS"]) == {
        "packaging": ["fixture-extra"],
    }


def test_lost_ownership_never_recursively_cleans_slot(case, monkeypatch):
    def lose(argv, *, cwd, **kwargs):
        (cwd / ".stage-owner.json").write_text('{"token":"other-owner"}')
        raise CandidateError("build failed after ownership changed")

    monkeypatch.setattr(staging, "_run", lose)
    with pytest.raises(CandidateError):
        stage(case)
    slot = case.root / "versions" / "0.1.1.dev1"
    assert slot.exists()
    assert (slot / ".stage-owner.json").read_text() == '{"token":"other-owner"}'


def test_run_expired_deadline_never_spawns(case, monkeypatch):
    import agent_procutil

    monkeypatch.setattr(staging, "_run", original_run)
    monkeypatch.setattr(
        agent_procutil, "spawn_sync_in_kill_on_close_job",
        lambda *args, **kwargs: pytest.fail("expired build must not spawn"),
    )
    with pytest.raises(CandidateError, match="timeout"):
        staging._run([], cwd=case.tmp, env={}, deadline=0)


original_run = staging._run


def test_real_lease_error_is_candidate_error(case, monkeypatch):
    class Busy:
        def __init__(self, root):
            self.root = root

        def __enter__(self):
            raise zdd.CutoverLockedError(self.root / "zdd-cutover.lock", 123)

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(zdd, "CutoverLock", Busy)
    with pytest.raises(CandidateError, match="lease"):
        stage(case)
    assert not case.calls


@pytest.mark.parametrize("reason", ["timeout", "failure", "overflow"])
def test_build_runner_reaps_owned_job_on_all_failure_paths(case, monkeypatch, reason):
    import agent_procutil

    class Process:
        pid = 123
        returncode = None
        killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -1

        def wait(self, timeout):
            return self.returncode

    class Job:
        closed = False

        def close(self):
            self.closed = True
            process.returncode = -1

    process, job = Process(), Job()

    def spawn(argv, **kwargs):
        if reason == "failure":
            process.returncode = 7
        if reason == "overflow":
            kwargs["stdout"].write(b"x" * 129)
            kwargs["stdout"].flush()
        return process, job

    ticks = iter([0.0, 10.0, 10.0])
    monkeypatch.setattr(staging, "time", SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda value: None,
    ))
    monkeypatch.setattr(staging, "_LIMIT", 128)
    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(staging, "_run", original_run)
    with pytest.raises(CandidateError):
        staging._run(["fake"], cwd=case.tmp, env={}, deadline=5 if reason == "timeout" else 20)
    assert job.closed
    assert process.poll() is not None
    assert not list(case.tmp.glob(".build-output-*"))


@pytest.mark.parametrize("contained", [True, False])
def test_posix_launch_group_authority_from_parent(case, monkeypatch, contained):
    import agent_procutil

    calls = []
    process = SimpleNamespace(
        pid=987654, returncode=0, poll=lambda: 0, wait=lambda timeout: 0,
    )
    proxy = SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
    proxy.name = "posix"
    monkeypatch.setattr(staging, "os", proxy)
    monkeypatch.setattr(staging.sys, "platform", "linux")
    actual_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: (
        True if str(path).replace("\\", "/") == "/proc/self/stat" else actual_is_file(path)
    ))
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: contained)
    monkeypatch.setattr(agent_procutil, "no_window_kwargs", lambda: {})

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return process, None

    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(
        proxy, "killpg", lambda pid, sig: calls.append(("group", pid)), raising=False,
    )
    original_run(["fake"], cwd=case.tmp,
                 env={"COPILOT_EXTENSIONS_TEST_CONTAINED": "0" if contained else "1"},
                 deadline=time.monotonic() + 10)
    argv, kwargs = calls[0]
    assert kwargs.get("start_new_session", False) is (not contained)
    assert staging._CONTAINED_SUPERVISOR in argv
    assert len(calls) == 1  # No group signals, including after private leader exit.


def test_cleanup_unconfirmed_preserves_candidate(case, monkeypatch):
    monkeypatch.setattr(staging, "_run", lambda *args, **kwargs: (_ for _ in ()).throw(
        staging._CleanupUnconfirmed("cleanup unconfirmed"),
    ))
    with pytest.raises(CandidateError, match="unconfirmed"):
        stage(case)
    assert (case.root / "versions" / "0.1.1.dev1").exists()


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_real_owned_descendant_cleanup_and_sanitized_error(case, failure):
    if os.name != "nt" and sys.platform != "linux":
        pytest.skip("contained POSIX supervisor currently requires Linux")
    from agent_index.rendezvous import pid_alive

    receipt = case.tmp / "owned-descendant.json"
    script = """
import json,os,subprocess,sys,time
from pathlib import Path
child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(json.dumps({'child':child.pid,
    'group':os.getpgrp() if hasattr(os,'getpgrp') else None}))
print('https://user:password@secret.invalid/feed', file=sys.stderr, flush=True)
if sys.argv[2] == 'exit':
    raise SystemExit(7)
time.sleep(120)
"""
    with pytest.raises(CandidateError) as error:
        original_run([sys.executable, "-c", script, str(receipt), failure],
                     cwd=case.tmp, env=staging._environment(case.tmp),
                     deadline=time.monotonic() + 5)
    assert receipt.is_file()
    identity = json.loads(receipt.read_text())
    if os.name != "nt":
        assert identity["group"] == os.getpgrp()
    until = time.monotonic() + 5
    while pid_alive(identity["child"]) and time.monotonic() < until:
        time.sleep(0.05)
    assert not pid_alive(identity["child"])
    assert "isolated runtime probe" in str(error.value)
    assert "password" not in str(error.value)
    assert "secret.invalid" not in str(error.value)
    assert "exit status 7" in str(error.value) if failure == "exit" else (
        "timeout" in str(error.value)
    )
    assert not list(case.tmp.glob(".build-output-*"))


@pytest.mark.parametrize("current", ["reused-start-token", None])
def test_supervisor_identity_mismatch_refuses_all_signals(monkeypatch, current):
    process = SimpleNamespace(
        pid=123, returncode=None, poll=lambda: None,
        terminate=lambda: pytest.fail("unowned process must not be signalled"),
        kill=lambda: pytest.fail("unowned process must not be killed"),
    )
    monkeypatch.setattr(staging, "_start_token", lambda pid: current)
    with pytest.raises(staging._CleanupUnconfirmed, match="identity lost"):
        staging._terminate_supervisor(process, "original-token", "probe")


def test_reaped_supervisor_never_signalled(monkeypatch):
    process = SimpleNamespace(
        pid=123, returncode=0, poll=lambda: 0,
        terminate=lambda: pytest.fail("reaped leader must not be signalled"),
    )
    monkeypatch.setattr(staging, "_start_token", lambda pid: pytest.fail("identity is gone"))
    staging._terminate_supervisor(process, "original-token", "probe")


@pytest.mark.parametrize("tool", ["python", "uv"])
def test_tool_replaced_during_build_withholds_completion(case, monkeypatch, tool):
    original = staging._run

    def replace(argv, **kwargs):
        output = original(argv, **kwargs)
        if "-c" in argv:
            getattr(case.build, tool).write_bytes(b"replacement")
        return output

    monkeypatch.setattr(staging, "_run", replace)
    with pytest.raises(CandidateError, match="tool changed during"):
        stage(case)
    assert not (case.root / "versions" / "0.1.1.dev1").exists()


def _cleanup_case(case):
    slot = case.root / "versions" / "0.1.1.dev1"
    target = slot / "build-state"
    target.mkdir(parents=True)
    (target / "payload").write_bytes(b"cache")
    owner = {"token": "owned-token"}
    (slot / ".stage-owner.json").write_text(json.dumps(owner))
    return slot, target, owner


@pytest.mark.parametrize("winerror", [5, 32, 145])
def test_transient_owned_cleanup_retries(case, monkeypatch, winerror):
    slot, target, owner = _cleanup_case(case)
    real = staging.shutil.rmtree
    attempts = []

    def transient(path):
        attempts.append(path)
        if len(attempts) == 1:
            error = OSError("transient")
            error.winerror = winerror
            raise error
        return real(path)

    monkeypatch.setattr(staging.shutil, "rmtree", transient)
    staging._remove_owned_tree(target, slot=slot, owner=owner, primitive=case.primitive,
                               root=case.root, version="0.1.1.dev1")
    assert len(attempts) == 2
    assert not target.exists()
    assert (slot / ".stage-owner.json").exists()


@pytest.mark.parametrize("winerror", [2, 5, 32, 145])
def test_permanent_owned_cleanup_failure_preserves_candidate(case, monkeypatch, winerror):
    slot, target, owner = _cleanup_case(case)
    ticks = iter([0.0, 10.0])
    monkeypatch.setattr(staging, "time", SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda value: None,
    ))

    def fail(path):
        error = OSError("permanent")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(staging.shutil, "rmtree", fail)
    with pytest.raises(staging._CleanupUnconfirmed, match="cleanup failed"):
        staging._remove_owned_tree(target, slot=slot, owner=owner, primitive=case.primitive,
                                   root=case.root, version="0.1.1.dev1")
    assert target.exists()
    assert (slot / ".stage-owner.json").exists()


def test_scratch_cleanup_failure_blocks_receipt_and_completion(case, monkeypatch):
    original = staging._remove_owned_tree

    def fail(target, **kwargs):
        if target.name == "build-state":
            raise staging._CleanupUnconfirmed("scratch cleanup failed")
        return original(target, **kwargs)

    monkeypatch.setattr(staging, "_remove_owned_tree", fail)
    with pytest.raises(CandidateError, match="scratch cleanup failed"):
        stage(case)
    slot = case.root / "versions" / "0.1.1.dev1"
    assert slot.exists()
    assert not (slot / "candidate.json").exists()
    assert not (slot / ".install-complete.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length cleanup")
@pytest.mark.parametrize("kind", ["scratch", "failed-slot"])
def test_real_deep_windows_owned_cache_cleanup(case, kind):
    slot, target, owner = _cleanup_case(case)
    deep = target
    for index in range(12):
        deep /= f"long-cache-segment-{index:02d}"
    os.makedirs(staging._long_path(deep))
    with open(staging._long_path(deep / "wheel.bin"), "wb") as stream:
        stream.write(b"cache")
    assert len(str(deep)) > 260
    staging._remove_owned_tree(target if kind == "scratch" else slot,
                               slot=slot, owner=owner, primitive=case.primitive,
                               root=case.root, version="0.1.1.dev1")
    assert not target.exists()
    assert (slot / ".stage-owner.json").exists() is (kind == "scratch")
