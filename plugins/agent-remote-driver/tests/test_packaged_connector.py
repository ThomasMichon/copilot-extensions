"""The installed connector must not retain a source-checkout dependency."""

from __future__ import annotations

import os
import json
from pathlib import Path
import runpy
import shutil
import sys
import tarfile
import time
import zipfile

from agent_procutil.owned_process import run_caller_owned


ROOT = Path(__file__).resolve().parents[3]


def test_sdist_builds_complete_connector_wheel_without_checkout(tmp_path: Path) -> None:
    source = ROOT / "plugins/agent-remote-driver"
    helper = ROOT / "libs/versioned-runtime/standalone_build.py"
    selected = runpy.run_path(str(helper))["application_resources"](
        source, "agent_remote_driver_app",
    )
    expected = {name: path.read_bytes() for name, path in selected.items()}
    project = tmp_path / "source/plugins/agent-remote-driver"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "plugin.json", "setup.py"):
        shutil.copyfile(source / name, project / name)
    shutil.copytree(source / "src", project / "src")
    shutil.copytree(source / "extensions", project / "extensions")
    canonical = project.parents[1] / "libs/versioned-runtime/standalone_build.py"
    canonical.parent.mkdir(parents=True)
    shutil.copyfile(helper, canonical)
    for path in selected.values():
        assert path is not None
        path = path.resolve()
        if path.is_relative_to(ROOT / "libs/copilot-driver-core"):
            target = project.parents[1] / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    output = tmp_path / "artifacts"
    output.mkdir()
    deadline = time.monotonic() + 60

    def build(directory: Path, operation: str) -> None:
        result = run_caller_owned(
            [sys.executable, "-I", "-B", "-c",
             f"import setuptools.build_meta as backend; backend.{operation}("
             "__import__('sys').argv[1], {'--global-option': ['--quiet']})",
             str(output)],
            cwd=directory, env=dict(os.environ, UV_OFFLINE="1"),
            timeout=max(0.01, deadline - time.monotonic()),
            max_stdout_bytes=1024 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    build(project, "build_sdist")
    archives = list(output.glob("*.tar.gz"))
    assert len(archives) == 1
    recovered = tmp_path / "sdist"
    with tarfile.open(archives[0]) as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert member.isfile() or member.isdir()
            if member.isfile():
                target = recovered / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                assert stream is not None
                with stream:
                    target.write_bytes(stream.read())
    projects = list(recovered.iterdir())
    assert len(projects) == 1
    shutil.rmtree(tmp_path / "source")
    build(projects[0], "build_wheel")
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assets = {
            name.removeprefix("agent_remote_driver_app/"): wheel.read(name)
            for name in wheel.namelist()
            if name.startswith("agent_remote_driver_app/_runtime_assets/")
        }
    assert assets == expected
    installed = tmp_path / "installed-driver"
    for name, body in assets.items():
        target = installed / name.removeprefix("_runtime_assets/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    from agent_remote_driver.bridge_extension import extension_sources

    bridge = tmp_path / "bridge"
    extension = bridge / "extensions/agent-bridge"
    (extension / "core").mkdir(parents=True)
    shutil.copyfile(ROOT / "plugins/agent-bridge/extensions/agent-bridge/extension.mjs", extension / "extension.mjs")
    for name, body in extension_sources():
        (extension / "core" / name).write_bytes(body)
    sdk = tmp_path / "node_modules/@microsoft/copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {".": "./index.mjs", "./extension": "./extension.mjs"},
    }), encoding="utf-8")
    (sdk / "index.mjs").write_text("export const approveAll = () => { throw Error('forbidden'); };",
                                  encoding="utf-8")
    (sdk / "extension.mjs").write_text("export const joinSession = (...args) => globalThis.__joinSession(...args);",
                                      encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    node = shutil.which("node")
    assert node, "closed global connector controls require Node"
    for mode in ("selected", "wrong-loadout"):
        result = run_caller_owned([
            node, str(Path(__file__).parent / "fixtures/native-connector-entry.mjs"),
            str(installed), str(bridge), str(installed), str(config), mode, "global",
        ], cwd=tmp_path, timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024)
        assert result.returncode == 0, result.stdout + result.stderr
