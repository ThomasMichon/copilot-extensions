"""Production protocol in a synthetic, explicitly authorized disposable fixture.

Copies source into an owned immutable fixture directory; installs no packages,
updates no host runtime and never registers outside a hosted Windows CI runner.
"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import venv
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_broker_fixture import (
    admit, close_job, collect_generations, fixture_environment, make_job,
    mcp_candidate_seed, prepare_bootstrap,
)


def imports(package_root: Path) -> None:
    sys.path.insert(0, str(package_root))


def controller_root(root: Path) -> Path:
    return root.with_name(root.name + "-controller")


def adopt_action(package_root: Path, root: Path, generation: str) -> int:
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    from update_driver.windows_broker import main

    return main([
        "--runtime-root", str(root), "--slot", str(root / "versions" / generation),
        "--execution-root", str(root / "execution"),
    ])


def manager_adopt_action(package_root: Path, root: Path, generation: str) -> int:
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worktree-manager" / "scripts"))
    from runtime_install import adopt_broker

    adopt_broker(root, root / "versions" / generation)
    print(json.dumps({"manager_adapter_adopted": True}))
    return 0


def worktrees_adopt_action(package_root: Path, root: Path, generation: str) -> int:
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    scripts = Path(__file__).resolve().parents[1] / "plugins" / "agent-worktrees" / "scripts"
    sys.path.insert(0, str(scripts))
    from runtime_install_driver import adopt_broker

    adopt_broker(root, root / "versions" / generation)
    print(json.dumps({"worktrees_adapter_adopted": True}))
    return 0


def bridge_adopt_action(package_root: Path, root: Path, generation: str) -> int:
    return native_adopt_action(package_root, root, generation, plugin="agent-bridge")


def codespaces_adopt_action(package_root: Path, root: Path, generation: str) -> int:
    return native_adopt_action(package_root, root, generation, plugin="agent-codespaces")


NATIVE_PLUGINS = frozenset({
    "agent-bridge", "agent-codespaces", "agent-dispatch", "agent-pull-requests", "agent-vault",
    "agent-mcp",
    "agent-index",
})


def native_adoption_program(root: Path, generation: str, *, plugin: str) -> tuple[Path, str]:
    """Render inert fixture code at the copied helpers' lexical script root."""
    if plugin not in NATIVE_PLUGINS:
        raise ValueError("fixture native plugin refused")
    scripts = Path(__file__).resolve().parents[1] / "plugins" / plugin / "scripts"
    source = (scripts / ("init.ps1" if plugin == "agent-mcp" else "install.ps1")).read_text(encoding="utf-8")
    functions = []
    for name in (
        "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete", "Invoke-RuntimeBrokerAdoption",
    ):
        match = re.search(r"(?ms)^function " + name + r" \{.*?^\}", source)
        if match is None:
            raise RuntimeError(f"fixture {plugin} adapter is unavailable")
        functions.append(match.group())
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    helper = controller_root(root) / ".runtime-bootstrap" / "versioned_runtime.py"
    native = scripts / "versioned_runtime_native.ps1"
    script = helper.parent / f"{plugin}-adopt.ps1"
    version, seed = (
        mcp_candidate_seed(root, generation, sys.executable)
        if plugin == "agent-mcp" else (generation, "")
    )
    program = (
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
        + f". {quote(native)}\n"
        + "$VersionedRuntime=$true\n$NoService=$false\n"
        + f"$InstallDir={quote(root)}\n$SrcVersion={quote(version)}\n"
        + f"$VenvDir={quote(root / 'versions' / generation)}\n$LinkDir=$VenvDir\n"
        + "$VenvPython=Join-Path $VenvDir 'Scripts\\python.exe'\n"
        + f"$ScriptDir={quote(helper.parent)}\n"
        + "function Assert-SourceRuntime {}\n"
        + "function Test-SourceRuntimeUnchanged {}\n"
        + f"function Get-BootstrapPython {{ return {quote(sys.executable)} }}\n"
        + "function Get-PayloadHash { return ('a'*64) }\n"
        + "\n".join(functions)
        + "\ntry {\n[void](Invoke-VersionedSlotClean"
        + (" -PayloadHash ('a'*64)" if plugin == "agent-bridge" else "") + ")\n"
        + seed
        + "if (-not (Invoke-VersionedMarkComplete -PayloadHash ('a'*64))) { throw 'Fixture completion failed' }\n"
        + "Invoke-RuntimeBrokerAdoption\n"
        + "} finally {\n"
        + "if (Test-Path -LiteralPath 'Function:\\Stop-RuntimeConstruction') { Stop-RuntimeConstruction }\n"
        + "}\n"
    )
    return script, program


def native_adopt_action(package_root: Path, root: Path, generation: str, *, plugin: str) -> int:
    admit(root, True)
    if generation not in {"first", "replacement"} or plugin not in NATIVE_PLUGINS:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    from agent_procutil.owned_process import run_owned

    script, program = native_adoption_program(root, generation, plugin=plugin)
    script.write_text(program, encoding="utf-8")
    shell = shutil.which("powershell.exe")
    if shell is None:
        raise RuntimeError(f"fixture {plugin} adapter requires native PowerShell")
    result = run_owned([
        shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
    ], timeout=60)
    if result.returncode:
        raise RuntimeError(f"fixture {plugin} adoption failed: " + result.stdout + result.stderr)
    print(json.dumps({plugin + "_adapter_adopted": True}))
    return 0


def native_remove_action(package_root: Path, root: Path, generation: str, *, plugin: str) -> int:
    admit(root, True)
    if generation not in {"first", "replacement"} or plugin not in NATIVE_PLUGINS:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    from types import SimpleNamespace

    scripts = Path(__file__).resolve().parents[1] / "plugins" / plugin / "scripts"
    sys.path.insert(0, str(scripts))
    from installer_driver import execute

    shell = shutil.which("powershell.exe")
    if shell is None:
        raise RuntimeError("fixture removal requires native PowerShell")
    return execute(SimpleNamespace(
        root=root, payload=scripts.parent, plugin=plugin,
        action="broker-remove" if plugin == "agent-mcp" else "uninstall",
        options=[], remove_broker=True, check_native=True, shell=Path(shell),
    ))


@contextmanager
def manager_profile(root: Path):
    admit(root, True)
    previous = {key: os.environ.get(key) for key in ("HOME", "USERPROFILE")}
    try:
        os.environ.update({key: str(root.with_name(root.name + "-profile")) for key in previous})
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def adopt_generation(
    package_root: Path, root: Path, generation: str, *,
    manager: bool = False, worktrees: bool = False, bridge: bool = False, codespaces: bool = False,
    native_plugin: str | None = None,
):
    """Exercise the actual default executor and its native owned process tree."""
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    if native_plugin is not None and (
        native_plugin not in NATIVE_PLUGINS or manager or worktrees or bridge or codespaces
    ):
        raise ValueError("fixture native plugin refused")
    imports(package_root)
    from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
    from agent_procutil.windows_broker_installation import load

    if manager or worktrees or bridge or codespaces or native_plugin is not None:
        with manager_profile(root):
            return _adopt_manager_generation(
                package_root, root, generation, worktrees=worktrees, bridge=bridge, codespaces=codespaces,
                native_plugin=native_plugin,
            )
    execution = root / "execution"
    slot = root / "versions" / generation
    request = enqueue(
        RequestSpec("windows-broker-fixture", source=generation, cwd=str(root)),
        runner=TrustedRunner((
            str(slot / "Scripts" / "python.exe"), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(root), "--slot", str(slot),
            "--execution-root", str(execution),
        ), str(root)),
        root=execution,
    )
    drain(root=execution)
    completed = status(request.id, root=execution)
    if completed.state != "completed" or completed.exit_code != 0:
        raise RuntimeError("fixture driver-owned adoption failed: " + completed.log_path.read_text())
    return load(root)


def _adopt_manager_generation(
    package_root: Path, root: Path, generation: str, *,
    worktrees: bool = False, bridge: bool = False, codespaces: bool = False, remove: bool = False,
    native_plugin: str | None = None,
):
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    if native_plugin is not None and (
        native_plugin not in NATIVE_PLUGINS or worktrees or bridge or codespaces
    ):
        raise ValueError("fixture native plugin refused")
    from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
    from agent_procutil.windows_broker_installation import load

    request = enqueue(
        RequestSpec("engine" if remove else "windows-broker-manager-fixture", source=generation, cwd=str(root)),
        runner=TrustedRunner((
            sys.executable, "-I", "-S", str(Path(__file__).resolve()),
            ("native-remove" if remove else "native-adopt") if native_plugin is not None else
            ("codespaces-remove" if codespaces else "bridge-remove") if remove else
            "codespaces-adopt" if codespaces else "bridge-adopt" if bridge else
            "worktrees-adopt" if worktrees else "manager-adopt",
            str(package_root), str(root), generation,
            *((native_plugin,) if native_plugin is not None else ()),
        ), str(root)),
    )
    try:
        drain()
    except Exception as error:
        completed = status(request.id)
        detail = completed.log_path.read_text(encoding="utf-8", errors="replace")[-16384:]
        raise RuntimeError(
            "fixture installer transaction failed (" + type(error).__name__ + "): " + detail,
        ) from error
    completed = status(request.id)
    if completed.state != "completed" or completed.exit_code != 0:
        raise RuntimeError("fixture Manager adoption failed: " + completed.log_path.read_text())
    if remove:
        return None
    return load(root)


def prepare_action(package_root: Path, root: Path, generation: str, runtime) -> Path:
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    slot = root / "versions" / generation
    venv.EnvBuilder(with_pip=False).create(slot)
    packages = slot / "Lib" / "site-packages"
    for package in package_root.iterdir():
        if package.is_dir():
            shutil.copytree(package, packages / package.name)
    shutil.copytree(
        controller_root(root) / ".runtime-bootstrap", slot / ".runtime-bootstrap",
    )
    runtime.mark_complete(root, generation, payload_hash="a" * 64)
    return packages


def caller(package_root: Path, root: Path) -> int:
    admit(root, True)
    imports(package_root)
    from agent_procutil import spawn_daemon

    if sys.stdin.readline() != "launch\n":
        return 2
    child = spawn_daemon(
        [
            str(Path(sys.base_prefix) / "python.exe"), "-I", str(root / "resident.py"),
            "argument with spaces", "", "\u65e5\n&",
        ],
        cwd=str(root), env=fixture_environment(dict(os.environ), control=False),
        console_children=True,
        log_path=str(root / "resident.log"), stderr_log_path=str(root / "resident-error.log"),
    )
    offer_path = root / "caller-offer.json"
    temporary = root / "caller-offer.tmp"
    temporary.write_text(json.dumps(child.offer()), encoding="utf-8")
    temporary.replace(offer_path)
    # The parent duplicates actual child custody before closing every caller Job.
    sys.stdin.readline()
    child.close()
    return 0


def worker_action(package_root: Path, root: Path, action: str) -> int:
    admit(root, True)
    imports(package_root)
    from update_driver import borrowed_custody, drain, ensure_worker

    queue = root / "worker-queue"
    if action == "drain-worker":
        return drain(root=queue)
    if action == "submit-worker":
        if sys.stdin.readline() != "launch\n":
            return 2
        child = ensure_worker(
            [
                str(Path(sys.base_prefix) / "python.exe"), "-I", "-S",
                str(Path(__file__).resolve()), "drain-worker", str(package_root), str(root),
            ],
            root=queue, cwd=root,
            env=fixture_environment(dict(os.environ), control=True),
        )
        temporary = root / "worker-offer.tmp"
        temporary.write_text(json.dumps(child.offer()), encoding="utf-8")
        temporary.replace(root / "worker-offer.json")
        sys.stdin.readline()
        child.close()
        return 0
    if action != "worker-product":
        raise ValueError("fixture worker action refused")
    scope = os.environ.get("BROKER_FIXTURE_SCOPE")
    if scope not in {"first", "second"}:
        raise RuntimeError("fixture worker context refused")
    with borrowed_custody(root=queue) as custody:
        custody.assert_owned()
        effects = root / "worker-effects.jsonl"
        record = {"scope": scope, "optional": os.environ.get("BROKER_FIXTURE_OPTIONAL")}
        with effects.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({**record, "phase": "start"}) + "\n")
        (root / ("worker-started-" + scope)).write_text("started", encoding="ascii")
        deadline = time.monotonic() + 20
        while not (root / "worker-release").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture installer release gate")
            time.sleep(.02)
        custody.assert_owned()
        with effects.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({**record, "phase": "end"}) + "\n")
    return 0


def inspect_worker(package_root: Path, root: Path, pu, callers, children, jobs) -> dict:
    admit(root, True)
    from agent_procutil.daemon_broker import _outside_job
    from agent_procutil.windows_broker_native import NativeChild, _duplicate
    from update_driver import RequestSpec, TrustedRunner, enqueue, status, wait

    queue = root / "worker-queue"
    runner = TrustedRunner(
        (
            str(Path(sys.base_prefix) / "python.exe"), "-I", "-S",
            str(Path(__file__).resolve()), "worker-product", str(package_root), str(root),
        ),
        str(root), timeout=25,
        context_keys=("BROKER_FIXTURE_SCOPE", "BROKER_FIXTURE_OPTIONAL"),
    )
    first_spec = RequestSpec(
        "worker-fixture", project="first",
        context=(("BROKER_FIXTURE_SCOPE", "first"), ("BROKER_FIXTURE_OPTIONAL", "first-only")),
    )
    first = enqueue(first_spec, runner=runner, root=queue)
    if enqueue(first_spec, runner=runner, root=queue).id != first.id:
        raise RuntimeError("fixture pending worker request did not coalesce")
    second = enqueue(
        RequestSpec("worker-fixture", project="second", context=(("BROKER_FIXTURE_SCOPE", "second"),)),
        runner=runner, root=queue,
    )
    process, outer = pu.spawn_sync_in_kill_on_close_job(
        [
            str(Path(sys.base_prefix) / "python.exe"), "-I", "-S",
            str(Path(__file__).resolve()), "submit-worker", str(package_root), str(root),
        ],
        require_job=True, allow_breakaway=False,
        env=fixture_environment(dict(os.environ), control=True), cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=0x08000000,
    )
    callers.append(process)
    if outer is None:
        raise RuntimeError("fixture worker caller Job unavailable")
    jobs.append(outer)
    api = pu._kernel32()
    pu._configure_job_api(api)
    inner = make_job(pu, jobs)
    if not api.AssignProcessToJobObject(inner.handle, int(process._handle)):
        raise ctypes.WinError(ctypes.get_last_error())
    process.stdin.write("launch\n")
    process.stdin.flush()
    offer_path = root / "worker-offer.json"
    deadline = time.monotonic() + 40
    while not offer_path.exists():
        if process.poll() is not None:
            raise RuntimeError("fixture ensure_worker failed: " + process.stderr.read())
        if time.monotonic() >= deadline:
            raise TimeoutError("fixture worker custody deadline")
        time.sleep(.025)
    offer = json.loads(offer_path.read_text(encoding="utf-8"))
    handle = _duplicate(int(process._handle), offer["source_handle"])
    child = NativeChild.from_handle(handle, offer["pid"], ["synthetic-update-worker"])
    children.append(child)
    if not _outside_job(child):
        raise RuntimeError("fixture update worker still belongs to a Job")
    deadline = time.monotonic() + 10
    while not (root / "worker-started-first").exists():
        if child.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError("fixture worker did not reach its gated product")
        time.sleep(.025)
    if (
        status(first.id, root=queue).state != "running"
        or status(second.id, root=queue).state != "pending"
        or (root / "worker-started-second").exists()
    ):
        raise RuntimeError("fixture startup waited for or overlapped installer execution")
    close_job(pu, inner)
    close_job(pu, outer)
    process.wait(timeout=5)
    if child.poll() is not None:
        raise RuntimeError("fixture update worker died with caller Jobs")
    if enqueue(first_spec, runner=runner, root=queue, coalesce_running=True).id != first.id:
        raise RuntimeError("fixture active worker request did not coalesce")
    (root / "worker-release").write_text("continue", encoding="ascii")
    for request in (first, second):
        completed = wait(request.id, root=queue, timeout=20)
        if completed.state != "completed" or completed.exit_code != 0:
            raise RuntimeError("fixture distinct worker scope failed: " + completed.log_path.read_text())
    if child.wait(timeout=10) != 0:
        raise RuntimeError("fixture finite update worker did not finish successfully")
    child.close()
    observed = [
        json.loads(line) for line in (root / "worker-effects.jsonl").read_text().splitlines()
    ]
    expected = [
        {"scope": scope, "optional": "first-only" if scope == "first" else None, "phase": phase}
        for scope in ("first", "second") for phase in ("start", "end")
    ]
    if observed != expected or len(status(root=queue)) != 2:
        raise RuntimeError("fixture worker scope, coalescing or transaction order mismatch")
    return {
        "actual_ensure_worker": True, "startup_precedes_gated_installer": True,
        "all_caller_jobs_closed": True, "worker_survived": True,
        "pending_and_active_coalescing": True, "distinct_scopes_completed": True,
        "exact_context_and_unsets": True, "no_transaction_overlap": True,
        "finite_worker_exit": True, "synthetic_products_only": True,
    }


def crash_publication(package_root: Path, root: Path, desired: str, previous: str, phase: str) -> int:
    admit(root, True)
    if phase not in {"task", "metadata"}:
        raise ValueError("fixture publication phase refused")
    imports(package_root)
    from agent_procutil import windows_task_broker as adapter

    original = adapter._publish

    def interrupted(config):
        if phase == "metadata":
            original(config)
        # Deliberately bypass Python cleanup; only the owning CI fixture may do so.
        os._exit(73)

    adapter._publish = interrupted
    adapter.reconcile(
        adapter._registration(json.loads(desired)),
        previous=adapter._registration(json.loads(previous)),
    )
    raise RuntimeError("fixture publication interruption was not reached")


def interrupt_publication(
    package_root: Path, root: Path, desired: dict, previous: dict, phase: str,
) -> int:
    admit(root, True)
    imports(package_root)
    import agent_procutil as pu
    from agent_procutil._windows_caller_owned import _retire_job

    process, owner = pu.spawn_sync_in_kill_on_close_job(
        [
            str(Path(sys.base_prefix) / "python.exe"), "-I", str(Path(__file__).resolve()),
            "crash-publication", str(package_root), str(root),
            json.dumps(desired), json.dumps(previous), phase,
        ],
        require_job=True, allow_breakaway=False, cwd=root,
        env=fixture_environment(dict(os.environ), control=True),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=0x08000000,
    )
    try:
        stdout, stderr = process.communicate(timeout=60)
        if len(stdout) > 4096 or len(stderr) > 4096:
            raise RuntimeError("fixture interruption output exceeded its budget")
        return process.returncode
    finally:
        _retire_job(owner, 5)
        process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            stream.close()


def negative_topology(root, pu, processes, children, jobs):
    from agent_procutil import DaemonSpawnError
    from agent_procutil.daemon_broker import _kernel32 as process_api
    from agent_procutil.windows_broker_native import NativeChild, _duplicate
    from zdd.private_windows_native import native

    outer = make_job(pu, jobs)
    inners = [make_job(pu, jobs), make_job(pu, jobs)]
    controls = []
    api = pu._kernel32()
    for inner in inners:
        process = subprocess.Popen(
            [str(Path(sys.base_prefix) / "python.exe"), "-I", "-c", "import time;time.sleep(30)"],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, creationflags=0x08000000 | 4,
        )
        processes.append(process)
        controls.append(process)
        for owner in (outer, inner):
            if not api.AssignProcessToJobObject(owner.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
    for process, same, other in (
        (controls[0], inners[0], inners[1]), (controls[1], inners[1], inners[0]),
    ):
        for owner, expected in ((outer, True), (same, True), (other, False)):
            member = ctypes.c_int32()
            if not process_api().IsProcessInJob(
                int(process._handle), owner.handle, ctypes.byref(member),
            ) or bool(member.value) is not expected:
                raise RuntimeError("negative fixture topology not established")
    candidate = controls[1]
    handle = _duplicate(native().k.GetCurrentProcess(), int(candidate._handle))
    child = NativeChild.from_handle(handle, candidate.pid, ["shared-outer-negative-control"])
    children.append(child)
    try:
        child.offer()
    except DaemonSpawnError:
        pass
    else:
        raise RuntimeError("shared-outer candidate received durable-success offer")
    close_job(pu, outer)
    for process in controls:
        process.wait(timeout=5)
    for inner in inners:
        inner.terminate_and_wait(5)
    child.close()
    return {
        "shared_outer": True, "different_immediate_jobs": True,
        "durable_success_refused": True, "control_died_with_outer": True,
    }


def inspect(root: Path) -> dict[str, object]:
    admit(root, True)
    source = Path(__file__).resolve().parents[1]
    controller = controller_root(root) / "site-packages"
    controller.mkdir(parents=True)
    for library, package in (
        ("agent-procutil", "agent_procutil"), ("zdd", "zdd"),
        ("update-driver", "update_driver"), ("single-instance-lease", "single_instance_lease"),
    ):
        shutil.copytree(
            source / "libs" / library / "src" / package, controller / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    bootstrap = controller_root(root) / ".runtime-bootstrap"
    prepare_bootstrap(bootstrap)
    runtime_source = bootstrap / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("fixture_runtime_gc", runtime_source)
    if spec is None or spec.loader is None:
        raise RuntimeError("fixture canonical runtime collector unavailable")
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    exec(compile(runtime_source.read_bytes(), str(runtime_source), "exec"), runtime.__dict__)
    imports(controller)
    packages = prepare_action(controller, root, "first", runtime)
    import agent_procutil as pu
    from agent_procutil import windows_task_broker as task_adapter
    from agent_procutil.daemon_broker import _outside_job
    from agent_procutil.windows_broker_native import NativeChild, _duplicate
    from agent_procutil.windows_task_broker import (
        Registration,
        reconcile,
        task_operation,
    )
    from zdd.private_protocol import Deadline
    from zdd.private_windows_native import native

    from agent_procutil.windows_broker_installation import registration

    config = registration(root, root / "versions" / "first")
    (root / "resident.py").write_text(
        "import ctypes,json,os,sys,time\n"
        "k=ctypes.WinDLL('kernel32');u=ctypes.WinDLL('user32')\n"
        "k.GetConsoleWindow.restype=ctypes.c_void_p\n"
        "u.IsWindowVisible.argtypes=[ctypes.c_void_p]\n"
        "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
        "'value':os.environ.get('BROKER_FIXTURE_VALUE'),"
        "'headless':not bool(u.IsWindowVisible(k.GetConsoleWindow()))}),flush=True)\n"
        "print('fixture-error',file=sys.stderr,flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    result: dict[str, object] = {
        "scope": "Production native protocol; synthetic disposable registration, not deployment",
        "fixture_controller_pid": os.getpid(),
        "supported": False, "cleanup_confirmed": False, "cycles": [],
    }
    callers = []
    children = []
    jobs = []
    adopted = False
    desktop = None
    try:
        from windows_broker_desktop import DesktopWitness

        desktop = DesktopWitness(root)
        result["desktop_control"] = desktop.qualify(pu)
        desktop.start()
        config = adopt_generation(controller, root, "first", codespaces=True)
        adopted = True
        config = adopt_generation(controller, root, "first", bridge=True)
        config = adopt_generation(controller, root, "first", worktrees=True)
        config = adopt_generation(controller, root, "first", manager=True)
        for plugin in ("agent-dispatch", "agent-pull-requests", "agent-vault", "agent-mcp", "agent-index"):
            config = adopt_generation(controller, root, "first", native_plugin=plugin)
            result[plugin + "_installer_adoption"] = True
        config = adopt_generation(controller, root, "first")
        result["driver_owned_adoption"] = True
        result["canonical_installed_action"] = True
        result["manager_installer_adoption"] = True
        result["worktrees_installer_adoption"] = True
        result["bridge_installer_adoption"] = True
        result["codespaces_installer_adoption"] = True
        first_status = task_operation("status", config, Deadline(10))
        if first_status.get("configured") is not True:
            raise RuntimeError("fixture adoption was not configured")
        result["negative_topology"] = negative_topology(root, pu, callers, children, jobs)
        from retirement_custody_acceptance import qualify as qualify_retirement

        result["retirement_started"] = True
        result["retirement"] = qualify_retirement(root, packages, pu)
        for cycle in range(2):
            if cycle == 1:
                previous = config
                invalid_entry = root / "invalid-runtime" / "agent_procutil" / "windows_broker_entry.py"
                invalid_entry.parent.mkdir(parents=True)
                invalid_entry.write_text(
                    "raise RuntimeError('synthetic runtime health refusal')\n", encoding="utf-8",
                )
                invalid = Registration(
                    config.installation, config.root, config.pythonw, str(invalid_entry),
                    config.principal,
                )
                try:
                    reconcile(invalid, previous=config)
                except pu.DaemonSpawnError as error:
                    if "runtime health qualification failed" not in str(error):
                        raise
                else:
                    raise RuntimeError("fixture unhealthy action was adopted")
                if (
                    task_operation("status", config, Deadline(10)).get("configured") is not True
                    or json.loads(config.config_path.read_text(encoding="utf-8")) != config.frame()
                ):
                    raise RuntimeError("fixture unhealthy action changed active adoption")
                prepare_action(controller, root, "replacement", runtime)
                config = adopt_generation(controller, root, "replacement")
                if task_operation("status", config, Deadline(10)).get("configured") is not True:
                    raise RuntimeError("fixture replacement registration refused")
                original_publish = task_adapter._publish

                def fail_publication(_config):
                    raise OSError("synthetic publication refusal")

                task_adapter._publish = fail_publication
                try:
                    try:
                        reconcile(previous, previous=config)
                    except OSError as error:
                        if str(error) != "synthetic publication refusal":
                            raise
                    else:
                        raise RuntimeError("fixture publication failure was not observed")
                finally:
                    task_adapter._publish = original_publish
                if (
                    task_operation("status", config, Deadline(10)).get("configured") is not True
                    or json.loads(config.config_path.read_text(encoding="utf-8")) != config.frame()
                ):
                    raise RuntimeError("fixture failed-update rollback not confirmed")
                recovery_controls = []

                for phase in ("task", "metadata"):
                    interrupted = interrupt_publication(
                        controller, root, previous.frame(), config.frame(), phase,
                    )
                    if interrupted != 73 or not config.pending_path.is_file():
                        raise RuntimeError("fixture publication interruption not confirmed")
                    if runtime._leases.read_runtime_references(root).get(config.owner) != [
                        "first", "replacement",
                    ]:
                        raise RuntimeError("fixture crashed installer lost runtime references")
                    control = "gc-control-" + phase
                    (root / "versions" / control).mkdir()
                    collect_generations(
                        runtime, root, retired=(control,), retained=("first", "replacement"),
                    )
                    try:
                        task_adapter.load(config.config_path, config.installation)
                    except pu.DaemonSpawnError as error:
                        if "installer recovery required" not in str(error):
                            raise
                    else:
                        raise RuntimeError("fixture interrupted publication admitted a launch")
                    if not task_adapter.recover(config):
                        raise RuntimeError("fixture interrupted publication was not recovered")
                    if (
                        config.pending_path.exists()
                        or task_operation("status", config, Deadline(10)).get("accepting") is not True
                        or json.loads(config.config_path.read_text(encoding="utf-8")) != config.frame()
                    ):
                        raise RuntimeError("fixture crash recovery did not restore original adoption")
                    recovery_controls.append(phase)
                result["publication_crash_recovery"] = recovery_controls
                if (
                    runtime._leases.read_runtime_references(root).get(config.owner) != ["replacement"]
                ):
                    raise RuntimeError("fixture old action generation was not retired after recovery")
                collect_generations(
                    runtime, root, retired=("first",), retained=("replacement",),
                )
                if packages.exists():
                    raise RuntimeError("fixture old action package content survived collection")
                result["crash_surviving_generation_retention"] = True
                result["replaced_action_removed_before_second_cycle"] = True
                task_operation("stop", config, Deadline(15))
                if task_operation("status", config, Deadline(10)).get("accepting") is not False:
                    raise RuntimeError("fixture stop still accepts work")
                config = adopt_generation(controller, root, "replacement")
                result["lifecycle"] = {
                    "first_and_repeat_adoption": True, "update": True,
                    "unhealthy_action_refused": True, "failed_publication_rollback": True,
                    "stop_and_readopt": True,
                }
            offer_path = root / "caller-offer.json"
            offer_path.unlink(missing_ok=True)
            env = fixture_environment(dict(os.environ), control=True)
            env["BROKER_FIXTURE_VALUE"] = "live-only-synthetic\n&"
            caller_packages = Path(config.entrypoint).parent.parent
            process, outer = pu.spawn_sync_in_kill_on_close_job(
                [
                    str(Path(sys.base_prefix) / "python.exe"), "-I", "-S",
                    str(Path(__file__).resolve()), "caller", str(caller_packages), str(root),
                ],
                require_job=True, allow_breakaway=False, env=env, cwd=root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=0x08000000,
            )
            callers.append(process)
            if outer is None:
                raise RuntimeError("fixture caller cleanup Job unavailable")
            jobs.append(outer)
            api = pu._kernel32()
            pu._configure_job_api(api)
            inner_owner = make_job(pu, jobs)
            if not api.AssignProcessToJobObject(inner_owner.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            process.stdin.write("launch\n")
            process.stdin.flush()
            deadline = time.monotonic() + 40
            while not offer_path.exists():
                if process.poll() is not None:
                    raise RuntimeError(
                        "fixture caller failed before custody transfer: " + process.stderr.read(),
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("fixture child custody deadline")
                time.sleep(.025)
            offer = json.loads(offer_path.read_text(encoding="utf-8"))
            handle = _duplicate(int(process._handle), offer["source_handle"])
            child = NativeChild.from_handle(handle, offer["pid"], ["synthetic-resident"])
            children.append(child)
            actual = native().identity(handle, child.pid)
            if (
                native().k.GetProcessId(handle) != child.pid
                or actual.start_time != offer["birth"] or not _outside_job(child)
            ):
                raise RuntimeError("fixture actual child identity or lifetime refused")
            close_job(pu, inner_owner)
            close_job(pu, outer)
            process.wait(timeout=5)
            for _ in range(2):
                try:
                    child.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    continue
                raise RuntimeError("resident died with fixture caller cleanup")
            observations = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                observations = [
                    line for line in (root / "resident.log").read_text(encoding="utf-8").splitlines()
                    if line.startswith('{"argv":')
                ]
                if len(observations) >= cycle + 1:
                    break
                time.sleep(.025)
            observed = json.loads(observations[cycle])
            if observed != {
                "argv": ["argument with spaces", "", "\u65e5\n&"],
                "cwd": str(root), "value": "live-only-synthetic\n&", "headless": True,
            }:
                raise RuntimeError("fixture exact resident contract mismatch")
            if "fixture-error" not in (root / "resident-error.log").read_text(encoding="utf-8"):
                raise RuntimeError("fixture stderr destination mismatch")
            child.retire(Deadline(5))
            child.close()
            result["cycles"].append({
                "cycle": cycle + 1, "actual_child_pid": child.pid,
                "default_daemon_entrypoint": True,
                "installed_module_discovery": True,
                "finite_broker_exit_proved": True, "all_caller_jobs_closed": True,
                "exact_contract": True, "actual_child_cleanup_confirmed": True,
            })
        result["update_worker_started"] = True
        result["update_worker"] = inspect_worker(
            Path(config.entrypoint).parent.parent, root, pu, callers, children, jobs,
        )
        lifecycles = {}
        for plugin in ("agent-dispatch", "agent-pull-requests", "agent-vault", "agent-mcp", "agent-index"):
            manifest = root / "deploy-manifest.json"
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump({
                    "schema_version": 3, "service": plugin,
                    "source": {"plugin": plugin, "kind": "local",
                               "path": str(source / "plugins" / plugin)},
                }, stream)
            with manager_profile(root):
                _adopt_manager_generation(controller, root, "replacement", native_plugin=plugin, remove=True)
            manifest.unlink()
            config = adopt_generation(controller, root, "replacement", native_plugin=plugin)
            if task_operation("status", config, Deadline(10)).get("configured") is not True:
                raise RuntimeError(f"fixture {plugin} first adoption after owned removal failed")
            lifecycles[plugin] = {"owned_removal": True, "first_adoption_after_removal": True}
        result["native_consumer_lifecycles"] = lifecycles
        with (root / "deploy-manifest.json").open("x", encoding="utf-8") as stream:
            json.dump({
                "schema_version": 3, "service": "agent-codespaces",
                "source": {"plugin": "agent-codespaces", "kind": "local",
                           "path": str(source / "plugins" / "agent-codespaces")},
            }, stream)
        with manager_profile(root):
            _adopt_manager_generation(controller, root, "first", codespaces=True, remove=True)
        result["codespaces_installer_removal"] = True
        from windows_broker_cell_acceptance import qualify as qualify_cell

        result["cell_started"] = True
        result["cell"] = qualify_cell(root, controller, runtime, pu, callers, children, jobs)
        result["supported"] = True
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        result["failure_type"] = type(error).__name__
        result["failure"] = str(error)
    finally:
        cleanup = (
            (not result.get("update_worker_started") or "update_worker" in result)
            and (not result.get("cell_started") or "cell" in result)
            and (not result.get("retirement_started") or "retirement" in result)
        )
        for child in children:
            if child._handle is not None:
                try:
                    child.retire(Deadline(5))
                    child.close()
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    cleanup = False
        for job in reversed(jobs):
            if job.handle is not None:
                try:
                    job.terminate_and_wait(5)
                except (OSError, RuntimeError):
                    cleanup = False
        for process in callers:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup = False
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if adopted:
            try:
                task_adapter.uninstall(config)
                if runtime._leases.read_runtime_references(root).get(config.owner):
                    raise RuntimeError("fixture uninstall retained settled broker generations")
            except (OSError, RuntimeError, subprocess.SubprocessError):
                cleanup = False
        result["cleanup_confirmed"] = cleanup and adopted
        if desktop is not None and desktop.thread is not None:
            try:
                result["desktop"] = desktop.finish()
            except RuntimeError as error:
                result["supported"] = False
                result["desktop_failure"] = str(error)
        (root / "protocol-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8",
        )
    return result


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 5 and args[0] in {"native-adopt", "native-remove"}:
        action = native_remove_action if args[0] == "native-remove" else native_adopt_action
        return action(Path(args[1]), Path(args[2]), args[3], plugin=args[4])
    if len(args) == 3 and args[0] in {"submit-worker", "drain-worker", "worker-product"}:
        return worker_action(Path(args[1]), Path(args[2]), args[0])
    if len(args) == 4 and args[0] == "adopt":
        return adopt_action(Path(args[1]), Path(args[2]), args[3])
    if len(args) == 4 and args[0] == "manager-adopt":
        return manager_adopt_action(Path(args[1]), Path(args[2]), args[3])
    if len(args) == 4 and args[0] == "worktrees-adopt":
        return worktrees_adopt_action(Path(args[1]), Path(args[2]), args[3])
    if len(args) == 4 and args[0] == "bridge-adopt":
        return bridge_adopt_action(Path(args[1]), Path(args[2]), args[3])
    if len(args) == 4 and args[0] == "codespaces-adopt":
        return codespaces_adopt_action(Path(args[1]), Path(args[2]), args[3])
    if len(args) == 4 and args[0] == "bridge-remove":
        return native_remove_action(Path(args[1]), Path(args[2]), args[3], plugin="agent-bridge")
    if len(args) == 4 and args[0] == "codespaces-remove":
        return native_remove_action(Path(args[1]), Path(args[2]), args[3], plugin="agent-codespaces")
    if len(args) == 3 and args[0] == "caller":
        return caller(Path(args[1]), Path(args[2]))
    if len(args) == 6 and args[0] == "crash-publication":
        return crash_publication(Path(args[1]), Path(args[2]), args[3], args[4], args[5])
    if not args or args[0] != "--ephemeral-ci-consent" or len(args) not in (1, 2):
        raise RuntimeError("protocol fixture requires explicit ephemeral Windows CI consent")
    raw_root = args[1] if len(args) == 2 else sys.stdin.readline(32769).strip()
    if not raw_root or len(raw_root) > 32768:
        raise RuntimeError("protocol fixture requires a bounded root")
    root = Path(raw_root)
    admit(root, True)
    result = inspect(root)
    print(json.dumps(result))
    return 0 if result["supported"] and result["cleanup_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
