"""Artifact delivery controls, not selected-installation or service readiness."""

import shutil
import sys

import pytest

from agent_procutil.owned_process import run_caller_owned


@pytest.mark.parametrize("missing", [
    "_standalone_runtime_build.py",
    "src/agent_chat_service/versioned_runtime_publication.py",
    "src/agent_chat_service/installer_dependency_config.py",
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
for name in ("versioned_runtime_source","installer_engine_process","installer_dependency_policy"):
    assert Path(sys.modules[name].__file__).parent == root
policy=sys.modules["installer_dependency_policy"]
assert Path(policy._configuration_path).parent == root
assert callable(engine.numbered_candidate)
assert callable(runtime.remove_owned_candidate)
print(json.dumps({"construction":"ready","artifact_contained":True}))
"""
    import json

    result = json.loads(installed_chat.run(
        [str(installed_chat.core_python), "-I", "-B", "-c", probe], cwd=tmp_path,
    ))
    assert result == (
        {"construction": "requires-python-3.11"} if sys.version_info < (3, 11)
        else {"construction": "ready", "artifact_contained": True}
    )
