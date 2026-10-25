from email.parser import Parser
import json
from pathlib import Path
import shutil
import sys
import tarfile
import zipfile

import pytest

from agent_ssh_service import __version__
from agent_procutil.owned_process import run_caller_owned
from fleet_manager.komodo_roles import KomodoRoleProvider
from fleet_manager.role_bindings import parse_bindings


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_metadata_does_not_import_the_runtime_backend(flag):
    script = """
import builtins,runpy,sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'asyncio','sqlite3','subprocess','fleet_manager','single_instance_lease'}:
        raise AssertionError('runtime dependency imported for metadata')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
sys.argv=['agent-ssh-service',sys.argv[1]]
runpy.run_module('agent_ssh_service',run_name='__main__')
"""
    result = run_caller_owned(
        [sys.executable, "-I", "-c", script, flag],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    if flag == "--help":
        assert "--config" in result.stdout
    else:
        assert result.stdout.strip() == __version__


def test_offline_wheel_and_sdist_do_not_require_a_checkout_or_marketplace(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1]
    project, output = tmp_path / "project", tmp_path / "artifacts"
    project.mkdir()
    output.mkdir()
    for name in ("pyproject.toml", "README.md", "MANIFEST.in"):
        shutil.copy2(source / name, project / name)
    shutil.copytree(source / "container", project / "container")
    shutil.copytree(source / "src", project / "src",
                    ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"))
    monkeypatch.chdir(project)
    result = run_caller_owned(
        [sys.executable, "-I", "-c",
         "from setuptools.build_meta import build_wheel,build_sdist; "
         f"build_wheel({str(output)!r});build_sdist({str(output)!r})"],
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(output.glob("*.whl"))) as wheel:
        metadata = Parser().parsestr(wheel.read(next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )).decode())
        assert metadata["Name"] == "agent-ssh-service"
        assert not any("@" in item or "file:" in item
                       for item in metadata.get_all("Requires-Dist"))
        assert "agent_ssh_service/hosting.py" in wheel.namelist()
        assert "agent_ssh_service/relay.py" in wheel.namelist()
        assert "agent-procutil" in metadata.get_all("Requires-Dist")
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        assert any(name.endswith("/container/Dockerfile") for name in archive.getnames())
    monkeypatch.chdir(tmp_path)
    child = run_caller_owned(
        [sys.executable, "-I", "-m", "agent_ssh_service", "--help"],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert child.returncode == 0 and "--config" in child.stdout


def test_container_is_nonroot_offline_and_uses_existing_role_provider():
    source = Path(__file__).resolve().parents[1]
    recipe = (source / "container" / "Dockerfile").read_text("utf-8")
    assert recipe.startswith("ARG PYTHON_BASE\nFROM ${PYTHON_BASE}")
    assert "--no-index" in recipe and "--require-hashes" in recipe
    assert "--only-binary=:all:" in recipe and "USER 65532:65532" in recipe
    assert "STOPSIGNAL SIGTERM" in recipe and "EXPOSE" not in recipe
    assert "ARG NATIVE_RELAY=false" in recipe and "/usr/sbin/sshd" in recipe
    assert [line for line in recipe.splitlines() if line.startswith("COPY ")] == [
        "COPY wheels/ /tmp/ssh-wheels/", "COPY requirements.lock /tmp/ssh-requirements.lock",
    ]
    providers, bindings = parse_bindings(json.loads(
        (source / "container" / "role-bindings.example.json").read_text("utf-8"),
    ))
    selected = KomodoRoleProvider().project(providers[0], bindings[0])
    assert selected.artifact_ref == "approved-ssh-service-image"
    assert bindings[0].implementation == "container.service"
