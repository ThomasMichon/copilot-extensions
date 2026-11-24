"""First-touch native handoff admission through the real wheel application."""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast
from zipfile import ZipFile

import agent_procutil as pu
from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
from agent_procutil.daemon_instances import InstanceCleanupResult, daemon_test_lifecycle
from agent_procutil.owned_process import run_caller_owned
import pytest

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "plugins" / "agent-worktrees"


class _ReplacementOptions(TypedDict):
    shell: str
    cwd: Path
    environment: dict[str, str]
    expected_generation: int


class _RestoreOptions(TypedDict):
    shell: str
    cwd: Path
    environment: dict[str, str]


class _PreparedDecision(TypedDict):
    nonce: str
    script: str
    arguments: list[str]
    completion: list[str]


class _SandboxFactory(Protocol):
    def __call__(self, *, prefix: str, dir: Path) -> AbstractContextManager[str]: ...


class _SourceIdentity(Protocol):
    source_commit: str
    source_tree: str

    def document(self) -> dict[str, object]: ...


class _RuntimeSource(Protocol):
    SourceIdentity: Callable[[str, str, str, str, str, str], _SourceIdentity]

    def assembly_hash(self, source: Path) -> str: ...

    def runtime_version(self, version: str, commit: str, source_hash: str) -> str: ...


class _RuntimeMarkers(Protocol):
    COMPLETE_MARKER: str


class _InstallerEngine(Protocol):
    runtime_source: _RuntimeSource
    runtime: _RuntimeMarkers


class _SourceInputs(Protocol):
    engine: _InstallerEngine
    MANIFEST: str
    SCHEMA: str


class _ProfileExports(Protocol):
    source_inputs: _SourceInputs


class _TomlReader(Protocol):
    def loads(self, content: str) -> dict[str, Any]: ...


class _BuilderExports(Protocol):
    tomllib: _TomlReader


class _ManagerEnvironment(Protocol):
    def __call__(
        self, home: Path, root: Path, tmp_path: Path, interpreter: str, *,
        caller_jobs: bool, worktrees_environment: dict[str, str],
    ) -> dict[str, str]: ...


class _ManagerApplication(Protocol):
    def __call__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control: str, *,
        worktrees_python: Path, worktrees_root: Path, worktrees_environment: dict[str, str],
        caller_jobs: bool, replacement_slot: Path | None, rollback_application: Path | None,
    ) -> None: ...


class _NativeStartup(Protocol):
    def __call__(
        self, tmp_path: Path, normalized_setup: bool, *,
        command_wrapper: Callable[[list[str], dict[str, str], Path], list[str]],
        environment: dict[str, str],
    ) -> None: ...


def _run(
    arguments: Sequence[str | Path | None], *, cwd: Path, env: dict[str, str], deadline: float,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(map(str, arguments)), cwd=cwd, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=max(0.01, deadline - time.monotonic()),
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _copy_application_inputs(source: Path, destination: Path) -> None:
    for entry in source.iterdir():
        if entry.is_file():
            shutil.copyfile(entry, destination / entry.name)
        elif entry.name in {"src", "scripts", "libs"}:
            shutil.copytree(
                entry, destination / entry.name,
                ignore=shutil.ignore_patterns(
                    ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "*.egg-info",
                ),
            )


def test_application_inputs_preserve_materialized_local_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder, materialize_main as materializer
    else:
        import build_python_artifacts as builder
        import materialize_main as materializer

    original, copied = tmp_path / "materialized", tmp_path / "copied"
    module = original / "libs" / "widget" / "src" / "widget" / "__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text("VALUE = 'fixture'\n", encoding="utf-8")
    (original / "libs" / "widget" / "pyproject.toml").write_text(
        '[project]\nname = "demo-widget"\nversion = "0.1.0"\n', encoding="utf-8",
    )
    (original / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[tool.uv.sources]\ndemo-widget = { path = "libs/widget" }\n',
        encoding="utf-8",
    )
    copied.mkdir()
    _copy_application_inputs(original, copied)
    materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=original, dest_consumer_dir=copied, canonical_root=tmp_path,
    )
    dependencies = dict(builder.resolve_vendored_libs(copied))
    assert dependencies["widget"] == (copied / "libs" / "widget").resolve()
    assert (dependencies["widget"] / "src" / "widget" / "__init__.py").read_bytes() == module.read_bytes()


@pytest.mark.parametrize("backends", [0, 1, 2])
def test_native_backend_receipt_selection_preserves_launcher_stages(tmp_path: Path, backends: int) -> None:
    from test_headed_worktrees_copilot import _backend_execution

    documents = {}
    for name in ("launcher", *(f"backend-{index}" for index in range(backends))):
        path = tmp_path / (name + ".execution.json")
        document = json.dumps({"events": [{
            "phase": "process-spawned",
            "details": {"boundary": "launcher-command" if name == "launcher" else "backend-command"},
        }]})
        path.write_text(document, encoding="utf-8")
        documents[path] = document
    if backends > 1:
        with pytest.raises(AssertionError, match="more than one native backend"):
            _backend_execution(tmp_path)
    elif backends:
        path, receipt = cast(tuple[Path, dict[str, Any]], _backend_execution(tmp_path))
        assert path.name == "backend-0.execution.json"
        assert receipt["events"][0]["details"]["boundary"] == "backend-command"
    else:
        assert _backend_execution(tmp_path) is None
    assert {path: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()} == documents


def _compile_private_mux_echo(tmp_path: Path, version: str | None) -> Path:
    compiler = next((path for framework in ("Framework64", "Framework") if (
        path := Path(os.environ.get("WINDIR", "")) / "Microsoft.NET" /
        framework / "v4.0.30319" / "csc.exe"
    ).is_file()), None)
    assert compiler, "The fixture's private mux adapter requires the system C# compiler"
    backend = tmp_path / "echo tools"
    backend.mkdir()
    source, binary = backend / "Echo.cs", backend / "psmux.exe"
    metadata = (
        f'[assembly: System.Reflection.AssemblyFileVersion("{version}")]\n' if version else ""
    )
    source.write_text(
        "using System; using System.Text;\n"
        + metadata +
        "class Echo { static void Main(string[] args) {\n"
        "Console.WriteLine(Environment.CommandLine);\n"
        "foreach (var value in args) Console.WriteLine("
        "Convert.ToBase64String(Encoding.UTF8.GetBytes(value)));\n"
        "} }\n",
        encoding="utf-8",
    )
    compiled = run_caller_owned(
        [str(compiler), "/nologo", f"/out:{binary}", str(source)], cwd=tmp_path, timeout=30,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return binary


@pytest.mark.skipif(os.name != "nt", reason="Native Windows command-line quoting and file metadata")
@pytest.mark.parametrize("version", ["3.4.0.0", "3.5.1.7"])
def test_private_mux_adapter_preserves_census_command_shape(tmp_path: Path, version: str) -> None:
    from agent_remote_driver import windows_mux_processes, windows_mux_version
    from test_headed_worktrees_copilot import _prepare_private_mux_adapter

    binary = _compile_private_mux_echo(tmp_path, version)
    directory, namespace_file = _prepare_private_mux_adapter(
        tmp_path, binary=binary, manager_target="wt-fixture", environment=dict(os.environ),
    )
    adapter = directory / "psmux.exe"
    assert windows_mux_version.file_version(str(adapter)) == tuple(map(int, version.split(".")))
    assert Path(windows_mux_version.require_creation_binary(
        str(adapter), search_roots=(),
    )).samefile(adapter)
    arguments = ["--version"]
    result = run_caller_owned(
        [str(directory / "psmux.exe"), *arguments], cwd=tmp_path, timeout=15,
        max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    command, *encoded = result.stdout.splitlines()
    assert [
        base64.b64decode(value).decode("utf-8") for value in encoded
    ] == ["-L", namespace_file.read_text(encoding="utf-8"), *arguments]
    match = windows_mux_processes._COMMAND.match(command)
    assert match is not None, command
    assert match[1] == "-L" and match[2] == "--version"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows file metadata")
@pytest.mark.parametrize("version", [None, "3.3.9.0"])
def test_private_mux_adapter_refuses_unqualified_backing_binary(
    tmp_path: Path, version: str | None,
) -> None:
    from agent_remote_driver import windows_mux_version
    from test_headed_worktrees_copilot import _prepare_private_mux_adapter

    binary = _compile_private_mux_echo(tmp_path, version)
    with pytest.raises(RuntimeError, match="readable file metadata"):
        windows_mux_version.require_creation_binary(str(binary), search_roots=())
    with pytest.raises(AssertionError, match="backing binary with qualified file metadata"):
        _prepare_private_mux_adapter(
            tmp_path, binary=binary, manager_target="wt-fixture", environment=dict(os.environ),
        )
    assert not (tmp_path / "discovery").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows file metadata")
def test_private_mux_adapter_refuses_changed_backing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_headed_worktrees_copilot as fixture

    binary = _compile_private_mux_echo(tmp_path, "3.4.0.0")
    original = run_caller_owned

    def compile_then_change(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        binary.write_bytes(binary.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(fixture, "run_caller_owned", compile_then_change)
    with pytest.raises(AssertionError, match="backing binary changed during compilation"):
        fixture._prepare_private_mux_adapter(
            tmp_path, binary=binary, manager_target="wt-fixture", environment=dict(os.environ),
        )


def test_public_fixture_git_mutations_ignore_inherited_repository_selectors(tmp_path: Path) -> None:
    from agent_worktrees.git_repository_environment import repository_identity_env
    from test_headed_worktrees_copilot import _initialize_fixture_repository

    environment = repository_identity_env()
    git = shutil.which("git", path=environment.get("PATH"))
    assert git

    def command(root: Path, *arguments: str) -> str:
        result = run_caller_owned(
            [git, *arguments], cwd=root, env=environment, timeout=15,
            max_stdout_bytes=8192, max_stderr_bytes=8192,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return cast(str, result.stdout.strip())

    foreign, checkout = tmp_path / "foreign", tmp_path / "checkout"
    foreign.mkdir()
    checkout.mkdir()
    command(foreign, "init", "--initial-branch=dev")
    (foreign / "untouched.txt").write_text("outside fixture", encoding="utf-8")
    command(foreign, "add", "untouched.txt")
    command(
        foreign, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "Unrelated fixture",
    )
    before = command(foreign, "rev-parse", "HEAD"), (foreign / ".git/index").read_bytes()
    authority = checkout / ".copilot-extensions/agent-worktrees/config.yaml"
    authority.parent.mkdir(parents=True)
    authority.write_text("default_branch: dev\n", encoding="utf-8")
    _initialize_fixture_repository(checkout, {
        **environment, "GIT_DIR": str(foreign / ".git"),
        "GIT_COMMON_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign),
        "GIT_INDEX_FILE": str(foreign / ".git/index"),
    })
    assert Path(command(checkout, "rev-parse", "--show-toplevel")).samefile(checkout)
    assert command(checkout, "show", "HEAD:.copilot-extensions/agent-worktrees/config.yaml")
    assert (command(foreign, "rev-parse", "HEAD"), (foreign / ".git/index").read_bytes()) == before
    assert (foreign / "untouched.txt").read_text(encoding="utf-8") == "outside fixture"


@pytest.mark.parametrize("control", ["manager-no-mux", "manager-mux", "embody", "copilot"])
def test_fixture_record_matches_receiving_namespace_before_native_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control: str,
) -> None:
    from agent_worktrees import config, record_reporting, tracking
    from agent_worktrees.execution_spaces import ExecutionSpaceError
    from test_headed_worktrees_copilot import _fixture_record_source

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(config, "_home", lambda: home)
    worktree_id = "driver-fixture-record"
    checkout = tmp_path / worktree_id
    checkout.mkdir()
    monkeypatch.setattr(sys, "argv", [
        "fixture", str(checkout), worktree_id, control, str(tmp_path / "discovery"), "0",
    ])
    exec(compile(_fixture_record_source(), "<fixture-record>", "exec"), {})
    record = tracking.load_record(
        config.project_dir("native-copilot") / "worktrees" / (worktree_id + ".yaml"),
    )
    receiving = config.load_config(
        path=config.project_dir("native-copilot") / "config.yaml", project="native-copilot",
    )
    record_reporting.require_current_subject(record, receiving)
    receiving = replace(receiving, repos={"native-copilot": replace(
        receiving.repos["native-copilot"], worktree_root=str(tmp_path / "outside"),
    )})
    with pytest.raises(ExecutionSpaceError, match="receiving worktree namespace"):
        record_reporting.require_current_subject(record, receiving)


@pytest.mark.timeout(300)
def test_fresh_application_admits_its_current_native_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualify_native_application(tmp_path, monkeypatch)


@pytest.mark.timeout(480)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed native Windows Copilot broker lane",
)
def test_fresh_application_owns_native_broker_fresh_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualify_native_application(tmp_path, monkeypatch, broker=True)


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed public Windows Worktrees embodiment lane",
)
def test_public_embody(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualify_public_application(monkeypatch, "embody")


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("WORKTREES_NATIVE_CALLER_JOBS") != "1",
    reason="Explicitly select the leased native caller-Job lifetime owner",
)
def test_public_caller_job_lifetime(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualify_public_application(monkeypatch, "embody", caller_jobs=True)


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("WORKTREES_NATIVE_CALLER_JOBS") != "1",
    reason="Explicitly select the leased native attached caller-Job lifetime owner",
)
def test_public_attached_caller_job_lifetime(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualify_public_application(monkeypatch, "copilot", caller_jobs=True)


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed public Windows Worktrees TTY lane",
)
def test_public_copilot(monkeypatch: pytest.MonkeyPatch) -> None:
    _qualify_public_application(monkeypatch, "copilot")


def _qualify_public_application(
    monkeypatch: pytest.MonkeyPatch, caller: str, *, caller_jobs: bool = False,
) -> None:
    TestSandbox = import_module("plugin_test_containment").TestSandbox
    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"])
    if caller_jobs:
        # The outer owner settles residents before removing this sandbox.
        directory = sandbox / "caller"
        directory.mkdir()
        _qualify_native_application(directory, monkeypatch, broker=caller, caller_jobs=True)
        return
    sandbox_factory = cast(_SandboxFactory, TestSandbox)
    with sandbox_factory(prefix="p-", dir=sandbox) as temporary_directory:
        _qualify_native_application(Path(temporary_directory), monkeypatch, broker=caller)


def test_public_application_workspace_is_outside_source_only_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if TYPE_CHECKING:
        from tools.plugin_test_command_safety import private_mux_args
    else:
        from plugin_test_command_safety import private_mux_args
    from repository_policy import AgentHostingDenied, require_agent_hosting

    candidate = tmp_path / "candidate"
    (candidate / ".git").mkdir(parents=True)
    configuration = candidate / ".agent-worktrees" / "config.yaml"
    configuration.parent.mkdir()
    configuration.write_text("expose_agent: false\n", encoding="utf-8")
    with pytest.raises(AgentHostingDenied, match="expose_agent: false"):
        require_agent_hosting(candidate, operation="fixture launch")
    monkeypatch.setitem(_qualify_public_application.__globals__, "ROOT", candidate)
    observed: list[tuple[Path, str]] = []

    def qualify(root: Path, _patch: pytest.MonkeyPatch, *, broker: str) -> None:
        caller = root / "caller"
        caller.mkdir()
        assert require_agent_hosting(caller, operation="fixture launch").expose_agent is None
        assert private_mux_args(root)
        observed.append((root, broker))

    monkeypatch.setitem(_qualify_public_application.__globals__, "_qualify_native_application", qualify)
    for caller in ("embody", "copilot"):
        _qualify_public_application(monkeypatch, caller)
    assert [caller for _, caller in observed] == ["embody", "copilot"]
    assert all(not root.exists() for root, _ in observed)


@pytest.mark.parametrize("packet", [
    "native-worktrees-copilot", "native-worktrees-copilot-caller-jobs",
])
def test_public_caller_packets_collect_exactly_the_declared_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packet: str,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    NATIVE_PACKETS = cast(
        dict[str, tuple[str, str, str, str]],
        import_module("windows_qualification_contract").NATIVE_PACKETS,
    )
    native_command = cast(
        Callable[[Path, Path, str], list[str]],
        import_module("windows_qualification_worker").native_command,
    )

    command = native_command(ROOT, tmp_path, packet)
    selected = command[command.index("-k") + 1]
    reply = run_caller_owned(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", str(Path(__file__)),
         "-k", selected, "--basetemp", str(tmp_path / "collection"),
         "-o", f"cache_dir={tmp_path / 'cache'}"],
        cwd=SOURCE, env=dict(os.environ), timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert reply.returncode == 0, reply.stdout + reply.stderr
    nodes = [line for line in reply.stdout.splitlines() if "::test_" in line]
    assert len(nodes) == 1 and nodes[0].endswith("::" + NATIVE_PACKETS[packet][2]), nodes


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("caller", ["embody", "copilot"])
def test_native_fixture_directory_remains_owned_by_outer_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool, caller: str,
) -> None:
    sandbox = tmp_path / "outer-sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    observed: list[Path] = []

    def qualify(
        directory: Path, _patch: pytest.MonkeyPatch, *, broker: str, caller_jobs: bool,
    ) -> None:
        assert broker == caller and caller_jobs is True
        assert directory.is_relative_to(sandbox)
        observed.append(directory)
        (directory / "resident-owned.log").write_text("held until outer settlement", encoding="utf-8")
        if failed:
            raise RuntimeError("native failed")

    monkeypatch.setitem(_qualify_public_application.__globals__, "_qualify_native_application", qualify)
    if failed:
        with pytest.raises(RuntimeError, match="native failed"):
            _qualify_public_application(monkeypatch, caller, caller_jobs=True)
    else:
        _qualify_public_application(monkeypatch, caller, caller_jobs=True)
    assert len(observed) == 1
    assert (observed[0] / "resident-owned.log").read_text() == "held until outer settlement"


def test_public_caller_leaf_preserves_deadline_and_settles_inherited_output(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "caller"
    directory.mkdir()
    installer = """
import subprocess,sys
subprocess.Popen(
    [sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
print("installer-root-complete", flush=True)
"""
    (directory / "request.json").write_text(json.dumps({
        "command": [sys.executable, "-I", "-B", "-c", installer],
        "cwd": str(tmp_path),
    }), encoding="utf-8")
    helper = SOURCE / "tests" / "native" / "public_caller_jobs_fixture.py"
    program = """
import json,pathlib,runpy,subprocess,sys,time
fixture = runpy.run_path(sys.argv[1])
directory = pathlib.Path(sys.argv[2])
request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
request["deadline"] = time.monotonic() + 2
(directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
try:
    fixture["_leaf"](directory)
except subprocess.TimeoutExpired as error:
    if sys.platform == "win32":
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8")
        assert "installer-root-complete" in output
    print("bounded-installer-timeout")
else:
    raise AssertionError("The caller deadline was not enforced")
"""
    reply = run_caller_owned(
        [sys.executable, "-I", "-B", "-c", program, str(helper), str(directory)],
        cwd=tmp_path, env=dict(os.environ), timeout=10,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert reply.returncode == 0, reply.stdout + reply.stderr
    assert reply.stdout.strip() == "bounded-installer-timeout"
    assert not (directory / "reply.json").exists()


def test_public_caller_leaf_refuses_an_expired_deadline_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import public_caller_jobs_fixture as fixture

    (tmp_path / "request.json").write_text(json.dumps({
        "command": [sys.executable, "-c", "raise AssertionError('must not launch')"],
        "cwd": str(tmp_path),
        "deadline": 0,
    }), encoding="utf-8")

    def refuse_creation(*_args: object, **_kwargs: object) -> None:
        pytest.fail("An expired caller deadline must refuse process creation")

    monkeypatch.setattr(subprocess, "Popen", refuse_creation)
    with pytest.raises(TimeoutError, match="caller fixture deadline expired"):
        fixture._leaf(tmp_path)
    assert not (tmp_path / "control.json").exists()
    assert not (tmp_path / "reply.json").exists()


def test_withdrawn_fixture_application_is_restored_after_broker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from public_caller_jobs_fixture import application_unavailable

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(tmp_path))
    application = tmp_path / "application"
    python = application / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (application / "application.json").write_text("ordinary fixture", encoding="utf-8")
    moved = application.with_name("application-temporarily-unavailable")
    with pytest.raises(RuntimeError, match="broker failed"):
        with application_unavailable(python):
            assert not application.exists() and moved.is_dir()
            raise RuntimeError("broker failed")
    assert python.is_file() and not moved.exists()
    assert (application / "application.json").read_text() == "ordinary fixture"
    moved.mkdir()
    with pytest.raises(AssertionError):
        with application_unavailable(python):
            pytest.fail("A retained alias must not be overwritten")
    assert python.is_file() and moved.is_dir()


def test_application_withdrawal_refuses_an_unowned_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from public_caller_jobs_fixture import application_unavailable

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    application = tmp_path / "foreign"
    python = application / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (application / "application.json").write_text("foreign", encoding="utf-8")
    with pytest.raises(AssertionError):
        with application_unavailable(python):
            pytest.fail("An unrelated application must not move")
    assert python.is_file()


@pytest.mark.parametrize("failure,generation", [
    (None, 2), (None, 1), ("install", 2), ("selection", 2),
    ("generation", 2), ("prefix", 2), ("module", 2), ("retained", 2),
])
def test_generation_replacement_keeps_installer_and_cleanup_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None, generation: int,
) -> None:
    import public_caller_jobs_fixture
    from test_headed_worktrees_copilot import _PublicCallerJobs

    fixture = cast(_PublicCallerJobs, public_caller_jobs_fixture)

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(tmp_path))
    root = tmp_path / "runtime"
    outgoing, incoming = (root / "versions" / name for name in ("1.0", "2.0"))
    python = outgoing / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    incoming.mkdir()
    (incoming / "application.json").write_text("fixture", encoding="utf-8")
    (root / "current-version").write_text(outgoing.name, encoding="utf-8")
    (root / "projects.yaml").write_text("projects: {fixture: retained}\n", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["env"] == {"FIXTURE": "selected"}
        if len(calls) == 1:
            assert command == [
                "selected-pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                str(incoming / "install.ps1"),
            ]
            assert not (incoming / ".venv").exists()
            if failure == "install":
                return subprocess.CompletedProcess(command, 1, "", "installation failed")
            for selector in ("current-version", "last-known-good"):
                (root / selector).write_text(
                    outgoing.name if failure == "selection" else incoming.name, encoding="utf-8",
                )
            (incoming / "install-success.json").write_text(json.dumps({
                "state": "installed", "version": incoming.name,
            }), encoding="utf-8")
            installed_python = incoming / ".venv" / "Scripts" / "python.exe"
            installed_python.parent.mkdir(parents=True)
            installed_python.touch()
            return subprocess.CompletedProcess(command, 0, 'setup callback\n{"state":"installed"}', "")
        if len(calls) == 2:
            assert command[:4] == [
                str(incoming / ".venv" / "Scripts" / "python.exe"), "-I", "-B", "-c",
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "generation": 3 if failure == "generation" else generation,
                "prefix": str((outgoing if failure == "prefix" else incoming) / ".venv"),
                "module": str(
                    (outgoing if failure == "module" else incoming)
                    / ".venv" / "Lib" / "site-packages" / "agent_worktrees" / "__init__.py"
                ),
            }), "")
        assert command == [
            str(incoming / ".venv" / "Scripts" / "python.exe"), "-I", "-B",
            str(incoming / "runtime" / "versioned_runtime.py"),
            "--root", str(root), "--json", "gc", "--protect-pids", "--min-age-days", "0",
        ]
        if failure == "retained":
            return subprocess.CompletedProcess(command, 0, '{"removed":[]}', "")
        shutil.rmtree(outgoing)
        return subprocess.CompletedProcess(command, 0, json.dumps({"removed": [outgoing.name]}), "")

    monkeypatch.setattr(subprocess, "run", run)
    arguments = _ReplacementOptions(
        shell="selected-pwsh", cwd=tmp_path, environment={"FIXTURE": "selected"},
        expected_generation=generation,
    )
    if failure:
        with pytest.raises(AssertionError):
            fixture.replace_application(python, incoming, **arguments)
        assert outgoing.is_dir()
        assert len(calls) == (
            3 if failure == "retained" else 2 if failure in {"generation", "prefix", "module"} else 1
        )
        if failure == "retained":
            assert (root / "current-version").read_text() == incoming.name
            assert (incoming / "install-success.json").is_file()
    else:
        selected = fixture.replace_application(python, incoming, **arguments)
        assert selected == incoming / ".venv" / "Scripts" / "python.exe"
        assert not outgoing.exists() and len(calls) == 3


def test_generation_replacement_refuses_unowned_or_prebuilt_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import public_caller_jobs_fixture
    from test_headed_worktrees_copilot import _PublicCallerJobs

    replace_application = cast(_PublicCallerJobs, public_caller_jobs_fixture).replace_application

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    outgoing = sandbox / "runtime" / "versions" / "1.0"
    python = outgoing / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    incoming = outgoing.with_name("2.0")
    incoming.mkdir()
    (incoming / "application.json").write_text("fixture", encoding="utf-8")
    (incoming / ".venv").mkdir()
    for selected in (incoming, tmp_path / "foreign"):
        with pytest.raises(AssertionError):
            replace_application(
                python, selected, shell="must-not-run", cwd=sandbox, environment={}, expected_generation=2,
            )


@pytest.mark.parametrize("damage", [None, "foreign", "prebuilt", "occupied"])
def test_application_restoration_uses_only_owned_unbuilt_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str | None,
) -> None:
    import public_caller_jobs_fixture
    from test_headed_worktrees_copilot import _PublicCallerJobs

    fixture = cast(_PublicCallerJobs, public_caller_jobs_fixture)

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    outgoing = sandbox / "runtime" / "versions" / "2.0"
    python = outgoing / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    artifact = (tmp_path if damage == "foreign" else sandbox) / "prepared-application"
    artifact.mkdir()
    (artifact / "application.json").write_text(json.dumps({
        "construction": {"runtime_version": "1.0"},
    }), encoding="utf-8")
    (artifact / "wheel.whl").write_bytes(b"fixture wheel")
    incoming = outgoing.with_name("1.0")
    if damage == "prebuilt":
        (artifact / ".venv").mkdir()
    elif damage == "occupied":
        incoming.mkdir()
    calls: list[Path] = []

    def replace(selected_python: Path, selected_slot: Path, **options: object) -> str:
        calls.append(selected_slot)
        assert selected_python == python and selected_slot == incoming
        assert options == {
            "shell": "selected-pwsh", "cwd": sandbox,
            "environment": {"FIXTURE": "selected"}, "expected_generation": 1,
        }
        assert not (selected_slot / ".venv").exists()
        assert (selected_slot / "wheel.whl").read_bytes() == b"fixture wheel"
        assert (selected_slot / "application.json").read_bytes() == (artifact / "application.json").read_bytes()
        return "selected original interpreter"

    monkeypatch.setattr(fixture, "replace_application", replace)
    options = _RestoreOptions(shell="selected-pwsh", cwd=sandbox, environment={"FIXTURE": "selected"})
    if damage:
        with pytest.raises(AssertionError):
            fixture.restore_application(python, artifact, **options)
        assert not calls
        assert incoming.exists() is (damage == "occupied")
    else:
        assert cast(object, fixture.restore_application(
            python, artifact, **options,
        )) == "selected original interpreter"
        assert calls == [incoming]
    assert python.is_file() and (artifact / "wheel.whl").is_file()


@pytest.mark.parametrize("caller", ["embody", "copilot"])
def test_lifetime_selection_cannot_disable_an_existing_contained_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    with pytest.raises(AssertionError):
        _qualify_native_application(tmp_path, monkeypatch, broker=caller, caller_jobs=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("early_exit,exit_code", [(False, 0), (False, -1), (True, 0)])
def test_attached_caller_jobs_close_before_claiming_caller_and_control_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, early_exit: bool, exit_code: int,
) -> None:
    import public_caller_jobs_fixture
    from test_headed_worktrees_copilot import _PublicCallerJobs

    fixture = cast(_PublicCallerJobs, public_caller_jobs_fixture)

    monkeypatch.setattr(fixture, "_require_native_tty_owner", lambda *args: tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    (tmp_path / "request.json").write_text(json.dumps({
        "command": ["installed-public-caller"], "cwd": str(tmp_path),
    }), encoding="utf-8")
    if not early_exit:
        (tmp_path / "close.json").write_text('{"close":true}', encoding="utf-8")
    events: list[str] = []
    closed: set[str] = set()

    def wait_caller(timeout: float) -> int:
        assert timeout == 5 and "inner" in closed
        events.append("caller-exit")
        return exit_code

    caller = SimpleNamespace(pid=101, poll=lambda: 0 if early_exit else None, wait=wait_caller)
    control = SimpleNamespace(pid=102)

    def bounded(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert kwargs["stdin"] is kwargs["stdout"] is kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs == {
            "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL, **pu.no_window_kwargs(),
        }
        return control

    def close(name: str) -> None:
        events.append(name + "-close")
        closed.add(name)

    def spawn(command: list[str], **kwargs: object) -> tuple[SimpleNamespace, SimpleNamespace]:
        assert kwargs["require_job"] is kwargs["allow_breakaway"] is True
        assert not any(key in kwargs for key in ("stdin", "stdout", "stderr", "creationflags"))
        if command[0] == "installed-public-caller":
            return caller, SimpleNamespace(close=lambda: close("inner"))
        assert command[-2:] == ["tty-leaf", str(tmp_path)]
        fixture._tty_leaf(tmp_path)
        return SimpleNamespace(wait=lambda timeout: 0), SimpleNamespace(close=lambda: close("outer"))

    monkeypatch.setattr(subprocess, "Popen", bounded)
    monkeypatch.setattr(pu, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(fixture, "process_start_time", lambda pid: f"birth-{pid}")
    monkeypatch.setattr(
        fixture, "pid_alive",
        lambda pid: "inner" not in closed if pid == 101 else "outer" not in closed,
    )
    if early_exit:
        with pytest.raises(AssertionError, match="returned before Job closure"):
            fixture._tty_leaf(tmp_path)
        assert events == ["inner-close", "caller-exit"]
        assert not (tmp_path / "inner-closed.json").exists()
    else:
        fixture._tty_middle(tmp_path)
        proof = fixture.close_public_tty_jobs(tmp_path)
        assert events == ["inner-close", "caller-exit", "outer-close"]
        assert proof["callerExitCode"] == exit_code
        assert proof["callerAliveBeforeClose"] is True
        assert proof["caller"] == {"pid": 101, "birth": "birth-101"}
        assert proof["control"] == {"pid": 102, "birth": "birth-102"}


def test_attached_caller_fixture_refuses_contained_owner_before_creating_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import public_caller_jobs_fixture
    from test_headed_worktrees_copilot import _PublicCallerJobs

    fixture = cast(_PublicCallerJobs, public_caller_jobs_fixture)

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    ticket = tmp_path / "not-created"
    with pytest.raises(AssertionError):
        fixture.prepare_public_tty_jobs(
            ["must-not-run"], python=sys.executable, cwd=tmp_path,
            environment=dict(os.environ), directory=ticket,
        )
    assert not ticket.exists()


def test_manager_install_retains_only_the_selected_runner_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV
    from test_manager_application_launch import _manager_install_environment as environment_fixture

    _manager_install_environment = cast(_ManagerEnvironment, environment_fixture)

    monkeypatch.setenv(TEST_CONTEXT_ENV, "unselected ambient context")
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "unselected-copilot-home"))
    arguments = tmp_path / "home", tmp_path / "manager", tmp_path, sys.executable
    selected = {TEST_CONTEXT_ENV: "selected runner context"}
    environment = _manager_install_environment(
        *arguments, caller_jobs=True, worktrees_environment=selected,
    )
    assert environment[TEST_CONTEXT_ENV] == selected[TEST_CONTEXT_ENV]
    assert environment["COPILOT_HOME"] == str(arguments[0] / ".copilot")
    assert environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
    assert TEST_CONTEXT_ENV not in _manager_install_environment(
        *arguments, caller_jobs=False, worktrees_environment=selected,
    )
    with pytest.raises(AssertionError):
        _manager_install_environment(*arguments, caller_jobs=True, worktrees_environment={})


@pytest.mark.parametrize("outcome", ["response", "refused", "exit", "timeout"])
def test_native_prompt_uses_selected_bridge_consumer_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    import test_headed_worktrees_copilot as fixture

    native, nonce = "original-native", "private-fixture-key"
    state = {"session_id": native, "child_pid": 12345, "nonce": nonce}
    environment = {"COPILOT_DRIVER_BRIDGE_PYTHON": sys.executable}
    calls: list[list[str]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert command[0] == sys.executable
        assert "test_copied_core_pickup" in command[command.index("-c") + 1]
        assert not any(nonce in argument for argument in command)
        assert json.loads(options["input_text"]) == {
            "state": state, "native": state["session_id"], "prompt": "one input",
            "refused": outcome == "refused",
        }
        assert options["timeout"] == 25 and options["env"] == environment
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, 1 if outcome == "exit" else 0, json.dumps({
            "session_id": state["session_id"], "child_pid": state["child_pid"],
            "result": (
                {"refused": True, "code": -32001} if outcome == "refused"
                else {"stop_reason": "end_turn"}
            ),
            "events": [{"type": "agent_message", "data": {"text": "native response"}}],
        }), "Bridge consumer failed" if outcome == "exit" else "")

    monkeypatch.setattr(fixture, "run_caller_owned", run)
    if outcome in {"exit", "timeout"}:
        with pytest.raises(subprocess.TimeoutExpired if outcome == "timeout" else AssertionError):
            fixture._bridge_prompt(
                state, native=native, prompt="one input", refused=False,
                environment=environment, cwd=tmp_path,
            )
    else:
        fixture._bridge_prompt(
            state, native=native, prompt="one input", refused=outcome == "refused",
            environment=environment, cwd=tmp_path,
        )
    assert len(calls) == 1


def test_native_control_cannot_prompt_the_core_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import test_headed_worktrees_copilot as fixture
    from agent_remote_driver.session_host.client import SessionHostClient

    calls: list[dict[str, Any]] = []
    native = "original-native"
    state = {"session_id": native, "child_pid": 12345, "port": 54321}
    environment = {"selected": "Bridge consumer"}

    async def direct_core(**options: object) -> None:
        pytest.fail("Native prompt bypassed Bridge and connected to Core directly")

    def bridge(original: dict[str, Any], **options: Any) -> None:
        assert original is state
        calls.append(options)

    monkeypatch.setattr(SessionHostClient, "connect", direct_core)
    monkeypatch.setattr(fixture, "_bridge_prompt", bridge)
    asyncio.run(fixture._control_native(
        state, native=native, environment=environment, cwd=tmp_path, prompt="one input",
    ))
    assert calls == [{
        "native": state["session_id"], "environment": environment, "cwd": tmp_path,
        "prompt": "one input", "refused": False,
    }]


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_manager_native_phase_propagates_owned_failures_without_inline_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    import test_manager_application_launch as fixture
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

    calls: list[list[str]] = []
    environment = {TEST_CONTEXT_ENV: "selected fixture lifecycle"}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["env"] == environment and kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 180
        assert kwargs["max_stdout_bytes"] == kwargs["max_stderr_bytes"] == 65536
        assert command[command.index("-c") + 2:][-5:] == [
            "manager-no-mux", str(tmp_path / "launch-session.ps1"),
            str(tmp_path / "python.exe"), str(tmp_path / "manager"), "1",
        ]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, cast(float, kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 3, "native failed", "owned cleanup retained")

    monkeypatch.setattr(fixture, "run_caller_owned", run)
    with pytest.raises(
        subprocess.TimeoutExpired if failure == "timeout" else AssertionError,
        match="timed out" if failure == "timeout" else "native failed",
    ):
        fixture._run_manager_native(
            tmp_path, "manager-no-mux", script=tmp_path / "launch-session.ps1",
            python=tmp_path / "python.exe", root=tmp_path / "manager",
            environment=environment, picker_decision=True,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("valid_observations", [False, True])
def test_manager_decision_proof_distinguishes_native_exit_from_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_observations: bool,
) -> None:
    from test_headed_worktrees_copilot import _manager_decision_source

    script, marker = tmp_path / "launch-session.ps1", tmp_path / "decision.json"
    script.touch()
    phases = ["confirmed", "resolution-requested", "resolved", "launch-requested"]
    if valid_observations:
        phases.append("launcher-returned")
    detail = {
        "events": [{"phase": phase, "details": {"exit_code": 1}} for phase in phases],
        "input": {"options": {"no_mux": True}},
    }
    manager = ModuleType("worktree_manager")
    setattr(manager, "__main__", SimpleNamespace(_run_production_picker=lambda *args, **kwargs: 1))
    setattr(manager, "engine_client", SimpleNamespace(set_engine_command=lambda command: None))
    attempts = ModuleType("worktree_manager.launch_attempts")
    setattr(attempts, "LaunchAttempts", lambda: SimpleNamespace(
        record_intent=lambda *args, **kwargs: None, detail=lambda *args, **kwargs: detail,
    ))
    relocated = ModuleType("worktree_manager.relocated_launch")
    setattr(relocated, "_relocated_launch_script", lambda: script)
    for module in (manager, attempts, relocated):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", ["fixture", "engine", "worktree", "attempt", str(script), str(marker)])
    code = compile(_manager_decision_source(), "manager-decision-fixture", "exec")
    with pytest.raises(SystemExit if valid_observations else AssertionError) as error:
        exec(code, {"__name__": "__main__"})
    # Both subprocess outcomes are 1; only completed assertions produce proof.
    assert (cast(SystemExit, error.value).code if valid_observations else 1) == 1
    assert marker.exists() is valid_observations
    if valid_observations:
        assert json.loads(marker.read_text(encoding="utf-8")) == {"attempt": "attempt", "exit_code": 1}


@pytest.mark.parametrize("damage", ["none", "early-callback", "nonce", "callback", "arguments"])
def test_manager_mux_decision_retains_proof_only_after_correlated_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    from test_headed_worktrees_copilot import _manager_mux_decision_source

    script, marker = tmp_path / "launch-session.ps1", tmp_path / "decision.json"
    script.touch()
    request = tmp_path / "request.json"
    prepared: _PreparedDecision = {
        "nonce": "offer", "script": str(script),
        "arguments": ["--project", "native-copilot", "--worktree-id", "worktree",
                      "--require-launch-attempt", "--launch-attempt-id", "attempt"],
        "completion": [sys.executable, "-I", "-B", "-X", "utf8",
                       "-m", "worktree_manager.terminal_launch", "attempt"],
    }
    phases = ["confirmed", "resolution-requested", "resolved", "launch-requested"]
    if damage == "early-callback":
        phases.append("launcher-returned")
    elif damage == "nonce":
        prepared["nonce"] = "another-offer"
    elif damage == "callback":
        prepared["completion"][-1] = "another-attempt"
    elif damage == "arguments":
        prepared["arguments"].append("--no-mux")
    (tmp_path / "result.json").write_text(json.dumps(prepared), encoding="utf-8")
    manager = ModuleType("worktree_manager")
    decisions: list[dict[str, Any]] = []

    def pick(*args: object, **kwargs: dict[str, Any]) -> int:
        decisions.append(kwargs["initial_decision"])
        return 0

    setattr(manager, "__main__", SimpleNamespace(_run_production_picker=pick))
    setattr(manager, "engine_client", SimpleNamespace(set_engine_command=lambda command: None))
    setattr(manager, "terminal_launch", SimpleNamespace(validate=lambda: (request, {"nonce": "offer"})))
    attempts = ModuleType("worktree_manager.launch_attempts")
    setattr(attempts, "LaunchAttempts", lambda: SimpleNamespace(
        record_intent=lambda *args, **kwargs: None,
        detail=lambda *args, **kwargs: {
            "events": [{"phase": phase} for phase in phases], "input": {"options": {}},
        },
    ))
    relocated = ModuleType("worktree_manager.relocated_launch")
    setattr(relocated, "_relocated_launch_script", lambda: script)
    for module in (manager, attempts, relocated):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", ["fixture", "engine", "worktree", "attempt", str(script), str(marker)])
    code = compile(_manager_mux_decision_source(), "manager-mux-decision-fixture", "exec")
    with pytest.raises(SystemExit if damage == "none" else AssertionError) as error:
        exec(code, {"__name__": "__main__"})
    assert decisions[0]["options"] == {}
    assert marker.exists() is (damage == "none")
    if damage == "none":
        assert cast(SystemExit, error.value).code == 0
        assert json.loads(marker.read_text()) == {
            "attempt": "attempt", "exit_code": 0, "request": str(request),
        }


@pytest.mark.parametrize("build_wheel", [False, True])
def test_native_application_materializes_canonical_sdk_before_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_wheel: bool,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder
    else:
        import build_python_artifacts as builder

    original_copy = _copy_application_inputs
    expected_deadline = time.monotonic() + 180
    generation = 37
    modules = {
        Path(directory) / (prefix + name): ROOT / "libs" / "payload-invocation" / name
        for directory, prefix in (("scripts", ""), ("src/agent_worktrees", "_"))
        for name in ("command_binding.py", "command_lifecycle.py")
    }

    def copy_with_stale_mirrors(source: Path, destination: Path) -> None:
        original_copy(source, destination)
        for relative in modules:
            (destination / relative).write_text("STALE_SDK = True\n", encoding="utf-8")

    class BuildBoundaryReached(Exception):
        pass

    def inspect_build(
        *, source_dir: Path, identity: dict[str, Any], dependencies: list[tuple[str, Path]],
        out_dir: Path, python: str | None, toolchain: builder.ToolchainLock | None,
        uv: str, deadline: float | None,
    ) -> None:
        assert uv == "unused" and deadline == expected_deadline
        for relative, canonical in modules.items():
            assert (source_dir / relative).read_bytes() == canonical.read_bytes(), str(relative)
        entry = source_dir / "src" / "agent_worktrees" / "__init__.py"
        assert f"_fixture_application_generation = {generation}" in entry.read_text(encoding="utf-8")
        if build_wheel:
            result = run_caller_owned(
                [sys.executable, "-I", "-B", "-c",
                 "import setuptools.build_meta as backend;"
                 "backend.build_wheel(__import__('sys').argv[1], {'--global-option': ['--quiet']})",
                 str(out_dir)],
                cwd=source_dir, env=dict(os.environ, UV_OFFLINE="1"),
                timeout=max(0.01, expected_deadline - time.monotonic()),
                max_stdout_bytes=1048576, max_stderr_bytes=65536,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            wheels = list(out_dir.glob("*.whl"))
            assert len(wheels) == 1
            wheel = wheels[0]
            with ZipFile(wheel) as built:
                for relative, canonical in modules.items():
                    packaged = (
                        Path("agent_worktrees") / "_runtime_assets" / relative
                        if relative.parts[0] == "scripts" else Path(*relative.parts[1:])
                    )
                    assert built.read(packaged.as_posix()) == canonical.read_bytes()
                assert built.read("agent_worktrees/__init__.py") == entry.read_bytes()
        raise BuildBoundaryReached

    monkeypatch.setattr(sys.modules[__name__], "_copy_application_inputs", copy_with_stale_mirrors)
    monkeypatch.setattr(builder, "build_source_artifacts", inspect_build)
    with pytest.raises(BuildBoundaryReached):
        _prepare_native_application(
            tmp_path / "inputs", tmp_path / "profile", monkeypatch=monkeypatch,
            uv="unused", interpreter=sys.executable, deadline=expected_deadline,
            generation=generation,
        )


@pytest.mark.parametrize("damage", ["manifest", "deadline"])
def test_native_application_refuses_unprepared_inputs_before_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder, prepare_runtime_source as preparation
    else:
        import build_python_artifacts as builder
        import prepare_runtime_source as preparation

    original_copy = _copy_application_inputs

    def copy_with_invalid_manifest(source: Path, destination: Path) -> None:
        original_copy(source, destination)
        if damage == "manifest":
            manifest = destination / "payload-invocation.json"
            manifest.write_bytes(manifest.read_bytes() + b"\n")

    def reject_build(**kwargs: object) -> None:
        pytest.fail("Unprepared application inputs reached the wheel builder")

    monkeypatch.setattr(sys.modules[__name__], "_copy_application_inputs", copy_with_invalid_manifest)
    monkeypatch.setattr(builder, "build_source_artifacts", reject_build)
    with pytest.raises(
        preparation.SourcePreparationError,
        match="Invocation manifest differs" if damage == "manifest" else "deadline exceeded",
    ):
        _prepare_native_application(
            tmp_path / "inputs", tmp_path / "profile", monkeypatch=monkeypatch,
            uv="unused", interpreter=sys.executable,
            deadline=time.monotonic() + (60 if damage == "manifest" else -1), generation=1,
        )


def _prepare_native_application(
    inputs: Path, profile: Path, *, monkeypatch: pytest.MonkeyPatch, uv: str,
    interpreter: str, deadline: float, generation: int,
) -> Path:
    started = time.monotonic()
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder, prepare_runtime_source as preparation
        from tools import local_install_profile as profiles
    else:
        import build_python_artifacts as builder
        import prepare_runtime_source as preparation
        import local_install_profile as profiles

    source, artifacts = inputs / "plugins" / "agent-worktrees", inputs / "wheels"
    source.mkdir(parents=True)
    artifacts.mkdir()
    _copy_application_inputs(SOURCE, source)
    entry = source / "src" / "agent_worktrees" / "__init__.py"
    entry.write_text(
        entry.read_text(encoding="utf-8") + f"\n_fixture_application_generation = {generation}\n",
        encoding="utf-8",
    )
    preparation.materialize_consumer(ROOT, SOURCE, source, deadline=deadline)
    dependencies = builder.resolve_vendored_libs(source)
    for _, directory in dependencies:
        tests = directory / "tests"
        if tests.is_dir():
            shutil.rmtree(tests)
    print(f"Application inputs materialized: {time.monotonic() - started:.2f}s", flush=True)
    with monkeypatch.context() as patch:
        patch.setattr(builder, "REPO", inputs)
        artifact = builder.build_source_artifacts(
            identity={"plugin": "agent-worktrees"}, source_dir=source,
            dependencies=dependencies,
            out_dir=artifacts, python=interpreter, toolchain=None,
            uv=uv, deadline=deadline,
        )
    print(f"Application wheels built: {time.monotonic() - started:.2f}s", flush=True)
    source_inputs = cast(_ProfileExports, profiles).source_inputs
    engine = source_inputs.engine
    project = cast(_BuilderExports, builder).tomllib.loads((source / "pyproject.toml").read_text())["project"]
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        "agent-worktrees", project["version"], "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version(project["version"], "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    (inputs / source_inputs.MANIFEST).write_text(json.dumps({
        "schema": source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"agent-worktrees": {
            "payload": "plugins/agent-worktrees", "wheels": "wheels",
            "source": identity.document(), "artifact": artifact,
        }},
    }), encoding="utf-8")
    prepared = profiles.prepare_registry_lock(
        inputs, profile, uv=uv, deadline=deadline,
        prepare_profile=True, component="agent-worktrees",
    )
    print(f"Application profile prepared: {time.monotonic() - started:.2f}s", flush=True)
    assert prepared["install_ready"] is True
    return Path(prepared["snapshot"]) / "applications" / "agent-worktrees"


def _owned_application_command(
    home: Path, caller: Path, root: Path, environment: dict[str, str],
    command: Sequence[str | Path | None], *, interpreter: str, deadline: float,
) -> list[str]:
    if TYPE_CHECKING:
        from tools import prepare_runtime_source as preparation
    else:
        import prepare_runtime_source as preparation
    isolate_account_profile = cast(
        Callable[[Path, Path], None], import_module("runtime_install_fixture").isolate_account_profile,
    )

    owner = home / ".copilot" / "installed-plugins" / "example" / "agent-worktrees"
    owner.mkdir(parents=True)
    _copy_application_inputs(SOURCE, owner)
    preparation.materialize_consumer(ROOT, SOURCE, owner, deadline=deadline)
    isolate_account_profile(owner, home)
    settings = caller / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    environment.update(
        COPILOT_PLUGIN_ROOT=str(owner), COPILOT_PLUGIN_STAGED_FROM=str(owner),
        COPILOT_HOME=str(home / ".copilot"),
        COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=str(caller),
    )
    return [
        interpreter, "-I", "-B", str(owner / "scripts" / "command_binding.py"),
        "--payload-root", str(owner), "--source-payload-root", str(owner),
        "--plugin-id", "agent-worktrees", "--legacy-root", str(root),
        "--project-root", str(caller), "--run-lifecycle", "--managed-deployment",
        "--", *map(str, command),
    ]


@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("root_alias", [False, True])
def test_application_fixture_installs_through_its_private_command_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, root_alias: bool,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = home / ".agent-worktrees"
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "unselected-copilot-home"))
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_WORKTREES_"))
        and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    selected_root = root
    if root_alias:
        if os.name == "nt":
            selected_root = Path("\\\\?\\" + str(root))
        else:
            (root / "alias-component").mkdir(parents=True)
            selected_root = root / "alias-component" / ".."
    probe = (
        "import json,os,sys;from pathlib import Path;"
        "owner=Path(os.environ['COPILOT_PLUGIN_STAGED_FROM']);"
        "project=Path(os.environ['COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT']);"
        "sys.path.insert(0,str(owner/'scripts'));import command_binding as binding;"
        "arguments=dict(payload_root=owner,plugin_id='agent-worktrees',"
        "legacy_root=Path(sys.argv[1]),project_root=project);"
        "primitive=binding._load_primitive(owner);"
        "lease=binding._load_lifecycle().borrowed_lease(binding._rules(),primitive,**arguments);"
        "assert lease is not None and lease.purpose['origin']=='management';"
        "sys.exit(7) if sys.argv[2]=='7' else None;"
        "binding.write_legacy_manifest(primitive,"
        "{'schema_version':3,'service':'agent-worktrees'},**arguments);"
        "print(json.dumps({'owner':str(owner),'project':str(project)}))"
    )
    deadline = time.monotonic() + 60
    command = _owned_application_command(
        home, caller, root, environment,
        [sys.executable, "-I", "-B", "-c", probe, selected_root, str(exit_code)],
        interpreter=sys.executable, deadline=deadline,
    )
    assert environment["COPILOT_HOME"] == str(home / ".copilot")
    result = run_caller_owned(
        command, cwd=caller, env=environment, timeout=max(0.01, deadline - time.monotonic()),
        max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    if not exit_code:
        assert json.loads(result.stdout) == {
            "owner": environment["COPILOT_PLUGIN_STAGED_FROM"], "project": str(caller),
        }
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert manifest["commandLifecycle"]["state"] == ("pending" if exit_code else "complete")
    assert manifest["commandLifecycle"]["owner"]["source"]["canonical"] == "github:example/plugins"
    if exit_code:
        assert "commandBinding" not in manifest
    else:
        assert Path(manifest["commandBinding"]["payloadRoot"]).is_relative_to(home)


def _qualify_native_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, broker: bool | str = False,
    caller_jobs: bool = False,
) -> None:
    if caller_jobs:
        from agent_procutil import contained_test_mode
        from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

        assert broker in {"embody", "copilot"} and not contained_test_mode()
        assert os.environ.get(TEST_CONTEXT_ENV), "The native runner must own the daemon lease"
    started = time.monotonic()
    construction_budget = 420 if broker in {"embody", "copilot"} else 300
    deadline = started + construction_budget
    print("Application construction starting", flush=True)
    sys.path.insert(0, str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import local_install_profile as profiles
        from tools.governed_feed_trust import _governed_feed_configured
    else:
        import local_install_profile as profiles
        from governed_feed_trust import _governed_feed_configured

    if not _governed_feed_configured():
        pytest.skip("native application construction requires the configured governed feed")
    assert os.environ.get("PYTEST_CURRENT_TEST")
    uv = shutil.which("uv")
    shell = shutil.which("pwsh") or shutil.which("powershell.exe") if os.name == "nt" else None
    assert uv and (os.name != "nt" or shell)
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    engine = cast(_ProfileExports, profiles).source_inputs.engine
    inputs = tmp_path / "source-inputs"
    application = _prepare_native_application(
        inputs, tmp_path / "profile", monkeypatch=monkeypatch, uv=uv,
        interpreter=interpreter, deadline=deadline, generation=1,
    )
    declaration = json.loads((application / "application.json").read_text())
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = home / ".agent-worktrees"
    root.mkdir()
    (root / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    slot = root / "versions" / declaration["construction"]["runtime_version"]
    shutil.copytree(application, slot)
    replacement = None
    if caller_jobs and broker == "embody":
        replacement_inputs = tmp_path / "replacement-inputs"
        incoming = _prepare_native_application(
            replacement_inputs, tmp_path / "replacement-profile", monkeypatch=monkeypatch,
            uv=uv, interpreter=interpreter, deadline=deadline, generation=2,
        )
        incoming_construction = json.loads((incoming / "application.json").read_text())["construction"]
        replacement = root / "versions" / incoming_construction["runtime_version"]
        assert replacement != slot
        assert not (incoming / ".venv").exists()
        assert not (incoming / engine.runtime.COMPLETE_MARKER).exists()
        shutil.copytree(incoming, replacement)
        shutil.rmtree(replacement_inputs)
        assert not replacement_inputs.exists()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_RT_",
                               "AGENT_WORKTREES_"))
        and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        AGENT_WORKTREES_ROOT=str(root), AGENT_WORKTREES_STATUS_MONITOR="0",
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1",
    )
    if caller_jobs:
        environment.pop("COPILOT_EXTENSIONS_TEST_CONTAINED")
        environment[TEST_CONTEXT_ENV] = os.environ[TEST_CONTEXT_ENV]
    from application_python_fixture import managed_python_environment

    environment = managed_python_environment(
        environment, directory=tmp_path / "managed-python", uv=uv,
        deadline=deadline, run=_run,
    )
    assert environment.get("PYTEST_CURRENT_TEST")
    install = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", slot / "install.ps1"] \
        if os.name == "nt" else [slot / "install"]
    install = _owned_application_command(
        home, caller, root, environment, install, interpreter=interpreter, deadline=deadline,
    )
    installed = _run(install, cwd=caller, env=environment, deadline=deadline)
    print(f"Application installed: {time.monotonic() - started:.2f}s", flush=True)
    assert json.loads(installed.stdout)["state"] == "installed"
    assert (root / "current-version").read_text().strip() == slot.name
    assert (root / "projects.yaml").read_text() == "projects: {}\n"
    completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
    shutil.rmtree(inputs)
    assert not inputs.exists()
    print(f"Application source inputs removed: {time.monotonic() - started:.2f}s", flush=True)
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checked = _run([
        python, "-I", "-B", "-c",
        "import argparse,json,os,sys;from pathlib import Path;"
        "import agent_worktrees;assert agent_worktrees._fixture_application_generation == 1;"
        "from agent_worktrees import config,launch_seed_native,status_monitor_runtime;"
        "assert not status_monitor_runtime._status_monitor_enabled();"
        "launch_seed_native.require_current_runtime(Path(sys.argv[1]));"
        "from agent_worktrees import __main__ as cli;"
        "cwd=str(Path.cwd());platform='windows' if os.name=='nt' else 'linux';"
        "configuration=config.Config(srcroot=cwd,machine='fixture',platform=platform,"
        "repo_name='fixture',repos={'fixture':config.RepoConfig(anchor=cwd,worktree_root=cwd)});"
        "command=cli._build_launch_cmd(configuration,"
        "argparse.Namespace(recovery=True,copilot_args=[]),cwd);"
        "print(json.dumps({'root':str(config.runtime_install_dir()),'prefix':sys.prefix,"
        "'command':command}))",
        root,
    ], cwd=caller, env=environment, deadline=deadline)
    facts = json.loads(checked.stdout)
    assert Path(facts["prefix"]) == slot / ".venv"
    assert Path(facts["root"]) == root
    command = facts["command"]
    scripts = [Path(argument) for argument in command if argument.endswith((".ps1", ".sh"))]
    assert len(scripts) == 2, command
    assert all(script.is_file() for script in scripts), command
    assert all(script.is_relative_to(slot / ".venv") for script in scripts), command
    boundary = scripts[0].with_name("native-seed-boundary.ps1" if os.name == "nt" else "native-seed-boundary.sh")
    if os.name == "nt":
        described = _run([
            shell, "-NoProfile", "-NoLogo", "-Command",
            f". {_literal(boundary)}; $reply=Invoke-AwNativeSeedPhase {_literal(root)} "
            "@('--describe-contract'); if (-not $reply) { throw 'Packaged native provider unavailable' }; "
            "ConvertTo-Json -InputObject $reply -Depth 10 -Compress",
        ], cwd=caller, env=environment, deadline=deadline)
    else:
        described = _run([
            "bash", "-c", '. "$1"; aw_native_seed_phase "$2" --describe-contract',
            "application", boundary, root,
        ], cwd=caller, env=environment, deadline=deadline)
    assert json.loads(described.stdout)["contract"] == "agent-worktrees.native-seed"
    print(f"Application packaged admission checked: {time.monotonic() - started:.2f}s", flush=True)
    if broker in {"embody", "copilot"}:
        assert time.monotonic() < deadline, "Public caller preparation exceeded 420 seconds"
        from test_manager_application_launch import _qualify_manager_application as qualify_manager

        manager = tmp_path / "manager"
        manager.mkdir()
        cast(_ManagerApplication, qualify_manager)(
            manager, monkeypatch, cast(str, broker), worktrees_python=python,
            worktrees_root=root, worktrees_environment=environment,
            caller_jobs=caller_jobs, replacement_slot=replacement,
            rollback_application=application if replacement is not None else None,
        )
    elif broker:
        assert time.monotonic() < deadline, "Application construction exceeded 300 seconds"
        native_root = tmp_path / "native-flow"
        native_root.mkdir()
        cleanup: list[InstanceCleanupResult] = []
        try:
            with daemon_test_lifecycle(
                owner="worktrees-application-broker-fixture",
                registry_root=tmp_path / "daemon-instances", deadline=time.monotonic() + 180,
                cleanup_results=cleanup,
            ) as lifecycle:
                qualified = run_caller_owned(
                    [sys.executable, "-I", "-B", "-X", "utf8", "-c",
                     "import os,sys;from pathlib import Path;"
                     "sys.path[:0]=sys.argv[1:3];"
                     "from test_headed_worktrees_copilot import _qualify_native_worktrees;"
                     "_qualify_native_worktrees(Path(sys.argv[3]),'broker',"
                     "worktrees_python=Path(sys.argv[4]),worktrees_root=Path(sys.argv[5]),"
                     "worktrees_environment=dict(os.environ))",
                     str(Path(__file__).parent), str(ROOT / "tools"),
                     str(native_root), str(python), str(root)],
                    env={
                        **environment, **lifecycle,
                        "COPILOT_EXTENSIONS_TEST_SANDBOX": os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"],
                    },
                    cwd=caller, timeout=180,
                    max_stdout_bytes=65536, max_stderr_bytes=65536,
                )
                print(qualified.stdout, flush=True)
                assert qualified.returncode == 0, qualified.stdout + qualified.stderr
        finally:
            assert all(result.status == "settled" for result in cleanup), cleanup
        assert cleanup, "The installed broker did not admit its owned native processes"
    elif os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE"):
        from test_native_startup_prompt import _qualify_native_startup as qualify_startup

        def packaged_command(
            native: list[str], native_environment: dict[str, str], directory: Path,
        ) -> list[str]:
            selected = _run([
                python, "-I", "-B", "-c",
                "import argparse,json,os,sys;from pathlib import Path;"
                "from agent_worktrees import __main__ as cli,config;"
                "native=json.loads(sys.argv[1]);cwd=str(Path.cwd());"
                "platform='windows' if os.name=='nt' else 'linux';"
                "configuration=config.Config(srcroot=cwd,machine='fixture',platform=platform,"
                "repo_name='fixture',repos={'fixture':config.RepoConfig(anchor=cwd,worktree_root=cwd,"
                "copilot_path={platform:native[0]})});"
                "print(json.dumps(cli._build_launch_cmd(configuration,"
                "argparse.Namespace(recovery=True,copilot_args=native[1:]),cwd)))",
                json.dumps(native),
            ], cwd=directory, env=native_environment, deadline=deadline)
            return cast(list[str], json.loads(selected.stdout))

        native_root = tmp_path / "native-flow"
        native_root.mkdir()
        cast(_NativeStartup, qualify_startup)(
            native_root, False, command_wrapper=packaged_command, environment=environment,
        )
    if replacement is None:
        assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion
    else:
        assert not replacement.exists()
        assert (root / "current-version").read_text().strip() == slot.name
        assert (root / "last-known-good").read_text().strip() == slot.name
        assert (slot / engine.runtime.COMPLETE_MARKER).is_file()
        assert (root / "projects.yaml").is_file()
