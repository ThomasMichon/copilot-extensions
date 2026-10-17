"""Standalone source selection and native release-layout preparation.

Native probes retain real admission and only add dry-run/offline at the physical
resolver boundary. A timestamped loopback fixture registry supplies synthetic
external metadata, never real tools or packages. This isolates first-party
source resolution; it does not prove real installation or consumer behavior.

Required runner contracts retain mapping guards and the materialized Index
smoke. The existing manual full runner lane also selects the three expanded
native cases marked runner_exhaustive.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import materialize_main
from standalone_consumers import local_dependency_source

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "standalone_materialized_runner", REPO / "tools" / "run-standalone-tests.py",
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _bytes(root):
    return {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def _materialized_bytes(root):
    result = {}
    for directory, children, files in os.walk(root):
        children[:] = sorted(set(children) - materialize_main._ignore(directory, children))
        for name in set(files) - materialize_main._ignore(directory, files):
            path = Path(directory) / name
            result[path.relative_to(root)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@pytest.fixture
def preparation_root(tmp_path_factory):
    return tmp_path_factory.mktemp("p")


@pytest.fixture
def resolver_registry():
    artifacts, projects = {}, {}
    for name, version in {
        "fastapi": "0.141.1", "uvicorn": "0.52.4", "httpx": "0.28.1",
        "pyyaml": "6.0.3", "pytest": "9.1.1", "setuptools": "84.0.0",
        "wheel": "0.45.1", "packaging": "25.0",
    }.items():
        filename = f"{name}-{version}-py3-none-any.whl"
        contents = io.BytesIO()
        with zipfile.ZipFile(contents, "w") as wheel:
            prefix = f"{name}-{version}.dist-info/"
            wheel.writestr(prefix + "METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
            wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            wheel.writestr(prefix + "RECORD", "")
        artifacts[filename] = contents.getvalue()
        projects[name] = [{
            "filename": filename, "url": "/files/" + filename,
            "hashes": {"sha256": hashlib.sha256(artifacts[filename]).hexdigest()},
            "upload-time": "2020-01-01T00:00:00Z",
        }]

    class Registry(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond(head=False)

        def do_HEAD(self):
            self.respond(head=True)

        def respond(self, *, head):
            name = self.path.rstrip("/").rsplit("/", 1)[-1]
            if self.path.startswith("/simple/") and name in projects:
                body = json.dumps({
                    "meta": {"api-version": "1.2"}, "name": name, "files": projects[name],
                }).encode()
                content_type = "application/vnd.pypi.simple.v1+json"
            elif self.path.startswith("/files/") and name in artifacts:
                body, content_type = artifacts[name], "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if not head:
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/simple"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("component,plugin,materialized", [
    pytest.param(
        "services/dispatch", "agent-dispatch", False,
        marks=pytest.mark.runner_exhaustive, id="services/dispatch-agent-dispatch-dev",
    ),
    pytest.param(
        "services/dispatch", "agent-dispatch", True,
        marks=pytest.mark.runner_exhaustive, id="services/dispatch-agent-dispatch-materialized",
    ),
    pytest.param(
        "services/index", "agent-index", False,
        marks=pytest.mark.runner_exhaustive, id="services/index-agent-index-dev",
    ),
    pytest.param(
        "services/index", "agent-index", True, id="services/index-agent-index-materialized",
    ),
])
def test_materialized_preparation_has_one_real_source_per_distribution(
    preparation_root, resolver_registry, monkeypatch, component, plugin, materialized,
):
    """Keep production admission and invoke UV, without downloading/installing."""
    tmp_path = preparation_root
    checkout = tmp_path / "checkout"
    libraries = (
        "zdd", "agent-procutil", "dropin-registry", "plugin-activation",
        "plugin-resolve", "single-instance-lease", "versioned-runtime",
    )
    inputs = [component, f"plugins/{plugin}", *(f"libs/{lib}" for lib in libraries)]
    original = {relative: _bytes(REPO / relative) for relative in inputs}
    for relative in inputs:
        shutil.copytree(
            REPO / relative, checkout / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", ".pytest_cache"),
        )
    if materialized:
        log = materialize_main.materialize_uv_editable_refs(checkout, canonical_root=REPO)
        # An already-materialized release checkout legitimately needs no expansion.
        assert not any(line.startswith("SKIP") for line in log), log
        core = checkout / "plugins" / plugin
        assert not materialize_main.uer.find_uv_editable_refs(core)
    assert {relative: _bytes(REPO / relative) for relative in inputs} == original
    if materialized:
        embedded = checkout / "plugins" / plugin / "libs" / "zdd"
        assert _bytes(embedded) == _materialized_bytes(REPO / "libs" / "zdd")
    before = {
        relative: _bytes(checkout / relative)
        for relative in (component, f"plugins/{plugin}", "libs")
    }
    monkeypatch.setattr(runner, "REPO", checkout)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    config = tmp_path / "uv.toml"
    config.write_text(f'[[index]]\nurl="{resolver_registry}"\ndefault=true\n')
    for name in list(os.environ):
        if name.startswith("UV_") and name not in {"UV_CACHE_DIR", "UV_PYTHON_DOWNLOADS"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("UV_CONFIG_FILE", str(config))
    actual_run = subprocess.run
    physical_installs = []

    def resolve(command, **kwargs):
        if command[:3] == ["uv", "pip", "install"] and "--show-settings" not in command:
            assert "--exclude-newer" in command and "--reinstall" in command
            assert Path(kwargs["cwd"]) != checkout
            physical_installs.append(command)
            # Cache only controlled fixture metadata, then require a real
            # successful resolution with network access disabled.
            actual_run([*command, "--dry-run"], **kwargs)
            command = [*command, "--dry-run", "--offline"]
        return actual_run(command, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", resolve)
    python = runner.default_python(component)
    python.parent.parent.mkdir(parents=True)
    try:
        runner.prepare(component, python, smoke=component == "services/index")
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            f"real materialized preparation failed:\n{exc.stdout}\n{exc.stderr}",
            pytrace=False,
        )
    finally:
        for relative, expected in before.items():
            assert _bytes(checkout / relative) == expected
        assert {relative: _bytes(REPO / relative) for relative in inputs} == original
    assert python.is_file()
    assert len(physical_installs) == 1


@pytest.mark.parametrize("raw_source", [
    "", '[tool.uv.sources]\nagent-procutil={git="https://example.invalid/source"}\n',
    '[tool.uv.sources]\nagent-procutil=[{path="lib"},{path="other"}]\n',
    '[tool.uv.sources]\nagent-procutil={path="lib",marker="sys_platform == \'win32\'"}\n',
    '[tool.uv.sources]\nagent-procutil={path="lib",editable="true"}\n',
    '[tool.uv.sources]\nagent-procutil={path=""}\n',
    '[tool.uv.sources]\nagent-procutil={path="missing"}\n',
    '[tool.uv.sources]\nagent-procutil={path="lib"}\nagent_procutil={path="other"}\n',
    '[tool.uv.sources]\nagent-procutil={path="lib"\n',
    '[tool.uv]\nsources=[]\n',
])
def test_local_override_refuses_missing_malformed_or_ambiguous_sources(tmp_path, raw_source):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="core"\n' + raw_source)
    library = tmp_path / "lib"
    library.mkdir()
    (library / "pyproject.toml").write_text('[project]\nname="agent-procutil"\n')
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="invalid local source mapping"):
        local_dependency_source(tmp_path, "agent-procutil", source_root=tmp_path)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("metadata", [
    '[project]\nname="different-package"\n', '[project]\nname=42\n', "malformed=[\n",
])
def test_local_override_refuses_inconsistent_dependency_identity(tmp_path, metadata):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.sources]\nagent-procutil={path="lib"}\n',
    )
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "pyproject.toml").write_text(metadata)
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="invalid local source mapping"):
        local_dependency_source(tmp_path, "agent-procutil", source_root=tmp_path)
    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("layout", ["../../libs/agent-procutil", "libs/agent-procutil"])
def test_local_override_preserves_selected_source_not_equivalent_canonical_copy(tmp_path, layout):
    project = tmp_path / "plugins" / "agent-index"
    project.mkdir(parents=True)
    for library in (tmp_path / "libs" / "agent-procutil", project / "libs" / "agent-procutil"):
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text('[project]\nname="agent-procutil"\n')
    (project / "pyproject.toml").write_text(
        f'[tool.uv.sources]\nagent-procutil={{path="{layout}"}}\n',
    )
    before = _bytes(tmp_path)
    assert local_dependency_source(
        project, "agent-procutil", source_root=tmp_path,
    ) == (project / layout).resolve()
    assert _bytes(tmp_path) == before


def test_local_override_refuses_source_outside_caller_attributed_root(tmp_path):
    project = tmp_path / "checkout"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pyproject.toml").write_text('[project]\nname="agent-procutil"\n')
    (project / "pyproject.toml").write_text(
        '[tool.uv.sources]\nagent-procutil={path="../outside"}\n',
    )
    before = _bytes(tmp_path)
    with pytest.raises(ValueError, match="invalid local source mapping"):
        local_dependency_source(project, "agent-procutil", source_root=project)
    assert _bytes(tmp_path) == before
