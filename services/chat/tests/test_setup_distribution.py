"""Installed construction controls, not selected-installation or service readiness."""

import json
import shutil
import sys

import pytest

from agent_procutil.owned_process import run_caller_owned


@pytest.mark.parametrize("missing", [
    "_standalone_runtime_build.py",
    "src/agent_chat_service/versioned_runtime_publication.py",
    "src/agent_chat_service/installer_dependency_config.py",
    "src/agent_chat_service/installer_wheel_inputs.py",
    "src/agent_chat_service/installer_wheel_snapshot.py",
])
def test_independent_sdist_refuses_missing_construction_input(tmp_path, installed_chat, missing):
    shutil.unpack_archive(installed_chat.sdist, tmp_path, "gztar")
    project, = tmp_path.iterdir()
    project.joinpath(*missing.split("/")).unlink()
    result = run_caller_owned(
        [
            sys.executable, "-I", "-c",
            "import os,sys; os.environ.pop('PYTHONPATH',None); os.chdir(sys.argv[1]); "
            "from setuptools.build_meta import build_wheel; build_wheel(sys.argv[2])",
            str(project), str(tmp_path / "wheels"),
        ],
        timeout=30,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    assert result.returncode != 0
    assert not list((tmp_path / "wheels").glob("*.whl"))
    assert "missing" in result.stderr or "FileNotFoundError" in result.stderr


def test_installed_constructor_preserves_the_separate_bootstrap_floor(tmp_path, installed_chat):
    probe = """
import importlib,json,sys
from pathlib import Path
import agent_chat_service as chat
root=Path(chat.__file__).parent
runtime=importlib.import_module("agent_chat_service._versioned_runtime")
sys.modules["versioned_runtime"]=runtime
sys.path.insert(0,str(root))
if sys.version_info < (3,11):
    try:
        import installer_engine
    except ModuleNotFoundError as error:
        assert error.name=="tomllib"
    else:
        raise AssertionError("canonical constructor unexpectedly supports this bootstrap")
    print(json.dumps({"construction":"requires-python-3.11"}))
    raise SystemExit(0)
import installer_engine as engine
assert Path(engine.__file__).parent == root
assert engine.runtime is runtime
for name in ("versioned_runtime_source","installer_engine_process","installer_dependency_policy",
             "installer_wheel_inputs","installer_wheel_snapshot"):
    assert Path(sys.modules[name].__file__).parent == root
policy=sys.modules["installer_dependency_policy"]
assert Path(policy._configuration_path).parent == root
assert callable(engine.numbered_candidate)
assert callable(runtime.remove_owned_candidate)
print(json.dumps({"construction":"ready","artifact_contained":True}))
"""
    result = json.loads(installed_chat.run(
        [str(installed_chat.core_python), "-I", "-B", "-c", probe], cwd=tmp_path,
    ))
    assert result == (
        {"construction": "requires-python-3.11"} if sys.version_info < (3, 11)
        else {"construction": "ready", "artifact_contained": True}
    )


def test_installed_construction_refuses_foreign_imports_without_mutation(
    tmp_path, installed_chat,
):
    probe = """
import json,sys
from types import ModuleType
from agent_chat_service.client import ClientError
from agent_chat_service.construction import _engine
names=("versioned_runtime","versioned_runtime_source",
       "installer_dependency_policy","installer_engine_process",
       "installer_wheel_inputs","installer_wheel_snapshot")
for name in names:
    foreign=ModuleType(name)
    sys.modules[name]=foreign
    try:
        _engine()
    except ClientError as error:
        expected="construction_python_unavailable" if sys.version_info<(3,11) else (
            "construction_import_conflict"
        )
        assert error.code==expected
    else:
        raise AssertionError("foreign construction resource was accepted")
    assert sys.modules[name] is foreign
    assert not any("agent_chat_service."+item in sys.modules for item in names[1:])
    del sys.modules[name]
print(json.dumps({"foreign_imports_preserved":len(names)}))
"""
    assert json.loads(installed_chat.run(
        [str(installed_chat.core_python), "-I", "-B", "-c", probe], cwd=tmp_path,
    )) == {"foreign_imports_preserved": 6}


def test_installed_constructor_builds_chat_client_from_independent_inputs(
    tmp_path, installed_chat, chat_library_sdists,
):
    inputs = tmp_path / "inputs"
    project = inputs / "services" / "chat"
    for destination, archive in (
        (project, installed_chat.sdist),
        *((inputs / "libs" / name, archive)
          for name, archive in chat_library_sdists.items()),
    ):
        unpacked = tmp_path / "unpack"
        unpacked.mkdir()
        shutil.unpack_archive(archive, unpacked, "gztar")
        extracted, = unpacked.iterdir()
        destination.parent.mkdir(parents=True, exist_ok=True)
        extracted.rename(destination)
        unpacked.rmdir()
    uv = shutil.which("uv")
    assert uv is not None
    probe = """
import json,os,shutil,subprocess,sys
from pathlib import Path
import agent_chat_service as chat
from agent_chat_service.client import ClientError
from agent_chat_service.construction import client_candidate
resources=Path(chat.__file__).parent
project,root,uv=map(Path,sys.argv[1:])
if sys.version_info < (3,11):
    try:
        with client_candidate(root,"0.1.0+client",project=project,uv=uv,python=Path(sys.executable)):
            raise AssertionError("unsupported construction was admitted")
    except ClientError as error:
        assert error.code=="construction_python_unavailable"
    else:
        raise AssertionError("canonical constructor unexpectedly supports this bootstrap")
    assert not root.exists()
    print(json.dumps({"construction":"requires-python-3.11"}))
    raise SystemExit(0)
from agent_chat_service.construction import _engine
engine=_engine()
runtime=engine.runtime
assert Path(engine.__file__).parent==resources
assert not any(name in sys.modules for name in (
    "versioned_runtime","versioned_runtime_source",
    "installer_dependency_policy","installer_engine_process",
    "installer_wheel_inputs","installer_wheel_snapshot",
))
assert not (project.parents[1]/"libs"/"versioned-runtime").exists()
assert not (project.parents[1]/"libs"/"installer-engine").exists()
expected=("agent-chat-service","agent-endpoint-rendezvous","agent-procutil")
arguments=dict(
    project=project,uv=uv,python=Path(sys.executable),
    child_timeout=60,
)
with client_candidate(root,"0.1.0+client",**arguments) as candidate:
    assert not candidate.reused
    assert candidate.extras==("client",) and candidate.local_packages==expected
    receipt=json.loads((candidate.slot/".runtime-input-origins.json").read_text())
    assert receipt["extras"]==["client"]
    assert receipt["local_packages"]==list(expected)
    assert set(receipt["packages"])==set(expected)
    assert runtime.is_complete(root,candidate.slot.name,expect_hash=candidate.payload_hash)
    assert len(engine._package_roots(candidate.frozen_plugin))==4
    assert (candidate.frozen_plugin/"_standalone_runtime_build.py").is_file()
    frozen=candidate.frozen_plugin
assert not frozen.exists()
assert runtime.current_version(root) is None
with client_candidate(root,"0.1.0+client",**{
    **arguments,"uv":root/"absent-uv","python":root/"absent-python",
}) as reused:
    assert reused.reused and reused.local_packages==expected
assert runtime.current_version(root) is None
shutil.rmtree(project.parents[1])
python=runtime.slot_python(root,candidate.slot.name)
command=[str(python),"-I","-S","-B",
         str(candidate.slot/".runtime-bootstrap"/"runtime_launcher.py"),
         "-m","agent_chat_service.cli","catalog"]
result=subprocess.run(command,capture_output=True,text=True,timeout=30,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
assert result.returncode==0,result.stdout+result.stderr
catalog=json.loads(result.stdout)
assert catalog["evidence"]=="local-installed-resource-record"
assert not catalog["release_selection_verified"]
assert not (candidate.slot/".runtime-bootstrap"/"__pycache__").exists()
refusal=subprocess.run([
    str(python),"-I","-B","-c",
    "import json,sys; from agent_chat_service.client import ClientError; "
    "from agent_chat_service.construction import _prepare_client; "
    "data={'slot':sys.prefix,'frozen_plugin':'absent'}; "
    "\\ntry: _prepare_client(data)"
    "\\nexcept ClientError as error: assert error.code=='construction_slot_complete'"
    "\\nelse: raise AssertionError('completed slot was prepared')",
],capture_output=True,text=True,timeout=30,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
assert refusal.returncode==0,refusal.stdout+refusal.stderr
print(json.dumps({"construction":"complete","reused":True,"selected":False}))
"""
    result = json.loads(installed_chat.run(
        [
            str(installed_chat.core_python), "-I", "-B", "-c", probe,
            str(project), str(tmp_path / "runtime"), uv,
        ], cwd=tmp_path, timeout=180,
    ))
    assert result == (
        {"construction": "requires-python-3.11"} if sys.version_info < (3, 11)
        else {"construction": "complete", "reused": True, "selected": False}
    )
