"""The retained client pair survives the actual guardian launch boundary."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_source(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


if "fleet_manager" not in sys.modules:
    _load_source("fleet_manager", ROOT / "fleet-manager/src/fleet_manager/__init__.py")
_diagnostics = _load_source(
    "promotion_original_client_diagnostics",
    ROOT / "fleet-manager/tests/test_client_diagnostics.py",
)
installed = _diagnostics.installed
installed_procutil = _diagnostics.installed_procutil
installed_runtime = _diagnostics.installed_runtime
test_client_resource_preparation_consumes_shared_startup_deadline = (
    _diagnostics.test_client_resource_preparation_consumes_shared_startup_deadline
)


class _ValueStructure:
    pass


def _inert_native_import() -> ModuleType:
    module = ModuleType("ctypes")
    module.__file__ = None
    module.Structure = _ValueStructure
    module.c_int64, module.c_uint32 = object(), object()

    def native_access(name: str) -> object:
        raise AssertionError(f"native ctypes API is outside this pure unit: {name}")

    module.__getattr__ = native_access
    return module


def test_accounting_resource_defers_native_binding_and_reuses_one_layout(monkeypatch):
    class NoNativeImport(ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"portable resource accessed native bindings: {name}")

    monkeypatch.setitem(sys.modules, "ctypes", NoNativeImport("ctypes"))
    monkeypatch.setitem(sys.modules, "promotion_lazy_accounting_fixture", ModuleType(
        "promotion_lazy_accounting_fixture",
    ))
    module = _load_source(
        "promotion_lazy_accounting_fixture",
        ROOT / "libs/agent-procutil/src/agent_procutil/_windows_job_types.py",
    )
    assert "_JobBasicAccountingInformation" not in vars(module)
    with pytest.raises(AttributeError):
        module.unknown_accounting_type
    monkeypatch.setitem(sys.modules, "ctypes", _inert_native_import())
    accounting = module._JobBasicAccountingInformation
    assert module._JobBasicAccountingInformation is accounting
    assert vars(module)["_JobBasicAccountingInformation"] is accounting
    assert len(accounting._fields_) == 8


@pytest.mark.parametrize("failed", [False, True])
def test_retained_streaming_packet_writer_settles_without_reopening_sources(
    installed, monkeypatch, failed, _canonical=False,
):
    construction, _, evidence = installed_procutil(installed, monkeypatch)

    class Thread:
        def __init__(self, target, args=(), **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

        def join(self, _timeout):
            pass

        def is_alive(self):
            return False

    class Input(io.BytesIO):
        written = None

        def write(self, data):
            if failed:
                raise OSError("fixture packet write failed")
            return super().write(data[:2])

        def close(self):
            self.written = self.getvalue()
            super().close()

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def register(self, *_args):
            pass

        def select(self, _timeout):
            return [("fixture-readable", 1)]

    process = SimpleNamespace(
        args=["fixture"], stdin=Input(), stdout=io.BytesIO(b"out"),
        stderr=io.BytesIO(b"err"), poll=lambda: 0, text_mode=False,
    )
    with construction._procutil_resource(evidence) as package, monkeypatch.context() as modules:
        streaming = sys.modules[package.__name__ + ".streaming"]
        if _canonical:
            name = package.__name__ + ".measured_streaming"
            modules.setitem(sys.modules, name, ModuleType(name))
            streaming = _load_source(
                name, ROOT / "libs/agent-procutil/src/agent_procutil/streaming.py",
            )
        monkeypatch.setattr(streaming.threading, "Thread", Thread)
        monkeypatch.setattr(streaming.selectors, "DefaultSelector", Selector)
        capture = streaming.SyncProcessOutput(process, input_bytes=b"complete packet")
        if failed:
            with pytest.raises(OSError, match="fixture packet write failed"):
                capture.wait(1)
        else:
            capture.wait(1)
        assert capture.finish(1) == (b"out", b"err")
        assert process.stdin.closed
        assert process.stdin.written == (b"" if failed else b"complete packet")
        assert capture._finished


def test_retained_relay_launch_uses_the_verified_bytes_not_its_file(
    installed, monkeypatch, _canonical=False,
):
    construction, _, evidence = installed_procutil(installed, monkeypatch)
    calls = []

    def launch(argv, **options):
        calls.append((argv, options))
        raise RuntimeError("fixture launch stopped before native execution")

    with construction._procutil_resource(evidence) as package, monkeypatch.context() as modules:
        relay = sys.modules[package.__name__ + "._output_relay"]
        retained = relay._retained_source_bytes
        if _canonical:
            name = package.__name__ + ".measured_relay"
            modules.setitem(sys.modules, name, ModuleType(name))
            relay = _load_source(
                name, ROOT / "libs/agent-procutil/src/agent_procutil/_output_relay.py",
            )
            relay._retained_source_bytes = retained
        monkeypatch.setattr(package, "spawn_sync_in_kill_on_close_job", launch)
        monkeypatch.setattr(relay, "Path", lambda *_: pytest.fail("reopened relay path"))
        with pytest.raises(RuntimeError, match="fixture launch stopped"):
            relay.ByteRelay(3)
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[1:4] == ["-I", "-B", "-c"]
    assert base64.b64encode(retained).decode("ascii") in argv[4]
    assert argv[5] == "--copy"
    assert options["allow_breakaway"] is False


@pytest.mark.parametrize("member", ["streaming", "relay"])
def test_canonical_streaming_implementations_are_measured(installed, monkeypatch, member):
    if member == "streaming":
        for failed in (False, True):
            test_retained_streaming_packet_writer_settles_without_reopening_sources(
                installed, monkeypatch, failed, _canonical=True,
            )
    else:
        test_retained_relay_launch_uses_the_verified_bytes_not_its_file(
            installed, monkeypatch, _canonical=True,
        )


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("failed", [False, True])
def test_construction_owner_call_keeps_pair_available_and_preserves_failure_custody(
    installed, monkeypatch, tmp_path, failed, platform,
):
    monkeypatch.setitem(sys.modules, "ctypes", _inert_native_import())
    _diagnostics.test_construction_owner_call_keeps_pair_available_and_preserves_failure_custody(
        installed, monkeypatch, tmp_path, failed, platform,
    )


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("member", [
    "_construction_procutil/__init__.py", "_construction_procutil/_job_scopes.py",
    "_construction_procutil/_windows_job_types.py",
    "_construction_procutil/streaming.py", "_construction_procutil/_output_relay.py",
    "_versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_source.py", "versioned_runtime_publication.py", "_installer_process.py",
])
def test_guardian_receives_admitted_pair_after_disk_replacement(
    installed, monkeypatch, tmp_path, member, failed,
):
    construction, package, evidence = installed_procutil(installed, monkeypatch)
    installed_runtime(installed, monkeypatch)
    evidence = construction.artifact_staging.diagnose_client()
    path = Path(__file__).parents[2] / "libs/installer-engine/installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("fixture_guardian", path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    process_bytes = path.read_bytes()
    owner._retained_source_bytes = process_bytes
    (package / "_installer_process.py").write_bytes(process_bytes)
    original = construction.artifact_staging._bytes
    observed = []
    failure = RuntimeError("native ownership is unconfirmed")
    failure.process, failure.job = object(), object()
    custody = failure.process, failure.job

    def replaced(path, limit):
        content = original(path, limit)
        if path == package / member:
            path.write_text("raise AssertionError('late unadmitted guardian code executed')\n")
        return content

    class Guardian:
        returncode = 0
        stdin = None

        def __init__(self, argv, **options):
            assert "--procutil-path" not in argv
            assert options["stdin"] == subprocess.PIPE
            assert options["close_fds"] is True
            assert len(options["startupinfo"].lpAttributeList["handle_list"]) == 1
            observed.append(argv)
            self.bootstrap = argv[argv.index("-c") + 1]
            self.completed = False

        def communicate(self, *, input=None, timeout):
            if not self.completed:
                assert isinstance(input, str)
                before = set(sys.modules)
                namespace = {"__name__": "fixture_retained_guardian"}
                with monkeypatch.context() as stdio:
                    stdio.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(input.encode("ascii"))))
                    exec(compile(self.bootstrap, "<guardian-bootstrap>", "exec"), namespace)
                with namespace["_load_guardian_resources"](namespace["_guardian_sources"]) as (runtime, module):
                    assert module.__path__ == []
                    assert module._job_child_environment({}, inherit_current=False) == {}
                    assert module.__name__ in sys.modules
                    accounting_module = sys.modules[module.__name__ + "._windows_job_types"]
                    assert "_JobBasicAccountingInformation" not in vars(accounting_module)
                    # The native attribute resolves from retained bytes, never
                    # from the substituted file or an ambient package.
                    accounting = module._JobBasicAccountingInformation
                    assert accounting is accounting_module._JobBasicAccountingInformation
                    assert [name for name, _ in accounting._fields_] == [
                        "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
                        "ThisPeriodTotalKernelTime", "TotalPageFaultCount", "TotalProcesses",
                        "ActiveProcesses", "TotalTerminatedProcesses",
                    ]
                    assert runtime._runtime_component("versioned_runtime_leases").__name__ in sys.modules
                assert set(sys.modules) == before
                self.completed = True
                if failed:
                    raise failure
            else:
                assert input is None
            return json.dumps({
                "returncode": 0, "stdout": "complete", "stderr": "",
                "timed_out": False, "launch_error": None,
            }), ""

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda value: value))
    monkeypatch.setitem(sys.modules, "ctypes", _inert_native_import())
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        **{**vars(os), "name": "nt", "set_handle_inheritable": lambda *_: None},
    ))
    monkeypatch.setattr(construction, "os", owner.os)
    monkeypatch.setattr(owner.subprocess, "STARTUPINFO", SimpleNamespace, raising=False)
    monkeypatch.setattr(owner.subprocess, "Popen", Guardian)
    monkeypatch.setattr(construction.artifact_staging, "_bytes", replaced)
    def process_resource(name, *, evidence):
        assert name == "_installer_process.py"
        _path, content = construction._process_resource_data(name, evidence=evidence)
        assert content == process_bytes
        return owner

    installed[0].files = [
        entry for entry in installed[0].files if str(entry) != "fleet_manager/_installer_process.py"
    ] + [type(installed[0].files[0])("fleet_manager/_installer_process.py", process_bytes)]
    evidence = construction.artifact_staging.diagnose_client()
    monkeypatch.setattr(construction, "_process_resource", process_resource)
    slot = tmp_path / "runtime" / "versions" / "fixture"
    slot.mkdir(parents=True)
    if failed:
        with pytest.raises(RuntimeError) as caught:
            construction._run(["fixture"], slot, "fixture", descriptor=1, resource_evidence=evidence)
        assert caught.value is failure
        assert (failure.process, failure.job) == custody
    else:
        assert construction._run(
            ["fixture"], slot, "fixture", descriptor=1, resource_evidence=evidence,
        ) == b"complete"
    assert len(observed) == 1


@pytest.mark.parametrize("fault", ["missing", "missing-accounting", "extra", "text", "empty", "oversized"])
def test_guardian_refuses_invalid_closure_without_read_or_execution(monkeypatch, fault):
    path = Path(__file__).parents[2] / "libs/installer-engine/installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("fixture_guardian", path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    sources = {name: b"raise AssertionError('invalid closure executed')\n" for name in owner._GUARDIAN_MEMBERS}
    if fault == "missing":
        sources.pop("_construction_procutil/_job_scopes.py")
    elif fault == "missing-accounting":
        sources.pop("_construction_procutil/_windows_job_types.py")
    elif fault == "extra":
        sources["extra.py"] = b"extra"
    elif fault == "text":
        sources["_installer_process.py"] = "text"
    elif fault == "empty":
        sources["_installer_process.py"] = b""
    else:
        sources["_installer_process.py"] = b"x" * (owner._GUARDIAN_SOURCE_LIMIT + 1)
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: pytest.fail("provided closure reopened a file"))
    with pytest.raises(ValueError, match="exact bounded source closure"):
        owner._guardian_packet(None, None, sources)
    before = set(sys.modules)
    with pytest.raises(ValueError, match="exact bounded source closure"):
        with owner._load_guardian_resources(sources):
            pytest.fail("invalid closure was admitted")
    assert set(sys.modules) == before


@pytest.mark.parametrize("fault", [
    "missing", "missing-accounting", "extra", "bad-base64", "empty", "oversized", "nonstring", "duplicate",
])
def test_bootstrap_rejects_invalid_complete_packet_before_executing_owner(monkeypatch, fault):
    path = Path(__file__).parents[2] / "libs/installer-engine/installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("fixture_guardian", path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    encoded = {
        name: base64.b64encode(b"raise AssertionError('invalid closure executed')\n").decode("ascii")
        for name in owner._GUARDIAN_MEMBERS
    }
    if fault == "missing":
        encoded.pop("_construction_procutil/_job_scopes.py")
    elif fault == "missing-accounting":
        encoded.pop("_construction_procutil/_windows_job_types.py")
    elif fault == "extra":
        encoded["extra.py"] = encoded["_installer_process.py"]
    elif fault == "bad-base64":
        encoded["_installer_process.py"] = "?"
    elif fault == "empty":
        encoded["_installer_process.py"] = ""
    elif fault == "oversized":
        encoded["_installer_process.py"] = base64.b64encode(b"x" * (owner._GUARDIAN_SOURCE_LIMIT + 1)).decode("ascii")
    elif fault == "nonstring":
        encoded["_installer_process.py"] = 1
    packet = json.dumps(encoded)
    if fault == "duplicate":
        packet = packet[:-1] + ', "_installer_process.py": ' + json.dumps(encoded["_installer_process.py"]) + "}"
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(packet.encode("ascii"))))
    with pytest.raises(ValueError):
        exec(compile(owner._GUARDIAN_BOOTSTRAP, "<guardian-bootstrap>", "exec"), {"__name__": "fixture"})


@pytest.mark.parametrize("boundary", ["spawn", "completion"])
def test_guardian_cannot_report_success_after_requested_execution_limit(monkeypatch, boundary):
    path = ROOT / "libs/installer-engine/installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("fixture_guardian", path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    clock = {"now": 0.0}
    observed = []

    def communicate(*, timeout):
        if boundary == "completion":
            clock["now"] = 2.0
        return "complete", ""

    process = SimpleNamespace(returncode=0, communicate=communicate, wait=lambda **_: None)
    job = SimpleNamespace(terminate_and_wait=lambda timeout: observed.append(("retire", timeout)))

    def spawn(*_args, **_kwargs):
        if boundary == "spawn":
            clock["now"] = 2.0
        return process, job

    procutil = SimpleNamespace(
        spawn_sync_in_kill_on_close_job=spawn, no_window_kwargs=lambda: {},
        SyncProcessLaunchError=RuntimeError,
    )
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    outcome = owner._run_guardian_child(
        SimpleNamespace(timeout=1.0), ["fixture"], procutil, lambda: False, None,
        {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True, "launch_error": None},
    )
    assert outcome["timed_out"] is True
    assert observed == [("retire", 10)]


@pytest.mark.parametrize("deadline", [None, 2.0])
@pytest.mark.parametrize("boundary", ["packet", "startup", "completion", "retirement"])
def test_retained_guardian_preserves_deadline_before_launch_and_after_settlement(
    monkeypatch, tmp_path, boundary, deadline,
):
    path = ROOT / "libs/installer-engine/installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("fixture_guardian", path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    clock = {"now": 0.0}
    launches, inheritance = [], []

    def packet(*_args):
        if boundary == "packet":
            clock["now"] = 3.0
        return "{}"

    def startup():
        if boundary == "startup":
            clock["now"] = 3.0
        return SimpleNamespace()

    class Guardian:
        returncode = 0

        def __init__(self, argv, **_options):
            self.stdin = None
            assert argv[argv.index("--deadline") + 1] == "2.0"
            launches.append(argv)
            self.calls = 0

        def communicate(self, *, input=None, timeout):
            self.calls += 1
            assert 0 < timeout <= 32
            if boundary == "completion" or boundary == "retirement" and self.calls == 2:
                clock["now"] = 3.0
            return json.dumps({
                "returncode": 0, "stdout": "complete", "stderr": "",
                "timed_out": False, "launch_error": None,
            }), ""

    monkeypatch.setattr(owner, "_guardian_packet", packet)
    monkeypatch.setattr(owner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda value: value))
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        **{**vars(os), "set_handle_inheritable": lambda _handle, flag: inheritance.append(flag)},
    ))
    monkeypatch.setattr(owner.subprocess, "STARTUPINFO", startup, raising=False)
    monkeypatch.setattr(owner.subprocess, "Popen", Guardian)
    with pytest.raises(owner.ChildTimeoutExpired) as caught:
        owner._retained_windows(
            ["fixture"], cwd=tmp_path, env={}, timeout=2.0,
            procutil=SimpleNamespace(no_window_kwargs=lambda: {}),
            root=tmp_path, version="fixture", runtime_path=tmp_path / "runtime.py",
            deadline=deadline,
        )
    assert caught.value.output == ("" if boundary in ("packet", "startup") else "complete")
    assert len(launches) == (0 if boundary in ("packet", "startup") else 1)
    assert inheritance == [True, False]
