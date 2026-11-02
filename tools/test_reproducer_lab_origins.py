"""Synthetic runtime-origin controls; no installed runtime, guest or service."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB

sys.path.insert(0, str(LAB))
import guest_origins
import runtime_origin_probe as probe


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    source, runtime = tmp_path / "source", tmp_path / "runtime"
    prefix = runtime / "bridge"
    executable = prefix / "bin/python3"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic interpreter")
    site = prefix / "lib/site-packages"
    package = site / "agent_bridge"
    original = source / "plugins/agent-bridge/src/agent_bridge"
    for root in (package, original):
        root.mkdir(parents=True)
        (root / "__init__.py").write_bytes(b"# synthetic main package\n")
        (root / "helper.py").write_bytes(b"value = 7\n")
    project = original.parent.parent
    (project / "pyproject.toml").write_text(
        '[project]\nname = "agent-bridge"\nversion = "1.2.3-dev4"\n', encoding="utf-8",
    )
    direct = {"url": project.as_uri(), "dir_info": {}}
    distribution = SimpleNamespace(
        version="1.2.3.dev4",
        read_text=lambda _name: json.dumps(direct),
        locate_file=lambda _path: site,
    )
    module = SimpleNamespace(__file__=str(package / "__init__.py"))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(executable))
    for name in tuple(sys.modules):
        if name == "agent_bridge" or name.startswith("agent_bridge."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(probe.importlib.metadata, "distribution", lambda _name: distribution)
    monkeypatch.setattr(probe.importlib, "import_module", lambda _name: module)
    return SimpleNamespace(
        source=source, runtime=runtime, prefix=prefix, executable=executable, site=site,
        package=package, original=original, project=project, direct=direct,
        distribution=distribution, module=module,
    )


def test_observation_attributes_main_package_only(paths: SimpleNamespace) -> None:
    value = probe.observe("bridge", runtime=paths.runtime, source=paths.source)
    assert value == probe.RuntimeOrigin(
        "bridge", str(paths.executable), str(paths.executable), str(paths.prefix),
        str(paths.package), "1.2.3.dev4", str(paths.original), 2, 35,
    )


@pytest.mark.parametrize("change", ["prefix", "executable", "base", "plugin"])
def test_wrong_interpreter_refuses(
    paths: SimpleNamespace, monkeypatch: Any, change: str,
) -> None:
    plugin = "bridge"
    if change == "prefix":
        monkeypatch.setattr(sys, "prefix", str(paths.source))
    elif change == "executable":
        monkeypatch.setattr(sys, "executable", str(paths.source / "python"))
    elif change == "base":
        monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    else:
        plugin = "other"
    with pytest.raises(ValueError, match="isolated runtime|unsupported"):
        probe.observe(plugin, runtime=paths.runtime, source=paths.source)


@pytest.mark.parametrize("change", [
    "version", "missing-direct", "editable", "numeric-editable", "other-source", "extra-direct",
    "foreign-site", "imported-source", "missing-file", "changed-file", "extra-file",
])
def test_wrong_installed_source_refuses(
    paths: SimpleNamespace, change: str,
) -> None:
    if change == "version":
        paths.distribution.version = "1.2.3"
    elif change == "missing-direct":
        paths.distribution.read_text = lambda _name: None
    elif change == "editable":
        paths.direct["dir_info"] = {"editable": True}
    elif change == "numeric-editable":
        paths.direct["dir_info"] = {"editable": 0}
    elif change == "other-source":
        paths.direct["url"] = paths.runtime.as_uri()
    elif change == "extra-direct":
        paths.direct["archive_info"] = {}
    elif change == "foreign-site":
        paths.distribution.locate_file = lambda _path: paths.source
    elif change == "imported-source":
        paths.module.__file__ = str(paths.original / "__init__.py")
    elif change == "missing-file":
        (paths.original / "only-in-source.py").write_bytes(b"missing from runtime\n")
    elif change == "changed-file":
        (paths.package / "helper.py").write_bytes(b"wrong source bytes\n")
    else:
        (paths.package / "extra.py").write_bytes(b"extra runtime bytes\n")
    with pytest.raises(ValueError):
        probe.observe("bridge", runtime=paths.runtime, source=paths.source)


@pytest.mark.parametrize("change", [
    "symlink", "entries", "file-bytes", "total-bytes", "cached-initializer", "cached-helper",
])
def test_source_inventory_is_bounded(
    paths: SimpleNamespace, monkeypatch: Any, change: str,
) -> None:
    if change == "symlink":
        (paths.original / "external.py").symlink_to(paths.package / "helper.py")
    elif change == "entries":
        monkeypatch.setattr(probe, "MAX_FILES", 1)
    elif change == "file-bytes":
        monkeypatch.setattr(probe, "MAX_FILE_BYTES", 1)
    elif change.startswith("cached-"):
        cache = paths.package / "__pycache__"
        cache.mkdir()
        name = "__init__" if change == "cached-initializer" else "helper"
        (cache / (name + ".cpython-312.pyc")).write_bytes(b"synthetic stale bytecode")
    else:
        monkeypatch.setattr(probe, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="redirected|limit|bounded|cached bytecode"):
        probe.observe("bridge", runtime=paths.runtime, source=paths.source)


def test_preimported_main_package_refuses(
    paths: SimpleNamespace, monkeypatch: Any,
) -> None:
    monkeypatch.setitem(sys.modules, "agent_bridge", paths.module)
    with pytest.raises(ValueError, match="imported before"):
        probe.observe("bridge", runtime=paths.runtime, source=paths.source)


def test_explicit_noneditable_metadata_is_supported(paths: SimpleNamespace) -> None:
    paths.direct["dir_info"] = {"editable": False}
    assert probe.observe("bridge", runtime=paths.runtime, source=paths.source).python_files == 2


def test_file_growth_is_read_with_a_finite_limit(
    paths: SimpleNamespace, monkeypatch: Any,
) -> None:
    import io

    reads: list[int] = []

    class GrowingFile(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return super().read(size)

    monkeypatch.setattr(probe, "MAX_FILE_BYTES", 32)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: GrowingFile(b"x" * 100))
    with pytest.raises(ValueError, match="exceeded its limit"):
        probe._read(paths.original / "helper.py")
    assert reads == [33]


@pytest.fixture
def caller(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(guest_origins.guest_runtime, "RUNTIME", runtime)
    monkeypatch.setattr(guest_origins.guest_runtime, "_runtime_identity", runtime.lstat)
    calls: list[tuple[list[str], dict[str, object]]] = []
    assertions: list[bool] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        plugin = argv[-1]
        prefix = runtime / plugin
        value = probe.RuntimeOrigin(
            plugin, str(prefix / "bin/python3"), "/usr/local/bin/python3.12", str(prefix),
            str(prefix / "lib/site-packages" / ("agent_" + plugin)), "1.2.3",
            str(guest_origins.SOURCE / "plugins" / ("agent-" + plugin) / "src" / ("agent_" + plugin)),
            1, 25,
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(asdict(value)), "")

    monkeypatch.setattr(guest_origins, "run", run)
    return SimpleNamespace(
        runtime=runtime, calls=calls, assertions=assertions,
        custody=SimpleNamespace(assert_owned=lambda: assertions.append(True)),
    )


def test_caller_has_fixed_finite_isolated_commands(caller: SimpleNamespace) -> None:
    result = guest_origins.inspect_runtimes(caller.custody)
    assert tuple(value.plugin for value in result) == probe.PLUGINS
    assert len(caller.calls) == 3 and len(caller.assertions) == 7
    for (argv, options), plugin in zip(caller.calls, probe.PLUGINS):
        assert argv == [
            str(caller.runtime / plugin / "bin/python3"), "-I", "-B",
            str(guest_origins.SOURCE / "tools/reproducer-lab/runtime_origin_probe.py"), plugin,
        ]
        assert options == {
            "timeout": 10, "max_stdout_bytes": 16 * 1024,
            "env": guest_origins.environment(),
        }
    assert list(caller.runtime.iterdir()) == []  # No installation/readiness marker.


@pytest.mark.parametrize("change", [
    "exit", "exception", "json", "fields", "inventory", "attribution", "runtime",
])
def test_failed_observation_never_retries_or_returns_partial_success(
    caller: SimpleNamespace, monkeypatch: Any, change: str,
) -> None:
    original_run = guest_origins.run

    def fail(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = original_run(argv, **kwargs)
        if change == "exception":
            raise TimeoutError("synthetic capture timeout")
        if change == "exit":
            return subprocess.CompletedProcess(argv, 1, "", "literal synthetic cause")
        data = json.loads(result.stdout)
        if change == "json":
            return subprocess.CompletedProcess(argv, 0, "not JSON", "")
        if change == "fields":
            data["ready"] = True
        elif change == "inventory":
            data["python_files"] = True
        elif change == "attribution":
            data["version"] = ""
        else:
            data["prefix"] = "/another/runtime"
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(guest_origins, "run", fail)
    with pytest.raises((ValueError, RuntimeError, TimeoutError)):
        guest_origins.inspect_runtimes(caller.custody)
    assert len(caller.calls) == 1


def test_caller_refuses_inside_event_loop(caller: SimpleNamespace) -> None:
    async def invoke() -> None:
        guest_origins.inspect_runtimes(caller.custody)

    with pytest.raises(RuntimeError, match="outside an event loop"):
        asyncio.run(invoke())
    assert not caller.calls


def test_custody_loss_does_not_observe_another_runtime(caller: SimpleNamespace) -> None:
    checks = 0

    def owned() -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise ValueError("synthetic lost operation")

    caller.custody.assert_owned = owned
    with pytest.raises(ValueError, match="lost operation"):
        guest_origins.inspect_runtimes(caller.custody)
    assert len(caller.calls) == 1


def test_runtime_root_replacement_refuses(
    caller: SimpleNamespace, monkeypatch: Any,
) -> None:
    checks = 0
    initial = caller.runtime.lstat()
    replacement = caller.runtime.parent.stat()

    def identity() -> object:
        nonlocal checks
        checks += 1
        return initial if checks == 1 else replacement

    monkeypatch.setattr(guest_origins.guest_runtime, "_runtime_identity", identity)
    with pytest.raises(ValueError, match="directory changed"):
        guest_origins.inspect_runtimes(caller.custody)
    assert len(caller.calls) == 1
