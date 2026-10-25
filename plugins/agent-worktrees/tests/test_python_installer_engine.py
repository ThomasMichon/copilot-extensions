"""Frozen-input contracts for the Python-owned installer lifecycle."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def engine(monkeypatch):
    if sys.version_info < (3, 11):
        pytest.skip("the installer bootstrap requires Python 3.11 or newer")
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "scripts"))
    monkeypatch.syspath_prepend(str(root / "libs" / "installer-engine"))
    path = root / "libs" / "installer-engine" / "installer_engine.py"
    spec = importlib.util.spec_from_file_location("python_installer_engine", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _package(root: Path, name: str, sources: str = "") -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n' + sources,
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def test_child_failure_preserves_unicode_diagnostic_and_original_error(engine, monkeypatch):
    import io

    failure = subprocess.CalledProcessError(1, ["uv"], output="", stderr="\u2502 build failed")
    stream = io.BytesIO()
    stderr = io.TextIOWrapper(stream, encoding="cp1252", errors="backslashreplace")
    monkeypatch.setattr(engine.sys, "stderr", stderr)
    monkeypatch.setattr(engine, "_procutil", lambda _inputs: None)

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(engine, "run_owned", fail)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        engine._child(["uv"], inputs=SimpleNamespace(wheel_inputs=None), descriptor=0, timeout=1)
    assert caught.value is failure
    stderr.flush()
    assert b"\\u2502 build failed" in stream.getvalue()


@pytest.mark.timeout(120)
@pytest.mark.parametrize("primary", [
    subprocess.CalledProcessError(17, ["uv"], output="child output", stderr="child failure"),
    OSError("child could not start"),
])
def test_candidate_failure_survives_retained_cleanup(
    engine, tmp_path, build_inputs, monkeypatch, capsys, primary,
):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    policy = sys.modules[engine.retain_uv_install.__module__]
    temporary_directory = policy.tempfile.TemporaryDirectory
    cleanup_attempts = []

    class CleanupFailure:
        def __init__(self, **kwargs):
            self.temporary = temporary_directory(**kwargs)
            self.name = self.temporary.name

        def cleanup(self):
            cleanup_attempts.append(self.name)
            self.temporary.name = self.name
            self.temporary.cleanup()
            raise PermissionError("injected retained cleanup failure")

    def fail(*args, **kwargs):
        raise primary

    monkeypatch.setattr(policy.tempfile, "TemporaryDirectory", CleanupFailure)
    monkeypatch.setattr(engine, "_construct", fail)
    with pytest.raises(type(primary)) as caught:
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module",
        ):
            pytest.fail("a failed construction cannot yield a ready candidate")
    assert caught.value is primary
    assert len(cleanup_attempts) == 1
    assert not Path(cleanup_attempts[0]).exists()
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    with engine.runtime.construction_lease(root, "1.0.0", timeout=0):
        pass
    diagnostic = capsys.readouterr().err
    assert diagnostic.count("retained-input-cleanup:") == 1
    assert "injected retained cleanup failure" in diagnostic


def test_child_timeout_surfaces_captured_backend_diagnostic(engine, monkeypatch, capsys):
    failure = subprocess.TimeoutExpired(
        ["uv"], 1, output="building example\n", stderr="backend stalled\n",
    )
    monkeypatch.setattr(engine, "_procutil", lambda _inputs: None)

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(engine, "run_owned", fail)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        engine._child(["uv"], inputs=SimpleNamespace(wheel_inputs=None), descriptor=0, timeout=1)
    assert caught.value is failure
    diagnostic = capsys.readouterr().err
    assert "building example" in diagnostic and "backend stalled" in diagnostic


def test_child_preserves_canonical_transaction_environment_and_cwd(engine, monkeypatch, tmp_path):
    environment = {"UV_CONFIG_FILE": "retained-config", "VIRTUAL_ENV": "admitted-environment"}
    monkeypatch.setattr(engine, "_procutil", lambda _inputs: None)

    def run(argv, **kwargs):
        assert kwargs["env"] == environment
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(engine, "run_owned", run)
    engine._child(
        [sys.executable], inputs=SimpleNamespace(wheel_inputs=None), descriptor=0, timeout=1,
        environment=environment, cwd=tmp_path,
    )


def test_frozen_procutil_imports_its_siblings_without_cross_root_reuse(engine, tmp_path):
    loaded = []
    for name in ("first", "second"):
        root = _package(tmp_path / name, "agent-procutil")
        package = root / "src" / "agent_procutil"
        package.mkdir()
        (package / "__init__.py").write_text("from .sibling import VALUE\n", encoding="utf-8")
        (package / "sibling.py").write_text(f"VALUE = {name!r}\n", encoding="utf-8")
        module = engine._procutil(engine.FrozenInputs(root, (root,), "unused"))
        assert module.VALUE == name
        sibling = sys.modules[module.__name__ + ".sibling"]
        assert Path(sibling.__file__).is_relative_to(root)
        loaded.append(module.__name__)
    assert loaded[0] != loaded[1]


@pytest.mark.parametrize("windows", [False, True])
def test_process_creation_failure_has_a_distinct_os_error_type(procutil, monkeypatch, windows):
    failure = OSError(193, "not an executable", "broken-python")
    monkeypatch.setattr(procutil, "_is_windows", lambda: windows)

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(procutil.subprocess, "Popen", failed)
    with pytest.raises(procutil.SyncProcessLaunchError) as caught:
        procutil.spawn_sync_in_kill_on_close_job(["broken-python"], require_job=True)
    assert isinstance(caught.value, OSError)
    assert caught.value.errno == failure.errno and caught.value.filename == failure.filename
    assert caught.value.__cause__ is failure


def test_post_creation_job_failure_is_not_a_launch_error(procutil, monkeypatch):
    from types import SimpleNamespace

    failure = OSError("Job assignment accounting unavailable")
    monkeypatch.setattr(procutil, "_is_windows", lambda: True)
    monkeypatch.setattr(procutil.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=1))

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(procutil, "_assign_suspended_to_kill_on_close_job", failed)
    with pytest.raises(OSError) as caught:
        procutil.spawn_sync_in_kill_on_close_job(["python"], require_job=True)
    assert caught.value is failure and not isinstance(caught.value, procutil.SyncProcessLaunchError)


def test_freeze_closes_transitive_external_paths_without_editing_source(engine, tmp_path):
    leaf = _package(tmp_path / "repo" / "libs" / "leaf", "example-leaf")
    middle = _package(
        tmp_path / "repo" / "libs" / "middle", "example-middle",
        '[tool.uv.sources]\nexample-leaf = {path = "../leaf", editable = true}\n',
    )
    plugin = _package(
        tmp_path / "repo" / "plugins" / "example", "example",
        '[tool.uv.sources]\nexample-middle = {path = "../../libs/middle", editable = true}\n',
    )
    before = (plugin / "pyproject.toml").read_bytes()
    frozen = engine.freeze_inputs(plugin, tmp_path / "frozen")
    assert len(frozen.packages) == 3
    assert all(path.is_relative_to(tmp_path / "frozen") for path in frozen.packages)
    assert (plugin / "pyproject.toml").read_bytes() == before
    (leaf / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert (tmp_path / "frozen" / "libs" / "leaf" / "src" / "module.py").read_text() == "VALUE = 1\n"
    assert middle.exists()
    assert engine.freeze_inputs(plugin, tmp_path / "changed").payload_hash != frozen.payload_hash


@pytest.mark.parametrize("extras", [
    ["server"], "server", ("",), ("server,store",), ("../server",),
    ("server", "SERVER"), ("unknown",),
])
def test_candidate_refuses_invalid_extra_selection_before_writing(engine, tmp_path, extras):
    plugin = _package(tmp_path / "plugin", "example")
    with (plugin / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('[project.optional-dependencies]\nserver=[]\n')
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="extras"):
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=tmp_path / "unused-uv",
            base_python=tmp_path / "unused-python", health_module="example_module", extras=extras,
        ):
            pytest.fail("invalid extras cannot admit a candidate")
    assert not root.exists()


def test_extra_selection_normalizes_order_and_refuses_ambiguous_declarations(engine, tmp_path):
    plugin = _package(tmp_path / "plugin", "example")
    manifest = plugin / "pyproject.toml"
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write('[project.optional-dependencies]\nserver=[]\nstdio_mcp=[]\n')
    assert engine._selected_extras(plugin, ("STDIO.MCP", "SERVER")) == ("server", "stdio-mcp")
    assert engine._selected_extras(plugin, ("STDIO..__MCP",)) == ("stdio-mcp",)
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write('stdio-mcp=[]\n')
    with pytest.raises(ValueError, match="ambiguous"):
        engine._selected_extras(plugin, ("server",))


def test_dev_candidate_refuses_nonbase_profiles_before_writing(engine, tmp_path):
    plugin = _package(tmp_path / "plugin", "example")
    with (plugin / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('[project.optional-dependencies]\nserver=[]\n')
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="immutable numbered"):
        with engine.dev_candidate(
            root, owner="profile-test", plugin=plugin, uv=tmp_path / "unused-uv",
            base_python=tmp_path / "unused-python", health_module="example_module",
            extras=("server",),
        ):
            pytest.fail("mutable slots cannot acquire a non-base profile")
    assert not root.exists()


def test_construction_identity_preserves_base_and_binds_selected_extras(engine, tmp_path):
    plugin = _package(tmp_path / "plugin", "example")
    files = engine._source_files((plugin,))
    base = engine._construction_hash(files, ())
    server = engine._construction_hash(files, ("server",))
    assert base == engine.runtime.fingerprint_source(files)
    assert len({base, server, engine._construction_hash(files, ("server", "store"))}) == 3
    (plugin / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert engine._construction_hash(files, ("server",)) != server


def test_freeze_identity_survives_relocation_and_ignores_build_outputs(engine, tmp_path):
    plugin = _package(tmp_path / "one" / "plugin", "example")
    (plugin / "build").mkdir()
    (plugin / "build" / "stale.py").write_text("OLD = 1", encoding="utf-8")
    (plugin / "src" / "__pycache__").mkdir()
    (plugin / "src" / "__pycache__" / "cache.pyc").write_bytes(b"cached")
    shutil.copytree(plugin, tmp_path / "two" / "plugin")
    first = engine.freeze_inputs(plugin, tmp_path / "frozen-one")
    second = engine.freeze_inputs(tmp_path / "two" / "plugin", tmp_path / "frozen-two")
    assert first.payload_hash == second.payload_hash
    assert not (first.plugin / "build").exists()
    assert not (first.plugin / "src" / "__pycache__").exists()


@pytest.mark.parametrize("mutation", ["content", "new-file", "dependency"])
def test_freeze_refuses_concurrent_input_mutation(engine, tmp_path, monkeypatch, mutation):
    plugin = _package(tmp_path / "repo" / "plugin", "example")
    dependency = _package(tmp_path / "repo" / "dependency", "example-dependency")
    original_copy = engine.shutil.copyfile
    mutated = False

    def copy_and_mutate(source, target, **kwargs):
        nonlocal mutated
        result = original_copy(source, target, **kwargs)
        if not mutated:
            mutated = True
            if mutation == "content":
                (plugin / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
            elif mutation == "new-file":
                (plugin / "src" / "new.py").write_text("NEW = 1\n", encoding="utf-8")
            else:
                with (plugin / "pyproject.toml").open("a", encoding="utf-8") as stream:
                    stream.write('[tool.uv.sources]\nexample-dependency = {path = "../dependency"}\n')
        return result

    monkeypatch.setattr(engine.shutil, "copyfile", copy_and_mutate)
    destination = tmp_path / "frozen"
    with pytest.raises(RuntimeError, match="changed while freezing"):
        engine.freeze_inputs(plugin, destination)
    assert not destination.exists()
    assert dependency.exists()


def test_freeze_refuses_missing_declared_dependency(engine, tmp_path):
    plugin = _package(
        tmp_path / "plugin", "example",
        '[tool.uv.sources]\nmissing = {path = "../missing"}\n',
    )
    with pytest.raises(FileNotFoundError):
        engine.freeze_inputs(plugin, tmp_path / "frozen")
    assert not (tmp_path / "frozen").exists()


def test_freeze_never_removes_preexisting_destination(engine, tmp_path):
    plugin = _package(tmp_path / "plugin", "example")
    destination = tmp_path / "frozen"
    destination.mkdir()
    (destination / "owned").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        engine.freeze_inputs(plugin, destination)
    assert (destination / "owned").read_text() == "keep"


def test_freeze_refuses_destination_inside_package(engine, tmp_path):
    plugin = _package(tmp_path / "plugin", "example")
    with pytest.raises(ValueError, match="inside runtime source"):
        engine.freeze_inputs(plugin, plugin / "frozen")
    assert not (plugin / "frozen").exists()


def test_freeze_refuses_wrong_local_distribution(engine, tmp_path):
    _package(tmp_path / "dependency", "wrong-name")
    plugin = _package(
        tmp_path / "plugin", "example",
        '[tool.uv.sources]\nexpected = {path = "../dependency"}\n',
    )
    with pytest.raises(ValueError, match="resolves to wrong-name"):
        engine.freeze_inputs(plugin, tmp_path / "frozen")


def test_freeze_refuses_nonlocal_source_override(engine, tmp_path):
    plugin = _package(
        tmp_path / "plugin", "example",
        '[tool.uv.sources]\nexternal = {git = "https://example.invalid/source.git"}\n',
    )
    with pytest.raises(ValueError, match="Unsupported nonlocal"):
        engine.freeze_inputs(plugin, tmp_path / "frozen")


def test_freeze_refuses_symlinked_source_and_destination_escape(engine, tmp_path):
    plugin = _package(tmp_path / "plugin", "example")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(plugin, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(ValueError, match="inside runtime source"):
        engine.freeze_inputs(plugin, alias / "frozen")
    with pytest.raises(ValueError, match="input is a symlink"):
        engine.freeze_inputs(alias, tmp_path / "frozen")
    (plugin / "src" / "alias.py").symlink_to("module.py")
    with pytest.raises(ValueError, match="input file is not ordinary"):
        engine.freeze_inputs(plugin, tmp_path / "frozen")


@pytest.mark.timeout(180)
def test_freeze_real_worktrees_graph_is_self_contained(engine, tmp_path):
    plugin = Path(__file__).resolve().parents[1]
    frozen = engine.freeze_inputs(plugin, tmp_path / "frozen")
    manifests = [
        engine.tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        for root in frozen.packages
    ]
    names = {data["project"]["name"] for data in manifests}
    assert {"agent-worktrees", "agent-machine-transport", "agent-procutil", "agent-zdd"} <= names
    assert len(names) == len(frozen.packages)
    assert frozen.payload_hash == engine.runtime.fingerprint_source(
        engine._source_files(frozen.packages)
    )
    assert all(root.is_relative_to(tmp_path / "frozen") for root in frozen.packages)


@pytest.fixture
def procutil(engine):
    root = Path(__file__).resolve().parents[3]
    package = root / "libs" / "agent-procutil"
    return engine._procutil(engine.FrozenInputs(package, (package,), "unused"))


def test_owned_child_captures_both_streams_and_surfaces_failure(engine, procutil, tmp_path):
    with engine.runtime.construction_lease(tmp_path, "1.0") as descriptor:
        result = engine.run_owned(
            [sys.executable, "-c", "import sys;print('out'*20000);print('err'*20000,file=sys.stderr)"],
            cwd=tmp_path, env=dict(os.environ), timeout=10,
            procutil=procutil, construction_fd=descriptor,
        )
        assert result.stdout.strip() == "out" * 20000
        assert result.stderr.strip() == "err" * 20000
        with pytest.raises(subprocess.CalledProcessError) as failure:
            engine.run_owned(
                [sys.executable, "-c", "import sys;print('diagnostic',file=sys.stderr);sys.exit(7)"],
                cwd=tmp_path, env=dict(os.environ), timeout=10,
                procutil=procutil, construction_fd=descriptor,
            )
        assert failure.value.returncode == 7
        assert "diagnostic" in failure.value.stderr


def _writer_tree(marker: Path) -> str:
    return (
        "import os,pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())+' '+str(child.pid))\n"
        "time.sleep(60)\n"
    )


def _wait_for_writer(marker: Path) -> list[int]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if marker.is_file() and marker.read_text():
            return [int(pid) for pid in marker.read_text().split()]
        time.sleep(0.02)
    pytest.fail("installer writer tree did not start")


def _assert_writers_dead(engine, pids: list[int]):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        live = [pid for pid in pids if engine.runtime._pid_alive(pid)]
        if os.name != "nt":
            live = [
                pid for pid in live
                if not (Path(f"/proc/{pid}/stat").is_file()
                        and Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] == "Z")
            ]
        if not live:
            return
        time.sleep(0.02)
    pytest.fail(f"installer writers survived ownership settlement: {live}")


def test_owned_child_timeout_reaps_descendants_before_lease_release(engine, procutil, tmp_path):
    marker = tmp_path / "writers"
    with engine.runtime.construction_lease(tmp_path, "1.0") as descriptor:
        with pytest.raises(subprocess.TimeoutExpired) as failure:
            engine.run_owned(
                [sys.executable, "-c",
                 "print('backend stdout',flush=True)\n"
                 "import sys;print('backend stderr',file=sys.stderr,flush=True)\n"
                 + _writer_tree(marker)],
                cwd=tmp_path, env=dict(os.environ), timeout=2,
                procutil=procutil, construction_fd=descriptor,
            )
        _assert_writers_dead(engine, _wait_for_writer(marker))
        assert "backend stdout" in failure.value.stdout
        assert "backend stderr" in failure.value.stderr
    with engine.runtime.construction_lease(tmp_path, "1.0", timeout=0):
        pass


def test_teardown_retry_retains_first_failure_even_when_wait_is_interrupted(
    engine, monkeypatch, caplog,
):
    import installer_engine_process as ownership

    attempts = 0
    sleeps = 0
    failure = OSError("ownership accounting temporarily unavailable")

    def settle():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise failure

    def sleep(delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            raise KeyboardInterrupt()

    monkeypatch.setattr(ownership.time, "sleep", sleep)
    with pytest.raises(OSError) as raised:
        ownership._settle(settle)
    assert raised.value is failure
    assert attempts == 2 and sleeps == 2
    assert "retaining construction custody" in caplog.text


def test_process_group_identity_is_not_reaped_before_descendants_settle(engine, monkeypatch):
    import installer_engine_process as ownership
    from types import SimpleNamespace

    observations = iter(["123 123 Z\n456 123 S\n", "123 123 Z\n"])
    events = []
    process = SimpleNamespace(pid=123, returncode=None)

    def wait(timeout):
        events.append("reap")
        process.returncode = 0

    def observe(*args, **kwargs):
        events.append("observe")
        return SimpleNamespace(stdout=next(observations))

    process.wait = wait
    monkeypatch.setattr(ownership.os, "killpg", lambda *args: events.append("kill"), raising=False)
    monkeypatch.setattr(ownership.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(ownership.subprocess, "run", observe)
    monkeypatch.setattr(ownership.time, "sleep", lambda delay: None)
    ownership._terminate_group(process)
    assert events == ["kill", "observe", "observe", "reap"]
    ownership._terminate_group(process)
    assert events == ["kill", "observe", "observe", "reap"], "never target a reaped PGID again"


@pytest.mark.parametrize("death", ["pid", "containment"])
def test_owned_child_hard_owner_death_reaps_writer_tree(engine, procutil, tmp_path, death):
    root = Path(__file__).resolve().parents[3]
    marker = tmp_path / "writers"
    owner_code = (
        "import pathlib,sys,os\n"
        f"sys.path[:0]=[{str(root / 'libs' / 'installer-engine')!r},"
        f"{str(root / 'plugins' / 'agent-worktrees' / 'scripts')!r}]\n"
        "import installer_engine as engine\n"
        f"package=pathlib.Path({str(root / 'libs' / 'agent-procutil')!r})\n"
        "procutil=engine._procutil(engine.FrozenInputs(package,(package,),'unused'))\n"
        f"root=pathlib.Path({str(tmp_path)!r})\n"
        "with engine.runtime.construction_lease(root,'1.0') as descriptor:\n"
        f" engine.run_owned([sys.executable,'-c',{_writer_tree(marker)!r}],"
        "cwd=root,env=dict(os.environ),timeout=60,procutil=procutil,construction_fd=descriptor)\n"
    )
    owner, job = procutil.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-c", owner_code],
        require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **({"start_new_session": True} if os.name != "nt" else {}),
        **procutil.no_window_kwargs(),
    )
    try:
        pids = _wait_for_writer(marker)
        if death == "pid":
            owner.kill()
        elif job is not None:
            job.close()
            job = None
        else:
            os.killpg(owner.pid, signal.SIGKILL)
        owner.wait(timeout=10)
        _assert_writers_dead(engine, pids)
        with engine.runtime.construction_lease(tmp_path, "1.0", timeout=10):
            pass
    finally:
        if job is not None:
            job.terminate_and_wait(10)
        elif owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)


_WHEEL_BACKEND = '''
import os,pathlib,time,tomllib,zipfile

def get_requires_for_build_wheel(config_settings=None):
    return []

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    root=pathlib.Path(__file__).parent
    project=tomllib.loads((root/"pyproject.toml").read_text())["project"]
    if os.environ.get("TEST_BUILD_COUNTER"):
        marker=pathlib.Path(os.environ["TEST_BUILD_COUNTER"])/project["name"]
        with marker.open("x") as output:
            output.write("built")
        time.sleep(0.5)
    name=project["name"].replace("-","_")
    version=project["version"]
    wheel=f"{name}-{version}-py3-none-any.whl"
    info=f"{name}-{version}.dist-info"
    metadata=f"Metadata-Version: 2.1\\nName: {project['name']}\\nVersion: {version}\\n"
    metadata+="".join(f"Requires-Dist: {dependency}\\n" for dependency in project.get("dependencies",[]))
    for extra,requirements in project.get("optional-dependencies",{}).items():
        metadata+=f"Provides-Extra: {extra}\\n"
        metadata+="".join(f"Requires-Dist: {dependency}; extra == '{extra}'\\n" for dependency in requirements)
    with zipfile.ZipFile(pathlib.Path(wheel_directory)/wheel,"w") as archive:
        for source in (root/"src").rglob("*.py"):
            archive.write(source,str(source.relative_to(root/"src")).replace("\\\\","/"))
        archive.writestr(info+"/METADATA",metadata+"\\n")
        archive.writestr(info+"/WHEEL","Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        archive.writestr(info+"/RECORD","")
    return wheel
'''


@pytest.fixture
def build_inputs(tmp_path, monkeypatch):
    uv = shutil.which("uv")
    assert uv, "the existing plugin test runner requires uv"
    root = Path(__file__).resolve().parents[3]
    repo = tmp_path / "repo"
    for name, directory, module, sources in [
        ("agent-procutil", "procutil", "agent_procutil", ""),
        ("example-dependency", "dependency", "example_dependency", ""),
        ("example", "plugin", "example_module",
         '[tool.uv.sources]\nexample-dependency={path="../dependency",editable=true}\n'
         'agent-procutil={path="../procutil",editable=true}\n'),
    ]:
        package = _package(repo / directory, name)
        dependencies = 'dependencies=["example-dependency","agent-procutil"]\n' if name == "example" else ""
        (package / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="1.0.0"\n{dependencies}'
            '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
            + sources, encoding="utf-8",
        )
        (package / "backend.py").write_text(_WHEEL_BACKEND, encoding="utf-8")
        (package / "src" / "module.py").unlink()
        if name == "agent-procutil":
            target = package / "src" / module
            shutil.copytree(
                root / "libs" / "agent-procutil" / "src" / "agent_procutil", target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            (package / "src" / f"{module}.py").write_text(
                "from example_dependency import VALUE\n" if name == "example" else "VALUE=1\n",
                encoding="utf-8",
            )
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_NO_CACHE", "1")
    return repo / "plugin", Path(uv)


@pytest.fixture
def profile_inputs(build_inputs):
    plugin, uv = build_inputs
    with (plugin / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '[project.optional-dependencies]\nserver=[]\nstore=[]\n'
            'missing=["unavailable-profile-dependency==1.0"]\n',
        )
    return plugin, uv


@pytest.fixture
def role_inputs(build_inputs):
    plugin, uv = build_inputs
    for directory, name, dependencies, sources in (
        ("host", "example-host", 'dependencies=["example-host-leaf"]\n',
         '[tool.uv.sources]\nexample-host-leaf={path="../host-leaf",editable=true}\n'),
        ("host-leaf", "example-host-leaf", "", ""),
    ):
        package = _package(plugin.parent / directory, name)
        (package / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="1.0.0"\n{dependencies}'
            '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
            + sources, encoding="utf-8",
        )
        (package / "backend.py").write_text(_WHEEL_BACKEND, encoding="utf-8")
    with (plugin / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            'example-host={path="../host",editable=true}\n'
            '[project.optional-dependencies]\nclient=[]\nservice=["example-host"]\n',
        )
    return plugin, uv


@pytest.mark.parametrize("selection", [
    [], "example", ("../example",), ("EXAMPLE", "example"), (False,), ("unknown",),
])
def test_local_package_selection_refuses_before_runtime_writes(
    engine, tmp_path, build_inputs, selection,
):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="selection"):
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module", local_packages=selection,
        ):
            pytest.fail("invalid local package selection cannot admit a candidate")
    assert not root.exists()


def test_local_package_profile_is_normalized_and_keeps_full_source_identity(
    engine, role_inputs, tmp_path,
):
    plugin, _uv = role_inputs
    packages = engine._package_roots(plugin)
    selection = engine._selected_local_packages(
        plugin, packages, ("EXAMPLE_Dependency", "AGENT.procutil"),
    )
    assert selection == ("agent-procutil", "example", "example-dependency")
    files = engine._source_files(packages)
    before = engine._construction_hash(files, ("client",), selection)
    assert before != engine._construction_hash(files, ("client",))
    host = plugin.parent / "host" / "src" / "module.py"
    host.write_text("VALUE=2\n", encoding="utf-8")
    assert engine._construction_hash(files, ("client",), selection) != before
    assert engine._selected_local_packages(plugin, packages, ()) == ("example",)
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="immutable numbered"):
        with engine.dev_candidate(
            root, owner="role-test", plugin=plugin, uv=tmp_path / "unused",
            base_python=Path(sys.executable), health_module="example_module", local_packages=(),
        ):
            pytest.fail("mutable development must not accept role selection")
    assert not root.exists()


@pytest.mark.timeout(180)
def test_selected_local_role_uses_one_retained_graph_and_exact_installed_closure(
    engine, tmp_path, role_inputs, monkeypatch,
):
    from contextlib import contextmanager

    plugin, uv = role_inputs
    root = tmp_path / "runtime"
    arguments = dict(
        plugin=plugin, uv=uv, base_python=Path(sys.executable), health_module="example_module",
    )
    client = ("agent-procutil", "example-dependency")
    retain = engine.retain_uv_install
    transactions = []

    @contextmanager
    def trace(*args, **kwargs):
        with retain(*args, **kwargs) as transaction:
            transactions.append(transaction)
            assert set(transaction.sources) == set(engine._package_roots(plugin))
            constraints = Path(transaction.argv[transaction.argv.index("--constraint") + 1])
            assert constraints.read_text(encoding="utf-8") == (
                "example-host<0,>=0\nexample-host-leaf<0,>=0\n"
            )
            for name in ("host", "host-leaf"):
                assert str(transaction.sources[plugin.parent / name]) not in transaction.argv
                assert str(transaction.sources[plugin.parent / name] / "pyproject.toml") not in transaction.argv
            yield transaction

    monkeypatch.setattr(engine, "retain_uv_install", trace)
    with engine.numbered_candidate(
        root, "1.0.0+client", extras=("client",), local_packages=client, **arguments,
    ) as built:
        assert not built.reused
        assert built.local_packages == ("agent-procutil", "example", "example-dependency")
        assert len(transactions) == 1
        evidence = built.slot / ".runtime-input-origins.json"
        receipt = json.loads(evidence.read_text(encoding="utf-8"))
        assert set(receipt["packages"]) == set(built.local_packages)
        assert receipt["local_packages"] == list(built.local_packages)
    assert not built.frozen_plugin.exists()
    assert all(not path.exists() for path in transactions[0].sources.values())

    def refuse_construction(*args, **kwargs):
        pytest.fail("healthy role reuse cannot construct")

    with monkeypatch.context() as reuse:
        reuse.setattr(engine.runtime, "construction_lease", refuse_construction)
        with engine.numbered_candidate(
            root, "1.0.0+client", extras=("client",), local_packages=tuple(reversed(client)),
            **{**arguments, "uv": tmp_path / "absent", "base_python": tmp_path / "absent-python"},
        ) as existing:
            assert existing.reused and existing.local_packages == built.local_packages
        for selection in (None, (), (*client, "example-host")):
            with pytest.raises(RuntimeError, match="different content"):
                with engine.numbered_candidate(
                    root, "1.0.0+client", extras=("client",), local_packages=selection, **arguments,
                ):
                    pytest.fail("another local closure cannot reuse the selected role")
        receipt["local_packages"] = []
        evidence.write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(engine.HealthRepairRequired, match="selected local packages"):
            with engine.numbered_candidate(
                root, "1.0.0+client", extras=("client",), local_packages=client, **arguments,
            ):
                pytest.fail("changed local closure evidence must refuse reuse")

    monkeypatch.setattr(engine, "retain_uv_install", retain)
    service = (*client, "example-host", "example-host-leaf")
    with engine.numbered_candidate(
        root, "1.0.0+service", extras=("service",), local_packages=service, **arguments,
    ) as host:
        assert not host.reused
        assert set(host.local_packages) == {
            "example", "agent-procutil", "example-dependency", "example-host", "example-host-leaf",
        }
        assert set(json.loads((host.slot / ".runtime-input-origins.json").read_text())["packages"]) == (
            set(host.local_packages)
        )
        assert engine.runtime.is_complete(root, host.slot.name, expect_hash=host.payload_hash)
    assert not (root / "current-version").exists()


@pytest.mark.timeout(180)
@pytest.mark.parametrize("selection", [
    ("agent-procutil", "example-dependency"),
    ("agent-procutil", "example-dependency", "example-host"),
])
def test_incomplete_local_role_cannot_fall_back_or_complete(
    engine, role_inputs, tmp_path, monkeypatch, selection,
):
    plugin, uv = role_inputs
    monkeypatch.setenv("UV_PRERELEASE", "allow")
    for directory in ("host", "host-leaf"):
        manifest = plugin.parent / directory / "pyproject.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace('version="1.0.0"', 'version="0.0.dev1"'),
            encoding="utf-8",
        )
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("previous\n", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        with engine.numbered_candidate(
            root, "1.0.0+service", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module", extras=("service",), local_packages=selection,
        ):
            pytest.fail("missing direct or transitive local dependency cannot construct")
    assert not engine.runtime.is_complete(root, "1.0.0+service")
    assert (root / "current-version").read_text(encoding="utf-8") == "previous\n"


@pytest.mark.timeout(180)
def test_deep_local_role_keeps_generated_constraints_out_of_source_ancestry(
    engine, tmp_path, role_inputs,
):
    plugin, uv = role_inputs
    deep = tmp_path / ("caller-" + "x" * 80) / "workspace" / "repo"
    deep.parent.mkdir(parents=True)
    shutil.move(str(plugin.parent), str(deep))
    plugin = deep / plugin.name
    packages = engine._package_roots(plugin)
    files = engine._source_files(packages)
    before = engine.runtime.fingerprint_source(files)
    inputs = engine.FrozenInputs(plugin, packages, before)
    with engine._retained_inputs(inputs, uv, Path(sys.executable), refresh=False) as legacy:
        baseline_depth = len(legacy.plugin.parts)
    with engine.numbered_candidate(
        tmp_path / "runtime", "1.0.0+client", plugin=plugin, uv=uv,
        base_python=Path(sys.executable), health_module="example_module",
        extras=("client",), local_packages=("agent-procutil", "example-dependency"),
    ) as candidate:
        assert not candidate.reused
        assert len(candidate.frozen_plugin.parts) == baseline_depth
        assert len(str(candidate.frozen_plugin)) < 260
        assert engine.runtime.is_complete(
            tmp_path / "runtime", candidate.slot.name, expect_hash=candidate.payload_hash,
        )
    assert engine._source_files(packages) == files
    assert engine.runtime.fingerprint_source(files) == before


@pytest.mark.timeout(180)
def test_role_health_refuses_an_excluded_installed_distribution(
    engine, role_inputs, tmp_path, monkeypatch,
):
    plugin, uv = role_inputs
    construct = engine._construct

    def extra_distribution(*args, **kwargs):
        python = construct(*args, **kwargs)
        slot = args[1]
        metadata = next(slot.rglob("example-1.0.0.dist-info")).with_name("example_host-1.0.0.dist-info")
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: example-host\nVersion: 1.0.0\n", encoding="utf-8",
        )
        return python

    monkeypatch.setattr(engine, "_construct", extra_distribution)
    root = tmp_path / "runtime"
    with pytest.raises(subprocess.CalledProcessError) as caught:
        with engine.numbered_candidate(
            root, "1.0.0+client", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module", extras=("client",),
            local_packages=("agent-procutil", "example-dependency"),
        ):
            pytest.fail("excluded installed dependencies cannot pass role health")
    assert "excluded local package is installed: example-host" in caught.value.stderr
    assert not engine.runtime.is_complete(root, "1.0.0+client")


@pytest.mark.timeout(120)
def test_selected_extras_use_retained_uv_and_cannot_reuse_another_profile(
    engine, tmp_path, profile_inputs, monkeypatch,
):
    from contextlib import contextmanager

    plugin, uv = profile_inputs
    root = tmp_path / "runtime"
    retain = engine.retain_uv_install
    transactions = []

    @contextmanager
    def trace(*args, **kwargs):
        with retain(*args, **kwargs) as transaction:
            transactions.append(transaction)
            yield transaction

    monkeypatch.setattr(engine, "retain_uv_install", trace)
    arguments = dict(
        plugin=plugin, uv=uv, base_python=Path(sys.executable), health_module="example_module",
    )
    with engine.numbered_candidate(
        root, "1.0.0+host", extras=("STORE", "SERVER"), **arguments,
    ) as built:
        assert not built.reused and built.extras == ("server", "store")
        assert engine.runtime.is_complete(root, "1.0.0+host", expect_hash=built.payload_hash)
        assert len(transactions) == 1
        target = str(transactions[0].sources[plugin]) + "[server,store]"
        assert target in transactions[0].argv
        evidence = built.slot / ".runtime-input-origins.json"
        receipt = json.loads(evidence.read_text(encoding="utf-8"))
        assert receipt["extras"] == ["server", "store"]
        assert receipt["packages"]["example"] == built.frozen_plugin.as_uri()
        assert built.payload_hash == engine._construction_hash(
            engine._source_files(engine._package_roots(plugin)), built.extras,
        )
    assert not built.frozen_plugin.exists()

    def refuse_construction(*args, **kwargs):
        pytest.fail("completed profile reuse must not construct")

    monkeypatch.setattr(engine.runtime, "construction_lease", refuse_construction)
    arguments.update(uv=tmp_path / "absent-uv", base_python=tmp_path / "absent-python")
    with engine.numbered_candidate(
        root, "1.0.0+host", extras=("server", "store"), **arguments,
    ) as reused:
        assert reused.reused and reused.extras == built.extras
    for extras in ((), ("server",), ("store",)):
        with pytest.raises(RuntimeError, match="different content"):
            with engine.numbered_candidate(root, "1.0.0+host", extras=extras, **arguments):
                pytest.fail("another profile cannot reuse the same immutable slot")
    assert len(transactions) == 1
    receipt["extras"] = []
    evidence.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(engine.HealthRepairRequired, match="selected dependency profile"):
        with engine.numbered_candidate(
            root, "1.0.0+host", extras=("server", "store"), **arguments,
        ):
            pytest.fail("profile evidence must be checked on healthy reuse")


@pytest.mark.timeout(120)
def test_selected_extra_dependency_failure_cannot_complete_or_activate(
    engine, tmp_path, profile_inputs,
):
    plugin, uv = profile_inputs
    root = tmp_path / "runtime"
    arguments = dict(
        plugin=plugin, uv=uv, base_python=Path(sys.executable), health_module="example_module",
    )
    with engine.numbered_candidate(root, "1.0.0", **arguments) as base:
        assert not base.extras
        assert base.payload_hash == engine.runtime.fingerprint_source(
            engine._source_files(engine._package_roots(plugin)),
        )
        assert "extras" not in json.loads(
            (base.slot / ".runtime-input-origins.json").read_text(encoding="utf-8"),
        )
    (root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        with engine.numbered_candidate(root, "1.0.0+missing", extras=("missing",), **arguments):
            pytest.fail("unavailable selected dependency cannot produce a ready candidate")
    assert not engine.runtime.is_complete(root, "1.0.0+missing")
    assert (root / "current-version").read_text(encoding="utf-8") == "1.0.0\n"
    assert not list((root / ".installer-inputs").iterdir())


def test_numbered_construction_uses_real_uv_and_remains_frozen_after_source_change(
    engine, procutil, tmp_path, build_inputs, monkeypatch,
):
    from contextlib import contextmanager

    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    retain = engine.retain_uv_install
    transactions = []

    @contextmanager
    def trace_retention(*args, **kwargs):
        with retain(*args, **kwargs) as transaction:
            transactions.append(transaction)
            yield transaction

    def refuse_second_snapshot(*_args, **_kwargs):
        pytest.fail("construction must not create an engine-owned second snapshot")

    monkeypatch.setattr(engine, "retain_uv_install", trace_retention)
    monkeypatch.setattr(engine, "freeze_inputs", refuse_second_snapshot)
    with engine.numbered_candidate(
        root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
        health_module="example_module",
    ) as candidate:
        assert not candidate.reused
        assert len(transactions) == 1
        assert candidate.frozen_plugin == transactions[0].sources[plugin]
        assert engine.runtime.is_complete(root, "1.0.0", expect_hash=candidate.payload_hash)
        engine.runtime.gc(root, keep=[], min_age_days=0, protect_pids=False)
        assert candidate.slot.is_dir(), "ready candidate must survive concurrent cleanup"
        installed = candidate.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        (plugin.parent / "dependency" / "src" / "example_dependency.py").write_text("VALUE=2\n")
        probe = subprocess.run(
            [str(installed), "-I", "-B", "-c", "import example_module;print(example_module.VALUE)"],
            capture_output=True, text=True, check=True, timeout=10,
            **procutil.no_window_kwargs(),
        )
        assert probe.stdout.strip() == "1"
    assert not list((root / ".installer-inputs").iterdir())
    assert not candidate.frozen_plugin.exists()


@pytest.mark.timeout(120)
def test_nested_control_and_dev_retention_does_not_accumulate_source_ancestry(
    engine, tmp_path, build_inputs,
):
    plugin, uv = build_inputs
    deep = tmp_path / ("caller-" + "x" * 80) / "workspace" / "repo"
    deep.parent.mkdir(parents=True)
    shutil.move(str(plugin.parent), str(deep))
    plugin = deep / plugin.name
    packages = engine._package_roots(plugin)
    inputs = engine.FrozenInputs(
        plugin, packages, engine.runtime.fingerprint_source(engine._source_files(packages)),
    )
    with engine._retained_inputs(inputs, uv, Path(sys.executable), refresh=False) as control:
        assert control.retained_hash
        retained_source = engine.FrozenInputs(
            control.plugin, control.packages, control.retained_hash,
        )
        with engine._retained_inputs(
            retained_source, uv, Path(sys.executable), refresh=True,
        ) as dev:
            assert len(dev.plugin.parts) == len(control.plugin.parts)
            assert max(len(str(path)) for path in (*control.packages, *dev.packages)) < 260
            assert dev.transaction.cwd == dev.plugin


@pytest.mark.timeout(120)
def test_deep_runtime_keeps_short_frozen_inputs_and_checks_installed_origins(
    engine, procutil, tmp_path, build_inputs,
):
    plugin, uv = build_inputs
    root = tmp_path / ("namespace-" + "n" * 85) / ("cell-" + "c" * 60) / "runtime"
    arguments = dict(
        plugin=plugin, uv=uv, base_python=Path(sys.executable), health_module="example_module",
    )
    with engine.numbered_candidate(root, "1.0.0", **arguments) as candidate:
        assert not candidate.reused
        frozen_plugin = candidate.frozen_plugin
        if os.name == "nt":
            assert len(str(frozen_plugin)) < 260
            assert len(str(engine.runtime.slot_python(root, "1.0.0"))) > 260
            assert not frozen_plugin.is_relative_to(root)
        evidence = candidate.slot / ".runtime-input-origins.json"
        snapshot = evidence.read_bytes()
        receipt = json.loads(snapshot)
        assert set(receipt["packages"]) == {"example", "example-dependency", "agent-procutil"}
        assert receipt["packages"]["example"] == frozen_plugin.as_uri()
    assert not frozen_plugin.exists()
    arguments.update(uv=tmp_path / "uv-must-not-run", base_python=tmp_path / "missing-python")
    with engine.numbered_candidate(root, "1.0.0", **arguments) as reused:
        assert reused.reused and evidence.read_bytes() == snapshot
    receipt["packages"]["example"] = "file:///unrelated-source"
    corrupt = json.dumps(receipt).encode("utf-8")
    evidence.write_bytes(corrupt)
    with pytest.raises(engine.HealthRepairRequired, match="recorded frozen installation origin"):
        with engine.numbered_candidate(root, "1.0.0", **arguments):
            pytest.fail("corrupt installed origin evidence cannot select an ordinary rebuild")
    assert evidence.read_bytes() == corrupt


@pytest.mark.timeout(120)
def test_fresh_health_requires_its_frozen_origin_receipt(
    engine, tmp_path, build_inputs, monkeypatch,
):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"

    def remove_evidence(_inputs, slot, *_args):
        (slot / ".runtime-input-origins.json").unlink()

    monkeypatch.setattr(engine, "_prepare_installed", remove_evidence)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module", construction_callable="fixture:remove_evidence",
        ):
            pytest.fail("missing fresh provenance cannot publish completion")
    assert "fresh runtime has no frozen origin evidence" in caught.value.stderr
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    with engine.runtime.construction_lease(root, "1.0.0", timeout=0):
        pass


def test_real_first_builders_coalesce_and_release_construction_before_settlement(
    engine, procutil, tmp_path, build_inputs, monkeypatch,
):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    source = Path(__file__).resolve().parents[3]
    counter = tmp_path / "build-counter"
    counter.mkdir()
    monkeypatch.setenv("TEST_BUILD_COUNTER", str(counter))
    code = (
        "import pathlib,sys,json\n"
        f"sys.path[:0]=[{str(source / 'libs' / 'installer-engine')!r},"
        f"{str(source / 'plugins' / 'agent-worktrees' / 'scripts')!r}]\n"
        "import installer_engine as engine\n"
        f"root=pathlib.Path({str(root)!r})\n"
        f"with engine.numbered_candidate(root,'1.0.0',plugin=pathlib.Path({str(plugin)!r}),"
        f"uv=pathlib.Path({str(uv)!r}),base_python=pathlib.Path(sys.executable),"
        "health_module='example_module') as candidate:\n"
        " with engine.runtime.construction_lease(root,'1.0.0',timeout=10):\n"
        "  print(json.dumps({'reused':candidate.reused,'hash':candidate.payload_hash}),flush=True)\n"
    )
    processes = []
    try:
        for _ in range(2):
            processes.append(procutil.spawn_sync_in_kill_on_close_job(
                [sys.executable, "-I", "-c", code],
                require_job=True, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **procutil.no_window_kwargs(),
            ))
        results = []
        for process, _ in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stdout + stderr
            results.append(json.loads(stdout))
        assert sorted(result["reused"] for result in results) == [False, True]
        assert results[0]["hash"] == results[1]["hash"]
        assert sorted(path.name for path in counter.iterdir()) == [
            "agent-procutil", "example", "example-dependency",
        ], "every package must have exactly one real construction"
        assert not list((root / ".installer-inputs").iterdir())
    finally:
        for process, job in processes:
            if job is not None:
                job.terminate_and_wait(10)
            elif process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


def test_numbered_reuse_never_takes_construction_or_touches_published_bytes(
    engine, tmp_path, build_inputs, monkeypatch,
):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    with engine.numbered_candidate(
        root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
        health_module="example_module",
    ) as built:
        slot = built.slot
    before = {file: (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns)
              for file in slot.rglob("*") if file.is_file()}

    def refuse_construction(*args, **kwargs):
        pytest.fail("completed reuse must not take an exclusive construction lease")

    monkeypatch.setattr(engine.runtime, "construction_lease", refuse_construction)
    with engine.numbered_candidate(
        root, "1.0.0", plugin=plugin, uv=tmp_path / "missing-uv",
        base_python=tmp_path / "missing-bootstrap",
        health_module="example_module",
    ) as candidate:
        assert candidate.reused
    assert before == {file: (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns)
                      for file in slot.rglob("*") if file.is_file()}
    (plugin / "src" / "example_module.py").write_text("VALUE=99\n")
    with pytest.raises(RuntimeError, match="different content"):
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module",
        ):
            pytest.fail("conflicting content cannot be admitted")
    assert before == {file: (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns)
                      for file in slot.rglob("*") if file.is_file()}


def test_failed_isolated_health_never_publishes_or_selects(engine, tmp_path, build_inputs):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("0.9\n")
    with pytest.raises(subprocess.CalledProcessError, match="returned non-zero"):
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="missing_health_module",
        ):
            pytest.fail("an unhealthy candidate cannot be published")
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    assert (root / "current-version").read_text() == "0.9\n"
    assert not list((root / ".installer-inputs").iterdir())


@pytest.mark.parametrize("fail_preparation", [False, True])
def test_installed_preparation_precedes_health_and_never_runs_on_reuse(
    engine, tmp_path, build_inputs, fail_preparation,
):
    plugin, uv = build_inputs
    (plugin / "src" / "example_module.py").write_text(
        "import pathlib,sys\n"
        "def prepare(data):\n"
        " assert not (pathlib.Path(sys.prefix)/'.install-complete.json').exists()\n"
        " if data['fail']: raise RuntimeError('preparation refused')\n"
        " pathlib.Path(__file__).with_suffix('.prepared').write_text(data['value'])\n"
        "def health():\n"
        " assert pathlib.Path(__file__).with_suffix('.prepared').read_text() == 'ready'\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    arguments = dict(
        plugin=plugin, uv=uv, base_python=Path(sys.executable),
        health_module="example_module", health_callable="health",
        construction_callable="example_module:prepare",
        construction_data={"fail": fail_preparation, "value": "ready"},
    )
    if fail_preparation:
        with pytest.raises(subprocess.CalledProcessError) as failure:
            with engine.numbered_candidate(root, "1.0.0", **arguments):
                pytest.fail("failed preparation must not publish")
        assert "preparation refused" in failure.value.stderr
        assert not engine.runtime.marker_path(root, "1.0.0").exists()
        return
    with engine.numbered_candidate(root, "1.0.0", **arguments) as candidate:
        assert not candidate.reused
    arguments.update(
        uv=tmp_path / "missing-uv", base_python=tmp_path / "missing-python",
        construction_callable="example_module:must_not_run_on_reuse",
    )
    with engine.numbered_candidate(root, "1.0.0", **arguments) as candidate:
        assert candidate.reused


def test_health_callable_exercises_lazy_imports_before_publication(engine, tmp_path, build_inputs):
    plugin, uv = build_inputs
    source = plugin / "src" / "example_module.py"
    source.write_text(
        "def load_surface():\n    import missing_lazy_dependency\n", encoding="utf-8",
    )
    root = tmp_path / "runtime"
    with pytest.raises(subprocess.CalledProcessError) as failure:
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module", health_callable="load_surface",
        ):
            pytest.fail("a module-only pass cannot satisfy the full-surface health gate")
    assert "missing_lazy_dependency" in failure.value.stderr
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    assert not (root / "current-version").exists()


def test_reuse_failed_direct_health_preserves_published_identity(engine, tmp_path, build_inputs):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    with engine.numbered_candidate(
        root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
        health_module="example_module",
    ) as candidate:
        slot = candidate.slot
    engine.runtime.activate(root, "1.0.0", link_free=True)
    installed = next(slot.glob("**/site-packages/example_module.py"))
    installed.write_text("raise RuntimeError('installed health is broken')\n", encoding="utf-8")
    before = {file: (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns)
              for file in slot.rglob("*") if file.is_file()}
    selectors = {name: (root / name).read_bytes() for name in ("current-version", "last-known-good")}
    with pytest.raises(engine.HealthRepairRequired) as failure:
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=tmp_path / "missing-uv",
            base_python=tmp_path / "missing-base", health_module="example_module",
        ):
            pytest.fail("unhealthy published reuse cannot become a rebuild")
    assert "installed health is broken" in str(failure.value)
    assert isinstance(failure.value.__cause__, subprocess.CalledProcessError)
    assert before == {file: (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns)
                      for file in slot.rglob("*") if file.is_file()}
    assert selectors == {name: (root / name).read_bytes() for name in selectors}


@pytest.mark.parametrize("failure", ["unexecutable", "timeout"])
def test_completed_runtime_launch_failure_requests_distinct_repair(
    engine, tmp_path, build_inputs, failure,
):
    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    arguments = dict(plugin=plugin, uv=uv, base_python=Path(sys.executable), health_module="example_module")
    with engine.numbered_candidate(root, "1.0.0", **arguments) as candidate:
        slot = candidate.slot
    engine.runtime.activate(root, "1.0.0", link_free=True)
    if failure == "unexecutable":
        engine.runtime.slot_python(root, "1.0.0").write_bytes(b"not an executable")
    else:
        next(slot.glob("**/site-packages/example_module.py")).write_text(
            "import time\ntime.sleep(60)\n", encoding="utf-8",
        )
    before = {path: path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    selectors = {name: (root / name).read_bytes() for name in ("current-version", "last-known-good")}
    with pytest.raises(engine.HealthRepairRequired):
        with engine.numbered_candidate(root, "1.0.0", child_timeout=1, **arguments):
            pytest.fail("broken completed health cannot license an in-place rebuild")
    assert before == {path: path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    assert selectors == {name: (root / name).read_bytes() for name in selectors}


@pytest.mark.parametrize("failure", [
    OSError("cleanup accounting failed"),
    subprocess.TimeoutExpired(["cleanup"], 1),
    RuntimeError("ownership refused"),
])
def test_reused_health_does_not_classify_cleanup_or_ownership_failure_as_repair(
    engine, tmp_path, monkeypatch, failure,
):
    monkeypatch.setattr(engine, "_input_origins", lambda inputs: {})

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(engine, "_child", failed)
    with pytest.raises(type(failure)) as caught:
        engine._verify_installed(
            engine.FrozenInputs(tmp_path, (), "unused"),
            tmp_path, tmp_path / "python", "example", 0, 1, require_frozen_origin=False,
        )
    assert caught.value is failure


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job accounting failure")
def test_unsettled_job_teardown_keeps_frozen_inputs_and_construction_custody(
    engine, procutil, tmp_path, build_inputs, monkeypatch,
):
    from types import SimpleNamespace
    from native_installer_guardian_teardown import write_procutil

    plugin, uv = build_inputs
    root = tmp_path / "runtime"
    marker = tmp_path / "writers"
    observations = tmp_path / "teardown.json"
    injected = tmp_path / "guardian_procutil.py"
    observed_inputs = []
    original = engine._procutil

    def instrument(inputs):
        observed_inputs.append(inputs)
        owned = original(inputs)
        write_procutil(
            injected, procutil=Path(owned.__file__), runtime=Path(engine.runtime.__file__),
            root=root, frozen=inputs.plugin, marker=marker, observations=observations,
            writer=_writer_tree(marker),
        )
        return SimpleNamespace(__file__=injected, no_window_kwargs=owned.no_window_kwargs)

    monkeypatch.setattr(engine, "_procutil", instrument)
    with pytest.raises(RuntimeError, match="guardian did not confirm settlement"):
        with engine.numbered_candidate(
            root, "1.0.0", plugin=plugin, uv=uv, base_python=Path(sys.executable),
            health_module="example_module", child_timeout=2,
        ):
            pytest.fail("a teardown failure cannot publish a candidate")
    entries = json.loads(observations.read_text())
    assert len(entries) == 2 and entries[0]["writers_live"] and entries[1]["writers_settled"]
    assert all(entry["frozen"] and entry["construction_blocked"]
               and entry["retention_blocked"] and entry["cleanup_blocked"]
               and not entry["complete"] for entry in entries)
    _assert_writers_dead(engine, entries[1]["pids"])
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    assert not list((root / ".installer-inputs").iterdir())
    assert not observed_inputs[0].plugin.exists()
    with engine.runtime.construction_lease(root, "1.0.0", timeout=0):
        with engine.runtime._cleanup_lease(root, "1.0.0", allow_construction_owner=True) as admitted:
            assert admitted
