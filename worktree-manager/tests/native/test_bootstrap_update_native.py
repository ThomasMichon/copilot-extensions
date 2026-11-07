"""Actual bootstrap, frozen worker and borrowed participant; finite install bodies."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from textwrap import indent
import time
import venv

import pytest

import agent_procutil
from update_driver import core
from worktree_manager import self_install
from worktree_manager.engine_acquisition_budget import TIMEOUT_ENV
from worktree_manager.source_config import SOURCE_ANCHOR_ENV

pytestmark = pytest.mark.timeout(90)
SOURCE = Path(__file__).resolve().parents[3]
LIBRARIES = (
    "update-driver", "single-instance-lease", "agent-procutil", "zdd",
    "worktree-identity", "update-telemetry",
)

CONSTRUCTOR = '''
def _fixture_install(args):
    from update_driver import core, participant_environment
    from single_instance_lease.operation import OperationLock, OperationLockedError

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    identifier = None
    if command[:1] == ["update"]:
        assert participant_environment()
        try:
            OperationLock(core.default_root() / "execution.lock").acquire()
        except OperationLockedError:
            pass
        else:
            raise AssertionError("constructor escaped whole-update custody")
        assert _source_digest(args.payload) == args.source_digest
        request, = (row for row in core.status() if row.state == "running")
        identifier = request.id
    with (args.root / "effects.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "phase": "manager", "request": identifier, "pid": os.getpid(),
            "payload": str(args.payload),
        }) + "\\n")
    (args.root / "fixture-payload").write_text(str(args.payload), encoding="utf-8")
    (args.root / "fixture-controller").write_text(str(SCRIPTS.parent), encoding="utf-8")
    return args.root / "versions" / "fixture"

_install = _fixture_install
if __name__ == "__main__" and "update" in sys.argv:
    _execution_driver(SCRIPTS.parent)
    from worktree_manager import update_python_path

    _original_finish_update = update_python_path.finish_update
    def _fixture_finish_update(command, result):
        if result == 0:
            root = Path(sys.argv[sys.argv.index("--root") + 1])
            with (root / "finalizers.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "command": command, "participant": "--update-participant" in sys.argv,
                }) + "\\n")
        return _original_finish_update(command, result)
    update_python_path.finish_update = _fixture_finish_update

if __name__ == "__main__" and "--update-participant" not in sys.argv and "update" in sys.argv:
    _execution_driver(SCRIPTS.parent)
    from agent_procutil._daemon_instance_identity import birth_identity

    root = Path(sys.argv[sys.argv.index("--root") + 1])
    (root / f"caller-{os.getpid()}.json").write_text(json.dumps({
        "pid": os.getpid(), "birth": birth_identity(os.getpid()),
    }), encoding="utf-8")
    if "--auth-report" in sys.argv:
        from worktree_manager import engine_client

        def report(project, arguments, **options):
            assert arguments[:2] == ["update", "--no-manager"]
            assert "--auth-report" in arguments and "--json" in arguments
            with (root / "effects.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"phase": "auth-report"}) + "\\n")
            print(json.dumps({"reports": []}))
            return 0

        engine_client.run_engine_passthrough = report
'''

SUITE = '''
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import ModuleType

root = Path(__file__).resolve().parents[3]
payload = Path((root / "fixture-payload").read_text(encoding="utf-8"))
controller = Path((root / "fixture-controller").read_text(encoding="utf-8"))
sys.path.insert(0, str(controller / "scripts"))
sys.path.insert(0, str(controller / "src"))
import runtime_install

driver = runtime_install._execution_driver(controller)
sys.path[:0] = [
    str(package / "src") for package in runtime_install.engine._package_roots(controller)
]
from update_driver import participant_environment
from single_instance_lease.operation import OperationLock, OperationLockedError

prior = payload / "src" / "worktree_manager"
if (prior / "update_driver_cli.py").is_file():
    import worktree_manager

    for name in ("source_config", "update_driver_cli"):
        spec = importlib.util.spec_from_file_location(
            "worktree_manager." + name, prior / (name + ".py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(worktree_manager, name, module)
        spec.loader.exec_module(module)

if sys.argv[3] != "update":
    assert not participant_environment()
    with runtime_install._installation_custody() as custody:
        assert custody is None
    with (root / "effects.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "phase": "inspection", "argv": sys.argv[1:], "witness": False,
        }) + "\\n")
    raise SystemExit(0)

with runtime_install._installation_custody() as custody:
    assert custody is not None
    custody.assert_owned()
    try:
        OperationLock(driver.default_root() / "execution.lock").acquire()
    except OperationLockedError:
        pass
    else:
        raise AssertionError("suite escaped whole-update custody")
    request, = (row for row in driver.status() if row.state == "running")
    def effect(phase):
        from worktree_manager.source_config import absolute_local_source, resolved_repo

        with (root / "effects.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "phase": phase, "request": request.id, "pid": os.getpid(),
                "payload": str(payload), "argv": sys.argv[1:],
                "acquisition_budget": os.environ.get(
                    "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC",
                ),
                "source_repo": absolute_local_source(resolved_repo(root)),
            }) + "\\n")
    def body(arguments):
        effect("suite-start")
        deadline = time.monotonic() + 40
        while not (root / "release-suite").exists():
            custody.assert_owned()
            if time.monotonic() >= deadline:
                raise TimeoutError("finite fixture suite was not released")
            time.sleep(.02)
        effect("suite-end")
        successful = root / "successful-request"
        return 0 if successful.exists() and successful.read_text() == request.id else 23

    from worktree_manager import update_driver_cli
    if (prior / "update_driver_cli.py").is_file():
        assert Path(update_driver_cli.__file__) == prior / "update_driver_cli.py"

    manager = ModuleType("worktree_manager.__main__")
    manager._cmd_update = body
    sys.modules[manager.__name__] = manager
    os.environ.update(participant_environment())
    assert sys.argv[1:4] == ["-m", "worktree_manager", "update"]
    result = update_driver_cli.cmd_update_request(sys.argv[4:])
raise SystemExit(result)
'''

DEQUEUE_CONTROL = '''
def _fixture_execute(transaction):
    root = Path(os.environ["WORKTREE_MANAGER_ROOT"])
    successful = root / "successful-request"
    if successful.exists() and successful.read_text() != transaction.request.id:
        deadline = time.monotonic() + 15
        while list((root / "bootstrap-acquisition").iterdir()):
            if time.monotonic() >= deadline:
                raise TimeoutError("first caller did not remove its acquisition")
            time.sleep(.02)
        import agent_procutil

        (root / "draining-worker-procutil").write_text(agent_procutil.__file__)
    return driver._execute(transaction)

raise SystemExit(driver.drain(executor=_fixture_execute))
'''


def _eventually(check, *, processes=(), timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        assert all(process.poll() is None for process in processes), "bootstrap exited early"
        time.sleep(.02)
    raise AssertionError("native bootstrap observation timed out")


def _git(source, *arguments):
    from agent_procutil.owned_process import run_owned

    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
    )
    result = run_owned(
        [
            "git", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
            "-c", "core.longpaths=true", "-C", str(source), *arguments,
        ],
        env=environment, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="session")
def bootstrap_source(tmp_path_factory):
    started = time.monotonic()
    host = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")
    if host is None:
        pytest.skip("native bootstrap shell is unavailable")
    assert os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
    adapter = self_install._runtime_installer()
    source = tmp_path_factory.mktemp("bootstrap-source") / "source"
    roots = {
        package
        for application in (
            SOURCE / "libs" / name
            for name in LIBRARIES
        )
        for package in adapter.engine._package_roots(application)
    }
    for package in sorted(roots):
        shutil.copytree(
            package, source / package.relative_to(SOURCE),
            ignore=shutil.ignore_patterns(
                "tests", "docs", "skills", "__pycache__", "*.egg-info", "*.dist-info",
                ".ruff_cache", ".pytest_cache",
            ),
        )
    manager = source / "worktree-manager"
    scripts = manager / "scripts"
    scripts.mkdir(parents=True)
    for script in (SOURCE / "worktree-manager" / "scripts").glob("*.py"):
        shutil.copy2(script, scripts / script.name)
    package = manager / "src" / "worktree_manager"
    package.mkdir(parents=True)
    for name in (
        "__init__", "update_driver_cli", "engine_acquisition_budget", "source_config",
        "runtime_source_config", "runtime_source", "self_install", "self_install_runtime",
        "self_install_payload", "config_write_lock", "update_python_path", "runtime_identity",
        "runtime_launchers", "self_install_recovery", "mux_file_lock",
        "engine_client", "engine_progress", "engine_sessions", "agent_plugin_runtime", "picker_review", "engine_posix_process",
        "engine_environment", "worktree_rows_contract", "worktree_observation_contract",
        "update_orchestration", "update_events", "project_update_controller",
    ):
        shutil.copy2(SOURCE / "worktree-manager" / "src" / "worktree_manager" / f"{name}.py", package)
    for name in ("bootstrap.ps1", "bootstrap.sh"):
        shutil.copy2(SOURCE / "worktree-manager" / name, manager)
    dependencies = {
        adapter.engine._package_name(application): application.relative_to(source).as_posix()
        for application in (
            source / "libs" / name
            for name in LIBRARIES
        )
    }
    (manager / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n'
        f"dependencies={json.dumps(list(dependencies))}\n[tool.uv.sources]\n"
        + "\n".join(f'{name}={{path="../{path}",editable=true}}' for name, path in dependencies.items())
        + "\n", encoding="utf-8",
    )
    sdk = source / "plugins" / "agent-worktrees"
    (sdk / "src").mkdir(parents=True)
    (sdk / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n', encoding="utf-8",
    )
    script = source / "worktree-manager" / "scripts" / "runtime_install.py"
    body = script.read_text(encoding="utf-8")
    entry = '\nif __name__ == "__main__":'
    assert body.count(entry) == 1
    script.write_text(body.replace(entry, CONSTRUCTOR + entry), encoding="utf-8")
    worker = scripts / "bootstrap_update.py"
    body = worker.read_text(encoding="utf-8")
    drain_entry = "raise SystemExit(driver.drain())"
    assert body.count(drain_entry) == 1
    worker.write_text(
        body.replace(drain_entry, "import time\n" + indent(DEQUEUE_CONTROL.strip(), "    ")),
        encoding="utf-8",
    )
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", ".")
    _git(
        source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "finite bootstrap source",
    )
    print(json.dumps({
        "stage": "source-fixture-ready", "elapsed": round(time.monotonic() - started, 3),
    }), file=sys.stderr, flush=True)
    return source


@pytest.fixture
def bootstrap(tmp_path, bootstrap_source):
    started = time.monotonic()
    host = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")
    source = tmp_path / "source"
    shutil.copytree(bootstrap_source, source)
    _git(source, "update-index", "--refresh")
    scripts = source / "worktree-manager" / "scripts"
    profile = tmp_path / "profile"
    profile.mkdir()
    root = tmp_path / "manager"
    root.mkdir()
    (root / "config.toml").write_text(
        f'[source]\nrepo={json.dumps(source.as_posix())}\nref="main"\n', encoding="utf-8",
    )
    sentinel = root / "staging" / "unrelated"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"retained shared staging")
    slot = root / "versions" / "fixture"
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    launcher = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    launcher.parent.mkdir()
    launcher.write_text(SUITE, encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    python = str(Path(sys._base_executable).resolve())
    if os.name == "nt":
        (tools / "uv.ps1").write_text(
            f"'{python.replace(chr(39), chr(39) * 2)}'\n$global:LASTEXITCODE = 0\n",
            encoding="utf-8",
        )
        command = [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(source / "worktree-manager" / "bootstrap.ps1"),
        ]
    else:
        uv = tools / "uv"
        uv.write_text(f"#!/bin/sh\nprintf '%s\\n' '{python}'\n", encoding="utf-8")
        uv.chmod(0o755)
        command = [host, str(source / "worktree-manager" / "bootstrap.sh")]
    environment = {
        **os.environ, "HOME": str(profile), "USERPROFILE": str(profile),
        "WORKTREE_MANAGER_ROOT": str(root),
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "never",
        "UV_OFFLINE": "1",
    }
    for key in ("UPDATE_DRIVER_WITNESS", "UPDATE_DRIVER_STARTUP"):
        environment.pop(key, None)
    environment.pop(TIMEOUT_ENV, None)
    environment.pop(SOURCE_ANCHOR_ENV, None)
    driver = profile / ".copilot-extensions" / "update-driver"
    callers, streams = [], []

    def start(
        *arguments, install_source=None, command_name="update", acquisition_budget=None,
        direct=False, source_anchor=None,
    ):
        log = tmp_path / f"caller-{len(callers)}.log"
        stream = log.open("wb")
        streams.append(stream)
        output = log.with_suffix(".out").open("wb")
        streams.append(output)
        invocation = [
            *command, *(["--install-source", str(install_source)] if install_source is not None else []),
            command_name, *arguments,
        ]
        if direct:
            invocation = [
                python, "-I", "-S", "-B", str(scripts / "runtime_install.py"),
                "--root", str(root),
                *(["--install-source", str(install_source)] if install_source is not None else []),
                "--", command_name, *arguments,
            ]
        launch_environment = dict(environment)
        if acquisition_budget is not None:
            launch_environment[TIMEOUT_ENV] = acquisition_budget
        if source_anchor is not None:
            launch_environment[SOURCE_ANCHOR_ENV] = str(source_anchor)
        process = subprocess.Popen(
            invocation, cwd=tmp_path, env=launch_environment,
            stdin=subprocess.DEVNULL, stdout=output, stderr=stream,
            **agent_procutil.no_window_kwargs(),
        )
        callers.append(process)
        return process, log

    def effects():
        path = root / "effects.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    try:
        print(json.dumps({
            "stage": "case-fixture-ready", "elapsed": round(time.monotonic() - started, 3),
        }), file=sys.stderr, flush=True)
        yield root, source, driver, start, effects
    finally:
        (root / "release-suite").touch()
        for process in callers:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()
        for log in sorted(tmp_path.glob("caller-*.log")):
            print(f"{log.name}:\n{log.read_text(encoding='utf-8')}", file=sys.stderr)
            print(log.with_suffix(".out").read_text(encoding="utf-8"), file=sys.stderr)
        if driver.exists():
            for record in core.status(root=driver):
                print(f"{record.id}: {record.state} {record.error}", file=sys.stderr)
                if record.log_path.exists():
                    print(record.log_path.read_text(encoding="utf-8"), file=sys.stderr)
        assert sentinel.read_bytes() == b"retained shared staging"


def test_retained_controller_prepares_dev_source_without_its_acquisition(
    bootstrap, tmp_path, monkeypatch,
):
    from worktree_manager import runtime_source_prepare as preparation
    from worktree_manager.runtime_source import ResolvedSource
    from worktree_manager.runtime_source_config import RuntimeSource

    monkeypatch.syspath_prepend(str(SOURCE / "worktree-manager" / "scripts"))
    import bootstrap_inputs

    _root, acquisition, driver, _start, _effects = bootstrap
    for filename in ("packaged-assets.json", "launch-wrapper-assets.json"):
        manifest = SOURCE / "worktree-manager" / filename
        declaration = json.loads(manifest.read_text(encoding="utf-8"))
        shutil.copy2(manifest, acquisition / "worktree-manager" / filename)
        for group in declaration.get("groups", [declaration]):
            for name in group["files"]:
                relative = Path(group["canonicalDir"]) / name
                target = acquisition / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SOURCE / relative, target)
    shutil.copytree(
        SOURCE / "libs/installer-engine", acquisition / "libs/installer-engine",
        ignore=shutil.ignore_patterns("tests", "docs", "__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )
    _git(acquisition, "add", ".")
    _git(
        acquisition, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "declared source preparation assets",
    )
    retained, _share = bootstrap_inputs.retain(acquisition / "worktree-manager", driver)
    selected = tmp_path / "selected"
    shutil.copytree(acquisition, selected)
    _git(selected, "update-index", "--refresh")
    dirty = _git(selected, "status", "--porcelain", "--untracked-files=all", "--ignored")
    assert not dirty, dirty
    commit, tree = _git(selected, "rev-parse", "HEAD"), _git(selected, "rev-parse", "HEAD^{tree}")
    _git(selected, "update-ref", "refs/remotes/origin/dev", commit)
    for path in acquisition.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    shutil.rmtree(acquisition)
    monkeypatch.setattr(preparation, "_runtime_scripts", lambda: retained / "scripts")
    monkeypatch.setattr(
        preparation, "running_payload_dir",
        lambda: pytest.fail("retained controller fell back to an acquisition checkout"),
    )
    resolved = ResolvedSource(RuntimeSource("dev", allow_dev=True), selected, commit, tree, commit)
    tool = preparation.preparer()
    assert tool.is_relative_to(retained)
    prepared = preparation.prepare(tool, resolved, "worktree-manager", tmp_path / "prepared")
    assert (Path(prepared["payload"]) / "src" / "worktree_manager" / "__init__.py").read_bytes() == (
        selected / "worktree-manager" / "src" / "worktree_manager" / "__init__.py"
    ).read_bytes()
    assert prepared["identity"]["runtime_source"]["source_commit"] == commit


@pytest.mark.parametrize("lose_caller", [False, True])
def test_native_bootstrap_coalesces_pending_work_and_retains_worker_after_caller_loss(
    bootstrap, lose_caller,
):
    root, source, driver, start, effects = bootstrap
    started = time.monotonic()

    def milestone(stage):
        print(json.dumps({
            "stage": stage, "elapsed": round(time.monotonic() - started, 3),
            "states": [row.state for row in core.status(root=driver)],
            "phases": [row["phase"] for row in effects()],
        }), file=sys.stderr, flush=True)

    if lose_caller:
        (root / "config.toml").write_text(
            '[source]\nrepo="source"\nref="main"\n', encoding="utf-8",
        )
    first, first_log = start("--project", "sample")
    # Durable admission finishes acquisition/retention; observe the next owned stage separately.
    _eventually(
        lambda: "Update request " in first_log.read_text(encoding="utf-8"), processes=(first,),
        timeout=60,
    )
    _eventually(
        lambda: any(row["phase"] == "suite-start" for row in effects()),
        processes=(first,),
    )
    milestone("first-suite-started")
    original, = core.status(root=driver)
    assert original.state == "running" and original.exit_code is None
    assert original.spec.backend == "manager"
    assert [row["phase"] for row in effects()] == ["manager", "suite-start"]
    assert Path(effects()[1]["source_repo"]) == source
    retained = Path(effects()[0]["payload"])
    assert retained.is_relative_to(driver / "bootstrap-inputs")
    assert retained != source / "worktree-manager"
    admission, = root.glob("caller-*.json")
    caller = json.loads(admission.read_text(encoding="utf-8"))

    second, second_log = start("--project", "sample", "--wait")
    _eventually(lambda: len(core.status(root=driver)) == 2, processes=(first, second))
    running, pending = core.status(root=driver)
    assert running.id == original.id and running.state == "running"
    assert pending.id != original.id and pending.state == "pending"
    _eventually(
        lambda: f"Update request {pending.id};" in second_log.read_text(encoding="utf-8"),
        processes=(first, second),
    )
    milestone("second-pending")
    assert second.poll() is None
    if lose_caller:
        from agent_procutil.process_retirement import PinnedProcess

        with PinnedProcess(caller["pid"], caller["birth"]) as original_caller:
            original_caller.terminate()
            assert original_caller.exited()
        first.wait(timeout=5)
    equivalent, equivalent_log = start("--project", "sample", "--detach", "--json")
    different, different_log = start("--project", "other")
    _eventually(
        lambda: f"Update request {pending.id};" in equivalent_log.read_text(encoding="utf-8"),
        processes=(second, equivalent, different),
    )
    _eventually(lambda: len(core.status(root=driver)) == 3, processes=(second, different))
    milestone("mixed-observers-admitted")
    running, queued, distinct = core.status(root=driver)
    assert running.id == original.id and running.state == "running"
    assert queued.id == pending.id and distinct.state == "pending"
    assert [row["phase"] for row in effects()] == ["manager", "suite-start"]
    (root / "release-suite").touch()
    milestone("suite-released")
    assert second.wait(timeout=20) == 23, second_log.read_text(encoding="utf-8")
    milestone("second-completed")
    assert equivalent.wait(timeout=20) == 0, equivalent_log.read_text(encoding="utf-8")
    assert json.loads(equivalent_log.with_suffix(".out").read_text())["request_id"] == pending.id
    milestone("equivalent-accepted")
    assert different.wait(timeout=20) == 23, different_log.read_text(encoding="utf-8")
    milestone("distinct-completed")
    if not lose_caller:
        assert first.wait(timeout=20) == 23, first_log.read_text(encoding="utf-8")
    completed = core.status(root=driver)
    assert all(row.state == "completed" and row.exit_code == 23 for row in completed)
    assert [row["phase"] for row in effects()] == [
        "manager", "suite-start", "suite-end", "manager", "suite-start", "suite-end",
        "manager", "suite-start", "suite-end",
    ]
    assert {row["request"] for row in effects()[:3]} == {original.id}
    assert {row["request"] for row in effects()[3:6]} == {pending.id}
    assert {row["request"] for row in effects()[6:]} == {distinct.id}
    assert "--project" in effects()[1]["argv"] and "sample" in effects()[1]["argv"]
    assert not list(root.glob("current-version"))

def test_native_detached_bootstrap_removes_only_its_acquisition_and_retains_worker(bootstrap):
    root, _source, driver, start, effects = bootstrap
    preserved = root / "bootstrap-acquisition" / "failed-earlier"
    preserved.mkdir(parents=True)
    sentinel = preserved / "diagnostic.txt"
    sentinel.write_bytes(b"retained failed acquisition")
    with ExitStack() as stack:
        if os.name == "nt":
            observer = runpy.run_path(str(
                SOURCE / "libs/agent-procutil/tests/test_spawn_daemon_windows_integration.py"
            ))
            windows = stack.enter_context(observer["_WindowObserver"](*observer["_configure_win32"]()))
        for cycle in range(2):
            (root / "release-suite").unlink(missing_ok=True)
            detached, log = start("--project", "detached", "--detach", "--json")
            assert detached.wait(timeout=60 if cycle == 0 else 20) == 0, log.read_text(
                encoding="utf-8",
            )
            assert list(preserved.parent.iterdir()) == [preserved]
            assert sentinel.read_bytes() == b"retained failed acquisition"
            _eventually(lambda: len(effects()) == 3 * cycle + 2)
            request = core.status(root=driver)[cycle]
            assert request.state == "running" and request.exit_code is None
            retained = Path(effects()[3 * cycle]["payload"])
            assert retained.is_relative_to(driver / "bootstrap-inputs") and retained.is_dir()
            if cycle == 1:
                (root / "successful-request").write_text(request.id, encoding="utf-8")
            (root / "release-suite").touch()
            _eventually(lambda: core.status(request.id, root=driver).state == "completed")
            assert core.status(request.id, root=driver).exit_code == (23 if cycle == 0 else 0)
        assert not (root / "finalizers.jsonl").exists()
        assert [row["phase"] for row in effects()] == [
            "manager", "suite-start", "suite-end",
        ] * 2
    if os.name == "nt":
        assert windows.surfaced == {} and windows.focus_changes == {}


def test_native_ordinary_worker_drains_after_successful_caller_removes_acquisition(bootstrap):
    root, source, driver, start, effects = bootstrap
    caller, log = start("--project", "sample")
    _eventually(
        lambda: "Update request " in log.read_text(encoding="utf-8"), processes=(caller,),
        timeout=60,
    )
    _eventually(
        lambda: any(row["phase"] == "suite-start" for row in effects()), processes=(caller,),
    )
    first, = core.status(root=driver)
    retained = Path(effects()[0]["payload"])
    (root / "successful-request").write_text(first.id, encoding="utf-8")
    changed = source / "plugins" / "agent-worktrees" / "src" / "fixture_revision.py"
    changed.write_text("REVISION = 2\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(
        source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "changed engine source",
    )
    detached, detached_log = start("--project", "later-fixture", "--detach", "--json")
    assert detached.wait(timeout=60) == 0, detached_log.read_text(encoding="utf-8")
    _running, later = core.status(root=driver)
    assert later.id != first.id
    assert later.state == "pending"
    acquisition, = (root / "bootstrap-acquisition").iterdir()
    (root / "release-suite").touch()
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    assert not acquisition.exists()
    _eventually(lambda: core.status(later.id, root=driver).state == "completed")
    assert core.status(later.id, root=driver).exit_code == 23
    assert core.status(first.id, root=driver).exit_code == 0
    assert [
        json.loads(line) for line in (root / "finalizers.jsonl").read_text().splitlines()
    ] == [{"command": ["update", "--project", "sample"], "participant": False}]
    procutil = Path((root / "draining-worker-procutil").read_text())
    assert procutil.is_relative_to(retained.parent) and procutil.is_file()
    assert [row["phase"] for row in effects()] == [
        "manager", "suite-start", "suite-end",
    ] * 2
    assert {row["request"] for row in effects()[:3]} == {first.id}
    assert {row["request"] for row in effects()[3:]} == {later.id}
    updated = Path(effects()[3]["payload"])
    assert updated != retained
    marker = Path("plugins") / "agent-worktrees" / "src" / changed.name
    assert (updated.parent / marker).read_text() == "REVISION = 2\n"
    assert not (retained.parent / marker).exists()


@pytest.mark.parametrize("linked_parent", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
def test_native_installed_cli_retains_source_anchor_and_caller_hold(
    tmp_path, monkeypatch, linked_parent, automatic,
):
    import update_driver
    from worktree_manager import source_config, update_driver_cli

    root, caller, anchor, worker, queue = (tmp_path / name for name in (
        "manager", "caller", "source-anchor", "worker", "queue",
    ))
    for path in (root, caller, anchor, worker):
        path.mkdir()
    inherited = anchor
    if linked_parent:
        nested, alias = anchor / "nested", tmp_path / "alias"
        nested.mkdir()
        try:
            alias.symlink_to(nested, target_is_directory=True)
        except OSError:
            if os.name == "nt":
                pytest.skip("native Windows directory symlinks are unavailable")
            raise
        inherited = alias / ".."
    selected_anchor = inherited.resolve()
    (selected_anchor / "repository").mkdir()
    (root / "config.toml").write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    source_config.set_source(repo="repository", ref="dev", root=root)
    hold = caller / "hold.json"
    hold.write_text('{"active":true}', encoding="utf-8")
    (selected_anchor / hold.name).write_text('{"active":false}', encoding="utf-8")
    monkeypatch.chdir(caller)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setenv(SOURCE_ANCHOR_ENV, str(inherited))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", hold.name)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_UPDATE_CONTEXT", raising=False)
    admitted = update_driver_cli._source_key()
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (sys.executable, "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        update_driver, "enqueue",
        lambda spec, *, runner: core.enqueue(spec, runner=runner, root=queue),
    )
    arguments = ["--detach", "--json", *(["--auto"] if automatic else [])]
    assert update_driver_cli.cmd_update_request(arguments) == 0
    request, = core.status(root=queue)
    assert Path(request.spec.cwd) == selected_anchor
    assert Path(request.runner.cwd) == caller
    assert Path(request.spec.killswitch_path) == hold
    assert request.spec.source == admitted
    calls = []

    def execute(transaction):
        with update_driver_cli._request_environment(
            transaction.request.spec.context, cwd=transaction.request.spec.cwd,
            killswitch_path=transaction.request.spec.killswitch_path,
        ):
            assert update_driver_cli._source_key() == admitted
            calls.append(transaction.request.id)
        return 0

    monkeypatch.chdir(worker)
    assert core.drain(root=queue, executor=execute) == 0
    result = core.status(request.id, root=queue)
    assert result.state == "completed" and result.exit_code == (3 if automatic else 0)
    assert calls == ([] if automatic else [request.id])
    assert hold.read_text(encoding="utf-8") == '{"active":true}'


def test_native_inherited_anchor_selects_acquisition_instead_of_cwd_decoy(bootstrap):
    root, source, _driver, start, effects = bootstrap
    anchor = root.parent / "anchor"
    selected = anchor / "source"
    shutil.copytree(source, selected)
    marker = Path("plugins") / "agent-worktrees" / "src" / "anchor_marker.py"
    (selected / marker).write_text("SELECTED = True\n", encoding="utf-8")
    _git(selected, "add", ".")
    _git(
        selected, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "inherited anchor source",
    )
    (root / "config.toml").write_text(
        '[source]\nrepo="source"\nref="main"\n', encoding="utf-8",
    )
    (root / "release-suite").touch()
    caller, log = start("--project", "sample", source_anchor=anchor)
    assert caller.wait(timeout=60) == 23, log.read_text(encoding="utf-8")
    retained = Path(effects()[0]["payload"])
    assert (retained.parent / marker).is_file()
    assert not (source / marker).exists()
    assert Path(effects()[1]["source_repo"]) == selected


def test_native_shell_controller_runs_a_prepared_source_without_new_bootstrap_code(bootstrap):
    root, source, driver, start, effects = bootstrap
    scripts = source / "worktree-manager" / "scripts"
    sys.path.insert(0, str(scripts))
    import install_source

    snapshot = root / "prepared"
    old = snapshot / "source" / "worktree-manager"
    (old / "src").mkdir(parents=True)
    (old / "src" / "old_application.py").write_text("VALUE = 1\n", encoding="utf-8")
    prior = old / "src" / "worktree_manager"
    prior.mkdir()
    # Actual pre-anchor modules from 4e63a7ca09f8c04769e8ff4c4fd2421b44eca5d0.
    for name in ("source_config", "update_driver_cli"):
        shutil.copyfile(
            SOURCE / "worktree-manager" / "tests" / "fixtures" / "bootstrap-prior-cli" / f"{name}.py",
            prior / f"{name}.py",
        )
    (root / "config.toml").write_text('[source]\nrepo="source"\nref="main"\n', encoding="utf-8")
    (old / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n',
        encoding="utf-8",
    )
    digest = install_source.sources.assembly_hash(old)
    identity = install_source.sources.SourceIdentity(
        "worktree-manager", "1.0.0", "a" * 40, "b" * 40, digest,
        install_source.sources.runtime_version("1.0.0", "a" * 40, digest),
    )
    (old / install_source.sources.SOURCE_FILE).write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    (snapshot / install_source.MANIFEST).write_text(json.dumps({
        "schema": install_source.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "projects": {"worktree-manager": {
            "payload": "source/worktree-manager", "source": identity.document(),
        }},
    }), encoding="utf-8")
    caller, log = start("--project", "sample", install_source=snapshot, acquisition_budget="600")
    _eventually(
        lambda: "Update request " in log.read_text(encoding="utf-8"), processes=(caller,),
    )
    _eventually(
        lambda: any(row["phase"] == "suite-start" for row in effects()), processes=(caller,),
    )
    request, = core.status(root=driver)
    assert request.state == "running"
    assert effects()[0]["payload"] == str(old)
    controller = Path((root / "fixture-controller").read_text(encoding="utf-8"))
    assert controller.is_relative_to(driver / "bootstrap-inputs")
    assert controller != old and "--update-participant" in request.runner.argv
    assert "--bootstrap-attempt" in request.runner.argv
    assert str(controller / "scripts" / "runtime_install.py") in request.runner.argv
    (root / "release-suite").touch()
    assert caller.wait(timeout=20) == 23, log.read_text(encoding="utf-8")
    assert core.status(request.id, root=driver).state == "completed"
    assert [row["phase"] for row in effects()] == ["manager", "suite-start", "suite-end"]
    assert effects()[1]["acquisition_budget"] == "600.0"
    assert effects()[1]["source_repo"] == str(source)
    assert "--bootstrap-attempt" not in effects()[1]["argv"]
    assert not (old / "scripts").exists()


def test_native_auth_report_preserves_json_stdout_without_installation(bootstrap):
    _root, _source, driver, start, effects = bootstrap
    caller, log = start("--auth-report", "--json")
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    assert json.loads(log.with_suffix(".out").read_text(encoding="utf-8")) == {"reports": []}
    assert core.status(root=driver) == () and not driver.exists()
    assert effects() == [{"phase": "auth-report"}]


def test_native_request_controls_preserve_fences_and_run_explicit_recovery(bootstrap):
    root, _source, driver, start, effects = bootstrap
    old = core.enqueue(
        core.RequestSpec("interrupted-fixture"),
        runner=core.TrustedRunner((sys.executable,), str(root)), root=driver,
    )

    def interrupt(transaction):
        raise RuntimeError("original fixture interruption")

    with pytest.raises(RuntimeError, match="original fixture interruption"):
        core.drain(root=driver, executor=interrupt)
    before = core.status(old.id, root=driver)
    caller, log = start("--recovery-status", "--json")
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    assert json.loads(log.with_suffix(".out").read_text(encoding="utf-8"))["refused"]
    assert core.status(old.id, root=driver) == before
    caller, log = start("--recover-for-repair", "--json")
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    result = json.loads(log.with_suffix(".out").read_text(encoding="utf-8"))
    assert result["replayed"] is False and result["released"][0]["request_id"] == old.id
    failed, = core.status(root=driver)
    assert failed.state == "interrupted" and failed.error == before.error
    assert failed.recovery_authority == "operator_repair"
    assert failed.released_at is not None and failed.settled_at is None
    assert effects() == []


@pytest.mark.skipif(os.name == "nt", reason="source-only POSIX non-update entry")
def test_native_posix_non_update_resolves_driver_without_creating_a_queue(bootstrap):
    _root, _source, driver, start, effects = bootstrap
    caller, log = start("--indicator-state", command_name="stage-update", direct=True)
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    assert core.status(root=driver) == () and not driver.exists()
    assert [row["phase"] for row in effects()] == ["manager", "inspection"]


@pytest.mark.timeout(180)
def test_full_declared_repository_inputs_reach_immutable_retention(
    tmp_path, monkeypatch, request,
):
    scripts = SOURCE / "worktree-manager" / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import bootstrap_inputs

    roots = {
        package
        for application in (SOURCE / "worktree-manager", SOURCE / "plugins" / "agent-worktrees")
        for package in bootstrap_inputs.engine._package_roots(application)
    }
    files = set(bootstrap_inputs.engine._source_files(tuple(sorted(roots))))
    files.update(bootstrap_inputs._preparation_assets(SOURCE / "worktree-manager").values())
    input_bytes = sum(path.stat().st_size for path in files)
    request.node.user_properties.extend([
        ("input_files", len(files)),
        ("input_bytes", input_bytes),
    ])
    print(f"Declared retention inputs: files={len(files)} bytes={input_bytes}", flush=True)
    started = time.monotonic()
    retained, _shared = bootstrap_inputs.retain(SOURCE / "worktree-manager", tmp_path / "driver")
    elapsed = time.monotonic() - started
    request.node.user_properties.append(("retention_seconds", elapsed))
    print(f"Verified declared retention: seconds={elapsed:.3f}", flush=True)
    assert retained.is_relative_to(tmp_path / "driver" / "bootstrap-inputs")
    for application in ("worktree-manager", "plugins/agent-worktrees"):
        expected = SOURCE / application / "pyproject.toml"
        assert (retained.parent / application / "pyproject.toml").read_bytes() == expected.read_bytes()
