"""Discover Manager launch assets from its real first-touch wheel application."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from importlib import import_module
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any, cast
from zipfile import ZipFile

from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
from agent_procutil.daemon_instances import (
    TEST_CONTEXT_ENV, InstanceCleanupResult, daemon_test_lifecycle,
)
from agent_procutil.owned_process import run_caller_owned
import pytest

from test_native_seed_application import ROOT, _BuilderExports, _run, _SandboxFactory

_NATIVE_COPILOT = (
    os.name == "nt"
    and os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    and os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") == "1"
)


def _run_lifetime_native(
    command: list[str], *, directory: Path, cwd: Path, environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

    stdout, stderr = directory / "native.stdout.log", directory / "native.stderr.log"
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process, job = spawn_sync_in_kill_on_close_job(
            command, cwd=cwd, env=environment, require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, **no_window_kwargs(),
        )
    try:
        assert job is not None
        code = process.wait(180)
    finally:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(5)
        finally:
            if job is not None:
                job.close()
    return subprocess.CompletedProcess(
        command, code, stdout.read_text(encoding="utf-8"), stderr.read_text(encoding="utf-8"),
    )


def _manager_install_environment(
    home: Path, root: Path, tmp_path: Path, interpreter: str, *, caller_jobs: bool,
    worktrees_environment: dict[str, str] | None,
) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_RT_",
                               "AGENT_WORKTREES_", "WORKTREE_"))
        and key not in {
            "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "COPILOT_AGENT_SESSION_ID",
        }
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_HOME=str(home / ".copilot"),
        WORKTREE_MANAGER_ROOT=str(root), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        AGENT_WORKTREES_STATUS_MONITOR="0",
        UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1",
        PSMUX_DATA_DIR=str(tmp_path / "mux-data"), TMUX_TMPDIR=str(tmp_path / "mux-data"),
    )
    if caller_jobs:
        assert worktrees_environment and worktrees_environment.get(TEST_CONTEXT_ENV)
        environment[TEST_CONTEXT_ENV] = worktrees_environment[TEST_CONTEXT_ENV]
    return environment


def _run_manager_native(
    directory: Path, control: str, *, script: Path, python: Path, root: Path,
    environment: dict[str, str], picker_decision: bool,
) -> None:
    command = [
        sys.executable, "-I", "-B", "-X", "utf8", "-c",
        "import sys;from pathlib import Path;"
        "sys.path[:0]=sys.argv[1:3];"
        "from test_headed_worktrees_copilot import _qualify_native_worktrees;"
        "_qualify_native_worktrees(Path(sys.argv[3]),sys.argv[4],"
        "manager_script=Path(sys.argv[5]),manager_python=Path(sys.argv[6]),"
        "manager_environment={'WORKTREE_MANAGER_ROOT':sys.argv[7],'WORKTREE_PANE_WAIT_TIMEOUT':'3'},"
        "verify_reuse=False,manager_picker=sys.argv[8]=='1')",
        str(Path(__file__).parent), str(ROOT / "tools"), str(directory),
        control, str(script), str(python), str(root), "1" if picker_decision else "0",
    ]
    qualified = run_caller_owned(
        command, cwd=directory, env=environment, timeout=180,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    print(qualified.stdout, flush=True)
    assert qualified.returncode == 0, qualified.stdout + qualified.stderr


@pytest.mark.parametrize(
    "control", ["manager-no-mux", "manager-mux"] if _NATIVE_COPILOT else ["manager-no-mux"],
)
@pytest.mark.timeout(300)
def test_fresh_manager_application_exposes_its_real_launcher_to_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control: str,
) -> None:
    _qualify_manager_application(tmp_path, monkeypatch, control)


@pytest.mark.timeout(300)
def test_fresh_manager_download_exposes_its_real_launcher_to_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _qualify_manager_application(
        tmp_path, monkeypatch, "manager-no-mux", download_manager=True,
    )


@pytest.mark.skipif(not _NATIVE_COPILOT, reason="Select the isolated native Windows Copilot lane")
@pytest.mark.timeout(300)
def test_manager_no_mux_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    TestSandbox = cast(_SandboxFactory, import_module("plugin_test_containment").TestSandbox)

    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"])
    with TestSandbox(prefix="m-", dir=sandbox) as directory:
        _qualify_manager_application(
            Path(directory), monkeypatch, "manager-no-mux", picker_decision=True,
        )


@pytest.mark.skipif(not _NATIVE_COPILOT, reason="Select the isolated native Windows Copilot lane")
@pytest.mark.timeout(300)
def test_manager_mux_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    TestSandbox = cast(_SandboxFactory, import_module("plugin_test_containment").TestSandbox)

    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"])
    with TestSandbox(prefix="m-", dir=sandbox) as directory:
        _qualify_manager_application(
            Path(directory), monkeypatch, "manager-mux", picker_decision=True,
        )


@pytest.mark.parametrize("build_wheel", [False, True])
def test_manager_application_inputs_close_declared_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_wheel: bool,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    candidate = tmp_path / "source"
    candidate.mkdir()
    deadline = time.monotonic() + 240
    source, _dependencies, _resources = _prepare_manager_application_inputs(candidate, deadline=deadline)
    assert source == candidate / "worktree-manager"
    policy = ("__init__.py", "planning.py", "role_bindings.py", "target_policy.py")
    for name in policy:
        relative = Path("fleet-manager") / "src" / "fleet_manager" / name
        assert (candidate / relative).read_bytes() == (ROOT / relative).read_bytes()
    if build_wheel:
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        result = run_caller_owned(
            [sys.executable, "-I", "-B", "-c",
             "import setuptools.build_meta as backend;"
             "backend.build_wheel(__import__('sys').argv[1], {'--global-option': ['--quiet']})",
             str(wheels)],
            cwd=source, env=dict(os.environ, UV_OFFLINE="1"),
            timeout=max(0.01, deadline - time.monotonic()),
            max_stdout_bytes=1048576, max_stderr_bytes=65536,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        archives = list(wheels.glob("*.whl"))
        assert len(archives) == 1
        with ZipFile(archives[0]) as built:
            prefix = "worktree_manager/_runtime_assets/"
            for name in policy:
                relative = Path("fleet-manager") / "src" / "fleet_manager" / name
                assert built.read(prefix + "scripts/runtime-source-tools/fleet_role_policy/" + name) == (
                    ROOT / relative
                ).read_bytes()
            for name in ("launch-session.ps1", "launch-session.sh"):
                assert built.read(prefix + "bin/" + name) == (source / "bin" / name).read_bytes()


def test_manager_application_forwards_its_build_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder, governed_feed_trust as feed
        from tools import standalone_python_artifacts as standalone
    else:
        import build_python_artifacts as builder
        import governed_feed_trust as feed
        import standalone_python_artifacts as standalone

    started = time.monotonic()
    preparation_deadlines: list[float] = []

    def prepare(candidate: Path, *, deadline: float) -> tuple[Path, list[tuple[str, Path]], tuple[Path, ...]]:
        assert started < deadline <= time.monotonic() + 300
        preparation_deadlines.append(deadline)
        source = candidate / "worktree-manager"
        source.mkdir()
        return source, [], ()

    class BuildBoundaryReached(Exception):
        pass

    def build(**kwargs: object) -> None:
        deadline = kwargs["deadline"]
        assert isinstance(deadline, float) and started < deadline <= time.monotonic() + 300
        assert preparation_deadlines == [deadline]
        assert kwargs["uv"] == shutil.which("uv")
        raise BuildBoundaryReached

    monkeypatch.setattr(feed, "_governed_feed_configured", lambda: True)
    monkeypatch.setattr(sys.modules[__name__], "_prepare_manager_application_inputs", prepare)
    monkeypatch.setattr(standalone, "source_closure", lambda *args: (
        tmp_path / "source-inputs" / "source" / "worktree-manager", [], (),
    ))
    monkeypatch.setattr(builder, "build_source_artifacts", build)
    with pytest.raises(BuildBoundaryReached):
        _qualify_manager_application(tmp_path, monkeypatch, "manager-no-mux")


def test_manager_download_precedes_application_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import governed_feed_trust as feed
    else:
        import governed_feed_trust as feed

    class DownloadBoundaryReached(Exception):
        pass

    deadlines: list[float] = []

    def prepare(candidate: Path, *, deadline: float) -> tuple[Path, list[tuple[str, Path]], tuple[Path, ...]]:
        deadlines.append(deadline)
        source = candidate / "worktree-manager"
        source.mkdir()
        return source, [], ()

    def download(candidate: Path, **options: object) -> tuple[list[str | Path], str]:
        assert candidate == tmp_path / "source-remote"
        assert options["deadline"] == deadlines[0]
        assert options["root"] == tmp_path / "home" / ".worktree-manager"
        assert not cast(Path, options["root"]).exists()
        raise DownloadBoundaryReached

    monkeypatch.setattr(feed, "_governed_feed_configured", lambda: True)
    monkeypatch.setattr(sys.modules[__name__], "_prepare_manager_application_inputs", prepare)
    monkeypatch.setattr(sys.modules[__name__], "_manager_download_install_command", download)
    with pytest.raises(DownloadBoundaryReached):
        _qualify_manager_application(
            tmp_path, monkeypatch, "manager-no-mux", download_manager=True,
        )


@pytest.mark.parametrize("index_name", [None, "fixture-index"])
def test_manager_download_preserves_the_qualified_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index_name: str | None,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import governed_feed_trust as feed
    else:
        import governed_feed_trust as feed

    candidate, root = tmp_path / "source", tmp_path / "manager"
    payload = candidate / "worktree-manager"
    payload.mkdir(parents=True)
    for name in ("bootstrap.ps1", "bootstrap.sh"):
        (payload / name).write_text("fixture", encoding="utf-8")
    environment: dict[str, str] = {}
    index = "https://packages.example.invalid/simple"
    monkeypatch.setattr(feed, "_validated_trusted_index_url", lambda environment: (index, index_name))
    monkeypatch.setenv("UV_INDEX_FIXTURE_INDEX_USERNAME", "fixture-user")
    monkeypatch.setenv("UV_INDEX_FIXTURE_INDEX_PASSWORD", "fixture-secret")

    def run(arguments: list[str | Path], **options: Any) -> subprocess.CompletedProcess[str]:
        assert options["deadline"] == 123.0 and options["env"] is environment
        return subprocess.CompletedProcess(arguments, 0, "f" * 40 + "\n", "")

    monkeypatch.setattr(sys.modules[__name__], "_run", run)
    command, revision = _manager_download_install_command(
        candidate, root=root, shell="selected-shell" if os.name == "nt" else None,
        environment=environment, deadline=123.0,
    )
    assert revision == "f" * 40
    assert command[-2] == payload / ("bootstrap.ps1" if os.name == "nt" else "bootstrap.sh")
    assert command[-1] == "--describe-install"
    assert not any(str(argument).startswith("--install-source") for argument in command)
    configuration = Path(environment["UV_CONFIG_FILE"]).read_text(encoding="utf-8")
    assert f"url = {json.dumps(index)}\ndefault = true\n" in configuration
    if index_name:
        assert f"name = {json.dumps(index_name)}\n" in configuration
        assert environment["UV_INDEX_FIXTURE_INDEX_USERNAME"] == "fixture-user"
        assert environment["UV_INDEX_FIXTURE_INDEX_PASSWORD"] == "fixture-secret"
    else:
        assert "UV_INDEX_FIXTURE_INDEX_PASSWORD" not in environment


def _prepare_manager_application_inputs(
    candidate: Path, *, deadline: float,
) -> tuple[Path, list[tuple[str, Path]], tuple[Path, ...]]:
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder
        from tools import prepare_runtime_source as preparation, standalone_python_artifacts as standalone
    else:
        import build_python_artifacts as builder
        import prepare_runtime_source as preparation
        import standalone_python_artifacts as standalone

    source = candidate / "worktree-manager"
    preparation._materialize(ROOT, "worktree-manager", source, deadline=deadline)
    for resource in standalone.standalone_build_resources("worktree-manager", source_root=ROOT):
        shutil.copytree(
            resource, candidate / resource.relative_to(ROOT),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return standalone.source_closure("worktree-manager", candidate, builder)


def test_manager_download_preserves_bytes_under_caller_git_newline_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import governed_feed_trust as feed
    else:
        import governed_feed_trust as feed

    candidate, root = tmp_path / "source", tmp_path / "manager"
    payload = candidate / "worktree-manager"
    payload.mkdir(parents=True)
    expected = b"fixture line one\nfixture line two\n"
    for name in ("bootstrap.ps1", "bootstrap.sh"):
        (payload / name).write_bytes(expected)
    config = tmp_path / "gitconfig"
    config.write_text("[core]\nautocrlf = true\n", encoding="utf-8")
    environment = dict(os.environ, GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1")
    for key in list(environment):
        if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or key == "GIT_CONFIG_COUNT":
            environment.pop(key)
    monkeypatch.setattr(feed, "_validated_trusted_index_url", lambda environment: (
        "https://packages.example.invalid/simple", None,
    ))
    deadline = time.monotonic() + 30
    _, revision = _manager_download_install_command(
        candidate, root=root, shell=None, environment=environment, deadline=deadline,
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    downloaded = inputs / "source"
    _run(
        [shutil.which("git"), "clone", "--quiet", "--no-local", candidate, downloaded],
        cwd=tmp_path, env=environment, deadline=deadline,
    )
    assert _run(
        [shutil.which("git"), "-C", downloaded, "rev-parse", "HEAD"],
        cwd=tmp_path, env=environment, deadline=deadline,
    ).stdout.strip() == revision
    assert (downloaded / "worktree-manager" / "bootstrap.ps1").read_bytes() == expected
    _remove_application_inputs(inputs, downloaded=True)
    assert not inputs.exists()


def test_manager_download_configuration_preserves_the_prepared_application_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    if TYPE_CHECKING:
        from tools import governed_feed_trust as feed
    else:
        import governed_feed_trust as feed

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("a real UV executable is required")
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    home, caller, application = (tmp_path / name for name in ("home", "caller", "application"))
    for path in (home, caller, application):
        path.mkdir()
    environment = _manager_install_environment(
        home, home / ".worktree-manager", tmp_path, interpreter,
        caller_jobs=False, worktrees_environment=None,
    )
    (application / "pyproject.toml").write_text(
        '[project]\nname = "fixture-application"\nversion = "1.0.0"\n'
        'requires-python = ">=3.10"\ndependencies = []\n'
        '[tool.uv]\npackage = false\nexclude-newer = "7 days"\n',
        encoding="utf-8",
    )
    deadline = time.monotonic() + 30
    _run(
        [uv, "lock", "--offline", "--python", interpreter],
        cwd=application, env=environment, deadline=deadline,
    )
    original = (application / "uv.lock").read_bytes()
    candidate = tmp_path / "source"
    payload = candidate / "worktree-manager"
    payload.mkdir(parents=True)
    for name in ("bootstrap.ps1", "bootstrap.sh"):
        (payload / name).write_text("fixture bootstrap\n", encoding="utf-8")
    monkeypatch.setattr(feed, "_validated_trusted_index_url", lambda environment: (
        "https://packages.example.invalid/simple", None,
    ))
    _manager_download_install_command(
        candidate, root=home / ".worktree-manager", shell=None,
        environment=environment, deadline=deadline,
    )
    _run(
        [uv, "lock", "--check", "--offline", "--python", interpreter, "--project", application],
        cwd=caller, env=environment, deadline=deadline,
    )
    assert (application / "uv.lock").read_bytes() == original


def _remove_application_inputs(inputs: Path, *, downloaded: bool) -> None:
    if downloaded:
        from runtime_install_fixture import remove_fixture_worktree

        remove_fixture_worktree(inputs / "source")
    shutil.rmtree(inputs)


def _run_application_install(
    arguments: Sequence[str | Path], *, cwd: Path, env: dict[str, str],
    deadline: float, directory: Path,
) -> subprocess.CompletedProcess[str]:
    """Keep the enclosing test scope, not an output pipe, in charge of the service."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Manager application installation deadline expired")
    directory.mkdir()
    stdout, stderr = directory / "stdout.log", directory / "stderr.log"

    def captured(path: Path, limit: int) -> str:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        assert len(content) <= limit, "Manager installation output exceeded its bound"
        return content.decode("utf-8")

    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Manager application installation deadline expired")
            process_result = subprocess.run(
                list(map(str, arguments)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, timeout=remaining, **no_window_kwargs(),
            )
    except subprocess.TimeoutExpired as error:
        error.output = captured(stdout, 1024 * 1024).encode("utf-8")
        error.stderr = captured(stderr, 512 * 1024).encode("utf-8")
        raise
    result = subprocess.CompletedProcess(
        process_result.args, process_result.returncode, captured(stdout, 1024 * 1024),
        captured(stderr, 512 * 1024),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if time.monotonic() >= deadline:
        raise TimeoutError("Manager application installation deadline expired during capture")
    return result


def test_manager_install_capture_does_not_wait_for_a_service_owned_by_the_enclosing_scope(
    tmp_path: Path,
) -> None:
    from agent_remote_driver.session_host.osutil import pid_alive
    from zdd.diagnostics import process_start_time

    release, identity = tmp_path / "release", tmp_path / "child.json"
    child = (
        "import sys,time\nfrom pathlib import Path\n"
        "end=time.monotonic()+5\n"
        "while not Path(sys.argv[1]).exists() and time.monotonic()<end: time.sleep(.02)\n"
    )
    script = tmp_path / "installer.py"
    script.write_text(
        "import json,subprocess,sys\nfrom pathlib import Path\n"
        "from zdd.diagnostics import process_start_time\n"
        f"child=subprocess.Popen([sys.executable,'-I','-B','-c',{child!r},{str(release)!r}],"
        "stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        f"Path({str(identity)!r}).write_text(json.dumps({{'pid':child.pid,"
        "'birth':process_start_time(child.pid)}),encoding='utf-8')\n"
        "print(json.dumps({'state':'installed'}),flush=True)\n",
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 2
        result = _run_application_install(
            [sys.executable, "-I", "-B", script], cwd=tmp_path, env=dict(os.environ),
            deadline=deadline, directory=tmp_path / "output",
        )
        assert json.loads(result.stdout) == {"state": "installed"}
        assert time.monotonic() < deadline
        owned = json.loads(identity.read_text(encoding="utf-8"))
        assert owned["birth"] and pid_alive(owned["pid"])
        assert process_start_time(owned["pid"]) == owned["birth"]
    finally:
        release.write_text("release\n", encoding="utf-8")
        if identity.is_file():
            owned = json.loads(identity.read_text(encoding="utf-8"))
            cleanup_deadline = time.monotonic() + 5
            while pid_alive(owned["pid"]) and process_start_time(owned["pid"]) == owned["birth"]:
                assert time.monotonic() < cleanup_deadline, owned
                time.sleep(.02)


def test_manager_install_capture_refuses_expiry_before_process_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_options: pytest.fail("expired installer launched"),
    )
    directory = tmp_path / "output"
    with pytest.raises(TimeoutError, match="deadline expired"):
        _run_application_install(
            ["unused"], cwd=tmp_path, env={}, deadline=time.monotonic() - 1,
            directory=directory,
        )
    assert not directory.exists()


def test_manager_install_capture_retains_partial_timeout_diagnostics(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired) as observed:
        _run_application_install(
            [
                sys.executable, "-I", "-B", "-c",
                "import sys,time;print('fixture-started',flush=True);"
                "print('fixture-error',file=sys.stderr,flush=True);time.sleep(5)",
            ],
            cwd=tmp_path, env=dict(os.environ), deadline=time.monotonic() + 2,
            directory=tmp_path / "output",
        )
    assert b"fixture-started" in observed.value.output
    assert observed.value.stderr and b"fixture-error" in observed.value.stderr


def _manager_download_install_command(
    candidate: Path, *, root: Path, shell: str | None,
    environment: dict[str, str], deadline: float,
) -> tuple[list[str | Path], str]:
    if TYPE_CHECKING:
        from tools.governed_feed_trust import _validated_trusted_index_url
    else:
        from governed_feed_trust import _validated_trusted_index_url

    index = _validated_trusted_index_url(dict(os.environ))
    assert index is not None, "Manager download requires the selected qualified package feed"
    git = shutil.which("git")
    assert git, "Manager download qualification requires Git"
    # Pin checkout bytes independently of the caller's global Git newline policy.
    (candidate / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    for arguments in (
        ["init", "--quiet", str(candidate)],
        [
            "-C", str(candidate), "-c", "core.longpaths=true",
            "-c", "core.autocrlf=false", "add", "--force", "--all",
        ],
        [
            "-C", str(candidate), "-c", "user.name=Manager fixture",
            "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "core.hooksPath=", "commit", "--quiet", "-m", "Manager download fixture",
        ],
    ):
        _run([git, *arguments], cwd=candidate, env=environment, deadline=deadline)
    revision = _run(
        [git, "-C", candidate, "rev-parse", "HEAD"],
        cwd=candidate, env=environment, deadline=deadline,
    ).stdout.strip()
    root.mkdir(parents=True)
    (root / "config.toml").write_text(
        "[source]\n"
        f"repo = {json.dumps(candidate.as_posix())}\n"
        f"ref = {json.dumps(revision)}\n",
        encoding="utf-8",
    )
    index_url, index_name = index
    uv_config = root / "bootstrap-uv.toml"
    uv_config.write_text(
        'exclude-newer = "7 days"\n[[index]]\n'
        + (f"name = {json.dumps(index_name)}\n" if index_name else "")
        + f"url = {json.dumps(index_url)}\ndefault = true\n",
        encoding="utf-8",
    )
    environment["UV_CONFIG_FILE"] = str(uv_config)
    if index_name:
        prefix = "UV_INDEX_" + re.sub(r"[^A-Za-z0-9]", "_", index_name).upper()
        for suffix in ("_USERNAME", "_PASSWORD"):
            key = prefix + suffix
            if key in os.environ:
                environment[key] = os.environ[key]
    bootstrap = candidate / "worktree-manager" / ("bootstrap.ps1" if os.name == "nt" else "bootstrap.sh")
    assert bootstrap.is_file()
    command: list[str | Path] = (
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", bootstrap]
        if shell is not None else ["bash", bootstrap]
    )
    return [*command, "--describe-install"], revision


def _manager_application_install_command(
    candidate: Path, *, inputs: Path, root: Path, profile: Path, shell: str | None,
    interpreter: str, uv: str, deadline: float, monkeypatch: pytest.MonkeyPatch,
    source_identity: tuple[str, str] | None = None,
) -> tuple[list[str | Path], Path]:
    if TYPE_CHECKING:
        from tools import build_python_artifacts as builder, local_install_profile as profiles
        from tools import standalone_python_artifacts as standalone
    else:
        import build_python_artifacts as builder
        import local_install_profile as profiles
        import standalone_python_artifacts as standalone

    source, dependencies, resources = standalone.source_closure("worktree-manager", candidate, builder)
    engine = profiles.source_inputs.engine
    artifacts = inputs / "wheels"
    with monkeypatch.context() as patch:
        patch.setattr(builder, "REPO", candidate)
        artifact = builder.build_source_artifacts(
            identity={"component": "worktree-manager"}, source_dir=source,
            dependencies=dependencies, resources=resources,
            out_dir=artifacts, python=interpreter, toolchain=None,
            uv=uv, deadline=deadline,
        )
    project = cast(_BuilderExports, builder).tomllib.loads((source / "pyproject.toml").read_text())["project"]
    source_hash = engine.runtime_source.assembly_hash(source)
    commit, tree = source_identity or ("a" * 40, "b" * 40)
    identity = engine.runtime_source.SourceIdentity(
        "worktree-manager", project["version"], commit, tree, source_hash,
        engine.runtime_source.runtime_version(project["version"], commit, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    (inputs / profiles.source_inputs.MANIFEST).write_text(json.dumps({
        "schema": profiles.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"worktree-manager": {
            "payload": source.relative_to(inputs).as_posix(), "wheels": "wheels",
            "source": identity.document(), "artifact": artifact,
        }},
    }), encoding="utf-8")
    prepared = profiles.prepare_registry_lock(
        inputs, profile, uv=uv, deadline=deadline,
        prepare_profile=True, component="worktree-manager",
    )
    assert prepared["install_ready"] is True
    application = Path(prepared["snapshot"]) / "applications" / "worktree-manager"
    declaration = json.loads((application / "application.json").read_text())
    assert declaration["setup"] == declaration["start"] == []
    slot = root / "versions" / declaration["construction"]["runtime_version"]
    shutil.copytree(application, slot)
    install = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", slot / "install.ps1"] \
        if shell is not None else [slot / "install"]
    return install, slot


def _qualify_manager_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control: str, *,
    worktrees_python: Path | None = None, worktrees_root: Path | None = None,
    worktrees_environment: dict[str, str] | None = None, caller_jobs: bool = False,
    replacement_slot: Path | None = None, picker_decision: bool = False,
    rollback_application: Path | None = None, download_manager: bool = False,
) -> None:
    paired = worktrees_python is not None
    assert not paired or (control in {"embody", "copilot"} and worktrees_environment is not None)
    selected_worktrees = {} if worktrees_environment is None else worktrees_environment
    assert not picker_decision or (not paired and control in {"manager-no-mux", "manager-mux"})
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
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
    shell = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else None
    assert uv and (os.name != "nt" or shell)
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    deadline = time.monotonic() + (270 if _NATIVE_COPILOT or download_manager else 300)
    inputs = tmp_path / "source-inputs"
    candidate = inputs / "source"
    if _NATIVE_COPILOT or download_manager:
        candidate = tmp_path / "source-remote"
    source = candidate / "worktree-manager"
    candidate.mkdir(parents=True)
    selected, _, _ = _prepare_manager_application_inputs(candidate, deadline=deadline)
    assert selected == source
    engine = profiles.source_inputs.engine
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = home / ".worktree-manager"
    environment = _manager_install_environment(
        home, root, tmp_path, interpreter,
        caller_jobs=caller_jobs, worktrees_environment=worktrees_environment,
    )
    downloaded_revision = None
    if _NATIVE_COPILOT or download_manager:
        install, downloaded_revision = _manager_download_install_command(
            candidate, root=root, shell=shell, environment=environment, deadline=deadline,
        )
    else:
        install, slot = _manager_application_install_command(
            candidate, inputs=inputs, root=root, profile=tmp_path / "profile", shell=shell,
            interpreter=interpreter, uv=uv, deadline=deadline, monkeypatch=monkeypatch,
        )
    from application_python_fixture import managed_python_environment

    environment = managed_python_environment(
        environment, directory=tmp_path / "managed-python", uv=uv,
        deadline=deadline, run=_run, version="3.11" if downloaded_revision is not None else None,
    )
    (tmp_path / "mux-data").mkdir()
    cleanup: list[InstanceCleanupResult] = []
    lifecycle_owner = nullcontext({}) if caller_jobs else daemon_test_lifecycle(
        owner="manager-application-launch-fixture", registry_root=tmp_path / "daemon-instances",
        deadline=deadline + (30 if downloaded_revision is not None else 0) + (180 if paired else 0),
        cleanup_results=cleanup,
    )
    try:
        with lifecycle_owner as lifecycle:
            environment.update(lifecycle)
            if downloaded_revision is not None:
                try:
                    installed = run_caller_owned(
                        list(map(str, install)), cwd=caller, env=environment,
                        timeout=max(0.01, deadline - time.monotonic()),
                        max_stdout_bytes=1048576, max_stderr_bytes=1048576,
                    )
                except subprocess.TimeoutExpired as error:
                    for label, content in (("stdout", error.output), ("stderr", error.stderr)):
                        if isinstance(content, bytes):
                            content = content.decode("utf-8", errors="replace")
                        print(f"Manager bootstrap timeout {label}:\n{(content or '')[-65536:]}", flush=True)
                    raise
                assert installed.returncode == 0, installed.stdout + installed.stderr
            else:
                installed = _run_application_install(
                    install, cwd=caller, env=environment, deadline=deadline,
                    directory=tmp_path / "application-output",
                )
            if downloaded_revision is not None:
                fetched = _run(
                    [shutil.which("git"), "-C", root / "staging", "rev-parse", "HEAD"],
                    cwd=caller, env=environment, deadline=deadline,
                ).stdout.strip()
                assert fetched == downloaded_revision
                fetched_tree = _run(
                    [shutil.which("git"), "-C", root / "staging", "rev-parse", "HEAD^{tree}"],
                    cwd=caller, env=environment, deadline=deadline,
                ).stdout.strip()
                assert (root / "staging" / "worktree-manager" / "scripts" / "runtime_install.py").read_bytes() == (
                    source / "scripts" / "runtime_install.py"
                ).read_bytes()
                assert json.loads(installed.stdout)["schema"] == "copilot-extensions.manager-install-entry"
                inputs.mkdir()
                downloaded = inputs / "source"
                (root / "staging").rename(downloaded)
                environment = managed_python_environment(
                    environment, directory=tmp_path / "application-python", uv=uv,
                    deadline=deadline, run=_run,
                )
                install, slot = _manager_application_install_command(
                    downloaded, inputs=inputs, root=root, profile=tmp_path / "profile", shell=shell,
                    interpreter=interpreter, uv=uv, deadline=deadline, monkeypatch=monkeypatch,
                    source_identity=(fetched, fetched_tree),
                )
                installed = _run_application_install(
                    install, cwd=caller, env=environment, deadline=deadline,
                    directory=tmp_path / "application-output",
                )
            assert json.loads(installed.stdout.splitlines()[-1])["state"] == "installed"
            assert (root / "current-version").read_text().strip() == slot.name
            completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
            _remove_application_inputs(inputs, downloaded=downloaded_revision is not None)
            runtime_environment = slot / ".venv"
            python = runtime_environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            assets = _run([
                python, "-I", "-B", "-X", "utf8", "-c",
                "import json;from worktree_manager.relocated_launch import _relocated_launch_script;"
                "script=_relocated_launch_script();assert script is not None;"
                "assert (script.parent/'ahp-command.py').is_file();"
                "print(json.dumps({'script':str(script)}))",
            ], cwd=caller, env=environment, deadline=deadline)
            script = Path(json.loads(assets.stdout)["script"])
            assert script.is_relative_to(runtime_environment) and "_runtime_assets" in script.parts
            consumer = _run([
                str(worktrees_python) if paired else sys.executable, "-I", "-B", "-X", "utf8", "-c",
                "import json;from agent_worktrees.manager_launch_cli import _usable_worktree_manager_launcher_dir;"
                "directory=_usable_worktree_manager_launcher_dir();"
                "assert directory is not None,'Worktrees rejected the real current Manager application';"
                "print(json.dumps({'directory':str(directory)}))",
            ], cwd=caller, env={
                **(selected_worktrees if paired else environment),
                "WORKTREE_MANAGER_ROOT": str(root),
            }, deadline=deadline)
            assert Path(json.loads(consumer.stdout)["directory"]).samefile(script.parent)
            if paired:
                assert time.monotonic() < deadline, "Manager construction exceeded 300 seconds"
                native = tmp_path / "n"
                native.mkdir()
                command = [
                    sys.executable, "-I", "-B", "-X", "utf8", "-c",
                    "import os,sys;from pathlib import Path;"
                    "sys.path[:0]=sys.argv[1:3];"
                    "from test_headed_worktrees_copilot import _qualify_native_worktrees;"
                    "_qualify_native_worktrees(Path(sys.argv[3]),sys.argv[7],"
                    "worktrees_python=Path(sys.argv[4]),worktrees_root=Path(sys.argv[5]),"
                    "worktrees_environment=dict(os.environ),caller_jobs=sys.argv[8]=='1',"
                    "replacement_slot=Path(sys.argv[9]) if sys.argv[9] else None,"
                    "rollback_application=Path(sys.argv[10]) if sys.argv[10] else None,"
                    "manager_environment={'WORKTREE_MANAGER_ROOT':sys.argv[6]})",
                    str(Path(__file__).parent), str(ROOT / "tools"), str(native),
                    str(worktrees_python), str(worktrees_root), str(root), control,
                    "1" if caller_jobs else "0",
                    str(replacement_slot) if replacement_slot is not None else "",
                    str(rollback_application) if rollback_application is not None else "",
                ]
                native_environment = {
                    **selected_worktrees, **lifecycle,
                    "COPILOT_EXTENSIONS_TEST_SANDBOX": os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"],
                }
                if caller_jobs:
                    qualified = _run_lifetime_native(
                        command, directory=native, cwd=caller, environment=native_environment,
                    )
                else:
                    qualified = run_caller_owned(
                        command, cwd=caller, env=native_environment, timeout=180,
                        max_stdout_bytes=65536, max_stderr_bytes=65536,
                    )
                print(qualified.stdout, flush=True)
                assert qualified.returncode == 0, qualified.stdout + qualified.stderr
            elif _NATIVE_COPILOT:
                native = tmp_path / "native-launch"
                native.mkdir()
                _run_manager_native(
                    native, control, script=script, python=python, root=root,
                    environment={**os.environ, **lifecycle}, picker_decision=picker_decision,
                )
            assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion
    except (AssertionError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.TimeoutExpired):
            for label, content in (("stdout", error.output), ("stderr", error.stderr)):
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
                print(f"Manager application timeout {label}:\n{(content or '')[-16_384:]}", flush=True)
        for log in sorted((home / ".copilot-extensions" / "update-driver").glob("*.log"))[:4]:
            try:
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 16_384))
                    print(
                        f"Manager installer log {log.name}:\n"
                        + stream.read().decode("utf-8", errors="replace"),
                        flush=True,
                    )
            except OSError:
                pass
        raise
    finally:
        assert all(result.status == "settled" for result in cleanup), cleanup
    assert caller_jobs or cleanup or downloaded_revision is not None, (
        "Manager application installation did not start its owned resident"
    )
