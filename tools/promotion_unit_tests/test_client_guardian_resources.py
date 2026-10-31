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

from test_client_diagnostics import installed as installed  # noqa: F401
from test_client_diagnostics import installed_procutil
from test_client_diagnostics import installed_runtime


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


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("member", [
    "_construction_procutil/__init__.py", "_construction_procutil/_job_scopes.py",
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


@pytest.mark.parametrize("fault", ["missing", "extra", "text", "empty", "oversized"])
def test_guardian_refuses_invalid_closure_without_read_or_execution(monkeypatch, fault):
    path = Path(__file__).parents[2] / "libs/installer-engine/installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("fixture_guardian", path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    sources = {name: b"raise AssertionError('invalid closure executed')\n" for name in owner._GUARDIAN_MEMBERS}
    if fault == "missing":
        sources.pop("_construction_procutil/_job_scopes.py")
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
    "missing", "extra", "bad-base64", "empty", "oversized", "nonstring", "duplicate",
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
