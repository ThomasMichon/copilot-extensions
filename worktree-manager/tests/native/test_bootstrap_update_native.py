"""Actual bootstrap, frozen worker and borrowed participant; finite install bodies."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv

import pytest

import agent_procutil
from update_driver import core
from worktree_manager import self_install
from worktree_manager.engine_acquisition_budget import TIMEOUT_ENV

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
if __name__ == "__main__" and "--update-participant" not in sys.argv and "update" in sys.argv:
    _execution_driver(SCRIPTS.parent)
    from agent_procutil._daemon_instance_identity import birth_identity

    root = Path(sys.argv[sys.argv.index("--root") + 1])
    (root / f"caller-{os.getpid()}.json").write_text(json.dumps({
        "pid": os.getpid(), "birth": birth_identity(os.getpid()),
    }), encoding="utf-8")
'''

SUITE = '''
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

if "--auth-report" in sys.argv or sys.argv[3] != "update":
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
        with (root / "effects.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "phase": phase, "request": request.id, "pid": os.getpid(),
                "payload": str(payload), "argv": sys.argv[1:],
                "acquisition_budget": os.environ.get(
                    "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC",
                ),
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
        return 23

    from worktree_manager import update_driver_cli

    manager = ModuleType("worktree_manager.__main__")
    manager._cmd_update = body
    sys.modules[manager.__name__] = manager
    os.environ.update(participant_environment())
    assert sys.argv[1:4] == ["-m", "worktree_manager", "update"]
    result = update_driver_cli.cmd_update_request(sys.argv[4:])
raise SystemExit(result)
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


@pytest.fixture
def bootstrap(tmp_path):
    host = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")
    if host is None:
        pytest.skip("native bootstrap shell is unavailable")
    assert os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
    adapter = self_install._runtime_installer()
    source = tmp_path / "source"
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
        "engine_client", "agent_plugin_runtime", "picker_review", "engine_posix_process",
        "engine_environment", "worktree_rows_contract", "worktree_observation_contract",
        "update_orchestration", "update_events",
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
    _git(source, "init", "-q", "-b", "main")
    _git(source, "add", ".")
    _git(
        source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "finite bootstrap source",
    )
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
        (tools / "uv.ps1").write_text(f"'{python.replace(chr(39), chr(39) * 2)}'\n", encoding="utf-8")
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
    driver = profile / ".copilot-extensions" / "update-driver"
    callers, streams = [], []

    def start(
        *arguments, install_source=None, command_name="update", acquisition_budget=None,
        direct=False,
    ):
        log = tmp_path / f"caller-{len(callers)}.log"
        stream = log.open("wb")
        streams.append(stream)
        invocation = [*command, command_name, *arguments]
        if install_source is not None or direct:
            invocation = [
                python, "-I", "-S", "-B", str(scripts / "runtime_install.py"),
                "--root", str(root),
                *(["--install-source", str(install_source)] if install_source is not None else []),
                "--", command_name, *arguments,
            ]
        launch_environment = dict(environment)
        if acquisition_budget is not None:
            launch_environment[TIMEOUT_ENV] = acquisition_budget
        process = subprocess.Popen(
            invocation, cwd=tmp_path, env=launch_environment,
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            **agent_procutil.no_window_kwargs(),
        )
        callers.append(process)
        return process, log

    def effects():
        path = root / "effects.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    try:
        yield root, source, driver, start, effects
    finally:
        (root / "release-suite").touch()
        for log in sorted(tmp_path.glob("caller-*.log")):
            print(f"{log.name}:\n{log.read_text(encoding='utf-8')}", file=sys.stderr)
        if driver.exists():
            for record in core.status(root=driver):
                print(f"{record.id}: {record.state} {record.error}", file=sys.stderr)
                if record.log_path.exists():
                    print(record.log_path.read_text(encoding="utf-8"), file=sys.stderr)
        for process in callers:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()
        assert sentinel.read_bytes() == b"retained shared staging"


@pytest.mark.parametrize("lose_caller", [False, True])
def test_native_bootstrap_coalesces_pending_work_and_retains_worker_after_caller_loss(
    bootstrap, lose_caller,
):
    root, source, driver, start, effects = bootstrap
    first, first_log = start("--project", "sample")
    # Durable admission finishes acquisition/retention; observe the next owned stage separately.
    _eventually(lambda: core.status(root=driver), processes=(first,))
    _eventually(
        lambda: any(row["phase"] == "suite-start" for row in effects()),
        processes=(first,),
    )
    original, = core.status(root=driver)
    assert original.state == "running" and original.exit_code is None
    assert original.spec.backend == "manager"
    assert [row["phase"] for row in effects()] == ["manager", "suite-start"]
    retained = Path(effects()[0]["payload"])
    assert retained.is_relative_to(driver / "bootstrap-inputs")
    assert retained != source / "worktree-manager"
    admission, = root.glob("caller-*.json")
    caller = json.loads(admission.read_text(encoding="utf-8"))

    second, second_log = start("--project", "sample")
    _eventually(lambda: len(core.status(root=driver)) == 2, processes=(first, second))
    running, pending = core.status(root=driver)
    assert running.id == original.id and running.state == "running"
    assert pending.id != original.id and pending.state == "pending"
    _eventually(
        lambda: f"Update request {pending.id};" in second_log.read_text(encoding="utf-8"),
        processes=(first, second),
    )
    equivalent, equivalent_log = start("--project", "sample")
    _eventually(
        lambda: f"Update request {pending.id};" in equivalent_log.read_text(encoding="utf-8"),
        processes=(first, second, equivalent),
    )
    assert len(core.status(root=driver)) == 2
    assert second.poll() is None
    if lose_caller:
        from agent_procutil.process_retirement import PinnedProcess

        with PinnedProcess(caller["pid"], caller["birth"]) as original_caller:
            original_caller.terminate()
            assert original_caller.exited()
        first.wait(timeout=5)
    different, different_log = start("--project", "other")
    _eventually(lambda: len(core.status(root=driver)) == 3, processes=(second, different))
    running, queued, distinct = core.status(root=driver)
    assert running.id == original.id and running.state == "running"
    assert queued.id == pending.id and distinct.state == "pending"
    assert [row["phase"] for row in effects()] == ["manager", "suite-start"]
    (root / "release-suite").touch()
    assert second.wait(timeout=20) == 23, second_log.read_text(encoding="utf-8")
    assert equivalent.wait(timeout=20) == 23, equivalent_log.read_text(encoding="utf-8")
    assert different.wait(timeout=20) == 23, different_log.read_text(encoding="utf-8")
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

    changed = source / "plugins" / "agent-worktrees" / "src" / "fixture_revision.py"
    changed.write_text("REVISION = 2\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(
        source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "changed engine source",
    )
    third, third_log = start("--project", "sample")
    assert third.wait(timeout=20) == 23, third_log.read_text(encoding="utf-8")
    assert len(core.status(root=driver)) == 4
    assert Path(effects()[9]["payload"]) != retained
    assert not (retained.parent / "plugins" / "agent-worktrees" / "src" / changed.name).exists()


def test_native_current_controller_runs_a_prepared_source_without_new_bootstrap_code(bootstrap):
    root, source, driver, start, effects = bootstrap
    scripts = source / "worktree-manager" / "scripts"
    sys.path.insert(0, str(scripts))
    import install_source

    snapshot = root / "prepared"
    old = snapshot / "source" / "worktree-manager"
    (old / "src").mkdir(parents=True)
    (old / "src" / "old_application.py").write_text("VALUE = 1\n", encoding="utf-8")
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
        lambda: any(row["phase"] == "suite-start" for row in effects()), processes=(caller,),
    )
    request, = core.status(root=driver)
    assert request.state == "running"
    assert effects()[0]["payload"] == str(old)
    controller = Path((root / "fixture-controller").read_text(encoding="utf-8"))
    assert controller.is_relative_to(driver / "bootstrap-inputs")
    assert controller != old and "--update-participant" in request.runner.argv
    assert str(controller / "scripts" / "runtime_install.py") in request.runner.argv
    (root / "release-suite").touch()
    assert caller.wait(timeout=20) == 23, log.read_text(encoding="utf-8")
    assert core.status(request.id, root=driver).state == "completed"
    assert [row["phase"] for row in effects()] == ["manager", "suite-start", "suite-end"]
    assert effects()[1]["acquisition_budget"] == "600.0"
    assert not (old / "scripts").exists()


def test_native_auth_report_closes_owned_bootstrap_without_forwarding_witness(bootstrap):
    root, _source, driver, start, effects = bootstrap
    caller, log = start("--auth-report")
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    request, = core.status(root=driver)
    assert request.state == "completed" and request.exit_code == 0
    assert [row["phase"] for row in effects()] == ["manager", "inspection"]
    assert effects()[1]["witness"] is False


@pytest.mark.skipif(os.name == "nt", reason="source-only POSIX non-update entry")
def test_native_posix_non_update_resolves_driver_without_creating_a_queue(bootstrap):
    _root, _source, driver, start, effects = bootstrap
    caller, log = start("--indicator-state", command_name="stage-update", direct=True)
    assert caller.wait(timeout=20) == 0, log.read_text(encoding="utf-8")
    assert core.status(root=driver) == () and not driver.exists()
    assert [row["phase"] for row in effects()] == ["manager", "inspection"]


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
    files = bootstrap_inputs.engine._source_files(tuple(sorted(roots)))
    request.node.user_properties.extend([
        ("input_files", len(files)),
        ("input_bytes", sum(path.stat().st_size for path in files)),
    ])
    started = time.monotonic()
    retained, _shared = bootstrap_inputs.retain(SOURCE / "worktree-manager", tmp_path / "driver")
    request.node.user_properties.append(("retention_seconds", time.monotonic() - started))
    assert retained.is_relative_to(tmp_path / "driver" / "bootstrap-inputs")
    for application in ("worktree-manager", "plugins/agent-worktrees"):
        expected = SOURCE / application / "pyproject.toml"
        assert (retained.parent / application / "pyproject.toml").read_bytes() == expected.read_bytes()
