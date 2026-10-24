"""Production protocol in a synthetic, explicitly authorized disposable fixture.

Copies source into an owned immutable fixture directory; installs no packages,
updates no host runtime and never registers outside a hosted Windows CI runner.
"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path


def admit(root: Path, consent: bool) -> None:
    temporary = os.environ.get("RUNNER_TEMP")
    if (
        not consent or os.name != "nt"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not temporary or not root.resolve().is_relative_to(Path(temporary).resolve())
        or not root.name.startswith("ce-broker-probe-")
    ):
        raise RuntimeError("protocol fixture requires explicit ephemeral Windows CI consent")


def imports(package_root: Path) -> None:
    sys.path.insert(0, str(package_root))


def adopt_action(package_root: Path, root: Path, generation: str) -> int:
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    from update_driver.windows_broker import adopt

    adopt(root, root / "versions" / generation, root=root / "execution")
    return 0


def adopt_generation(package_root: Path, root: Path, generation: str):
    """Exercise the actual default executor and its native owned process tree."""
    admit(root, True)
    if generation not in {"first", "replacement"}:
        raise ValueError("fixture action generation refused")
    imports(package_root)
    from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
    from agent_procutil.windows_broker_installation import load

    execution = root / "execution"
    request = enqueue(
        RequestSpec("windows-broker-fixture", source=generation, cwd=str(root)),
        runner=TrustedRunner((
            str(Path(sys.base_prefix) / "python.exe"), "-I", str(Path(__file__).resolve()),
            "adopt", str(package_root), str(root), generation,
        ), str(root)),
        root=execution,
    )
    drain(root=execution)
    completed = status(request.id, root=execution)
    if completed.state != "completed" or completed.exit_code != 0:
        raise RuntimeError("fixture driver-owned adoption failed: " + completed.log_path.read_text())
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
        root / "controller" / ".runtime-bootstrap", slot / ".runtime-bootstrap",
    )
    runtime.mark_complete(root, generation)
    return packages


def fixture_environment(source: dict[str, str], *, control: bool) -> dict[str, str]:
    allowed = {
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "BROKER_FIXTURE_VALUE",
    }
    if control:
        allowed.update({
            "RUNNER_TEMP", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT",
            "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
            "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
            "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
            "SYSTEMDRIVE", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
        })
    return {name: value for name, value in source.items() if name.upper() in allowed}


def caller(package_root: Path, root: Path) -> int:
    admit(root, True)
    imports(package_root)
    from agent_procutil.windows_task_broker import spawn

    if sys.stdin.readline() != "launch\n":
        return 2
    child = spawn(
        [
            str(Path(sys.base_prefix) / "python.exe"), "-I", str(root / "resident.py"),
            "argument with spaces", "", "\u65e5\n&",
        ],
        cwd=str(root), env=fixture_environment(dict(os.environ), control=False),
        creationflags=0x08000000,
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


def make_job(pu, owners):
    api = pu._kernel32()
    pu._configure_job_api(api)
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    owner = pu.JobHandle(handle)
    owners.append(owner)
    limits = pu._build_job_extended_limit_info()()
    limits.BasicLimitInformation.LimitFlags = pu._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not api.SetInformationJobObject(
        handle, pu._JobObjectExtendedLimitInformation, ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return owner


def close_job(pu, owner):
    if not pu._kernel32().CloseHandle(owner.handle):
        raise ctypes.WinError(ctypes.get_last_error())
    owner._handle = None


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
    controller = root / "controller" / "site-packages"
    controller.mkdir(parents=True)
    for library, package in (
        ("agent-procutil", "agent_procutil"), ("zdd", "zdd"),
        ("update-driver", "update_driver"), ("single-instance-lease", "single_instance_lease"),
    ):
        shutil.copytree(
            source / "libs" / library / "src" / package, controller / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    bootstrap = root / "controller" / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    for helper in (source / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(helper, bootstrap / helper.name)
    runtime_source = root / "controller" / ".runtime-bootstrap" / "versioned_runtime.py"
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
        CONFIG_ENV,
        OWNER_ENV,
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
        "supported": False, "cleanup_confirmed": False, "cycles": [],
    }
    callers = []
    children = []
    jobs = []
    adopted = False
    try:
        config = adopt_generation(controller, root, "first")
        adopted = True
        config = adopt_generation(controller, root, "first")
        result["driver_owned_adoption"] = True
        result["canonical_installed_action"] = True
        first_status = task_operation("status", config, Deadline(10))
        if first_status.get("configured") is not True:
            raise RuntimeError("fixture adoption was not configured")
        result["negative_topology"] = negative_topology(root, pu, callers, children, jobs)
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
                    if runtime.gc(root) != [control]:
                        raise RuntimeError("fixture collector reclaimed a recovery generation")
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
                    or runtime.gc(root) != ["first"]
                    or packages.exists()
                ):
                    raise RuntimeError("fixture old action generation was not retired after recovery")
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
            env.update({
                CONFIG_ENV: str(config.config_path), OWNER_ENV: config.installation,
                "BROKER_FIXTURE_VALUE": "live-only-synthetic\n&",
            })
            process, outer = pu.spawn_sync_in_kill_on_close_job(
                [
                    str(Path(sys.base_prefix) / "python.exe"), "-I", str(Path(__file__).resolve()),
                    "caller", str(controller), str(root),
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
                observations = (root / "resident.log").read_text(encoding="utf-8").splitlines()
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
                "finite_broker_exit_proved": True, "all_caller_jobs_closed": True,
                "exact_contract": True, "actual_child_cleanup_confirmed": True,
            })
        result["supported"] = True
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        result["failure_type"] = type(error).__name__
        result["failure"] = str(error)
    finally:
        cleanup = True
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
        (root / "protocol-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8",
        )
    return result


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "adopt":
        return adopt_action(Path(args[1]), Path(args[2]), args[3])
    if len(args) == 3 and args[0] == "caller":
        return caller(Path(args[1]), Path(args[2]))
    if len(args) == 6 and args[0] == "crash-publication":
        return crash_publication(Path(args[1]), Path(args[2]), args[3], args[4], args[5])
    if len(args) != 2 or args[0] != "--ephemeral-ci-consent":
        raise RuntimeError("protocol fixture requires explicit ephemeral Windows CI consent")
    root = Path(args[1])
    admit(root, True)
    result = inspect(root)
    print(json.dumps(result))
    return 0 if result["supported"] and result["cleanup_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
