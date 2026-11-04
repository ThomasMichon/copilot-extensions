"""Offline wheel consumers; no SSH process or operator configuration is used."""

from pathlib import Path
import shutil
import sys
import zipfile

from agent_procutil.owned_process import run_caller_owned
import pytest


@pytest.mark.parametrize("component", ["cli", "manager"])
def test_copied_wheel_preserves_the_cli_and_shared_ssh_resources(tmp_path, component):
    repo = Path(__file__).resolve().parents[3]
    source = (
        repo / "plugins" / "agent-ssh"
        if component == "cli" else repo / "libs" / "ssh-manager"
    )
    project, output = tmp_path / "project", tmp_path / "wheels"
    project.mkdir()
    output.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(source / name, project / name)
    shutil.copytree(
        source / "src", project / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
    )
    built = run_caller_owned(
        [sys.executable, "-I", "-c",
         "from setuptools.build_meta import build_wheel; "
         f"build_wheel({str(output)!r})"],
        cwd=project, timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheel = next(output.glob("*.whl"))
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        entries = [
            name for name in archive.namelist()
            if name.endswith(".dist-info/entry_points.txt")
        ]
        if component == "cli":
            entry_points = archive.read(entries[0]).decode("utf-8")
            assert "agent-ssh = agent_ssh.__main__:main" in entry_points
        else:
            assert not entries, "SSH Manager must remain a library, not a separate app"
        archive.extractall(installed)

    script = """
import importlib.resources, pathlib, sys
installed, component = sys.argv[1:]
sys.path.insert(0, installed)
if component == 'cli':
    import agent_ssh.__main__ as cli
    assert pathlib.Path(cli.__file__).is_relative_to(installed)
    cli.main(['--version'])
else:
    import ssh_manager
    from ssh_manager import proxy
    from ssh_manager.config_sources import SSHConfig
    assert pathlib.Path(ssh_manager.__file__).is_relative_to(installed)
    resources = importlib.resources.files('ssh_manager')
    for name in ('proxy_client.py', 'carrier_server.py'):
        assert resources.joinpath(name).read_bytes()
    assert pathlib.Path(proxy.__file__).with_name('proxy_client.py').is_file()
    config = SSHConfig(host_alias='fixture', user='owner',
                       config_file=str(pathlib.Path.cwd() / 'selected.config'))
    args = ssh_manager.build_forward_ssh_args(
        config, 24001, 24002, reverse_forwards=['24003:127.0.0.1:24004'])
    assert args[args.index('-F') + 1] == config.config_file
    assert args[args.index('-L') + 1] == '127.0.0.1:24001:127.0.0.1:24002'
    assert args[args.index('-R') + 1] == '24003:127.0.0.1:24004'
    assert args[-1] == 'owner@fixture'
"""
    child = run_caller_owned(
        [sys.executable, "-I", "-c", script, str(installed), component],
        cwd=tmp_path, timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert child.returncode == 0, child.stdout + child.stderr
