"""Disposable native direct-launch proof with two tracked cleanup ancestors."""

from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import traceback

from windows_broker_fixture import admit, fixture_environment

SOURCE = Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src"


def _verified_process(pid: int, birth: str, label: str):
    from agent_procutil.daemon_broker import WindowsDaemonProcess
    from agent_procutil._daemon_instance_identity import _windows_birth

    process = WindowsDaemonProcess(pid, [label])
    try:
        if _windows_birth(process._handle) != birth:
            raise RuntimeError(f"{label} identity changed")
    except BaseException:
        process.close()
        raise
    return process


def _observe(predicate, process, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        if process.poll() is not None:
            logs = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")[-8192:]
                for path in Path(process.args[-1]).glob("*.log")
            )
            raise RuntimeError("native lifetime actor exited before its witness:\n" + logs)
        time.sleep(0.05)
    raise TimeoutError("native lifetime witness deadline expired")


def _worker(root: Path) -> None:
    admit(root.parent, True)
    import agent_procutil as pu
    from agent_procutil import _job_scopes
    from agent_procutil._daemon_instance_identity import _windows_birth

    python, python_env = pu.direct_python(sys.executable)
    ordinary = subprocess.Popen(
        [python, "-c", "import time; time.sleep(120)"],
        env={**os.environ, **python_env},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    ready = root / "daemon-ready.json"
    code = (
        "import json,os,sys,time;from pathlib import Path;"
        "p=Path(sys.argv[1]);t=p.with_suffix('.tmp');"
        f"t.write_text(json.dumps({{'scopes':os.environ.get({_job_scopes.ENVIRONMENT_KEY!r}),"
        "'pid':os.getpid()}));t.replace(p);time.sleep(120)"
    )
    outcome = {"ordinary": ordinary.pid, "ordinary_birth": _windows_birth(ordinary._handle),
               "scope_count": len(_job_scopes.scope_names(os.environ))}
    try:
        daemon = pu.spawn_daemon(
            [python, "-c", code, str(ready)], env_overrides=python_env,
            cwd=root, log_path=root / "daemon.log", console_children=True,
        )
    except pu.DaemonSpawnError as error:
        outcome.update(
            admitted=False, cleanup_confirmed=error.cleanup_confirmed,
            process_created=error.process_created, admission_sent=error.admission_sent,
        )
    else:
        outcome.update(admitted=True, daemon=daemon.pid, daemon_birth=_windows_birth(daemon._handle))
    temporary = root / "outcome.tmp"
    temporary.write_text(json.dumps(outcome), encoding="utf-8")
    temporary.replace(root / "outcome.json")
    time.sleep(120)


def _launcher(root: Path, environment) -> None:
    admit(root.parent, True)
    import agent_procutil as pu
    from agent_procutil import _job_scopes

    python, python_env = pu.direct_python(sys.executable)
    replacement = {**environment, **python_env}
    replacement.pop(_job_scopes.ENVIRONMENT_KEY, None)
    process = pu.spawn_daemon(
        [python, str(Path(__file__).resolve()), "--intermediate", str(root)],
        env=replacement, cwd=root, log_path=root / "intermediate.log",
        breakaway=False, console_children=True,
    )
    try:
        process.wait(timeout=120)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _intermediate(root: Path, environment) -> None:
    admit(root.parent, True)
    import agent_procutil as pu

    python, python_env = pu.direct_python(sys.executable)
    with (root / "worker.log").open("wb") as log:
        process, job = pu.spawn_sync_in_kill_on_close_job(
            [python, str(Path(__file__).resolve()), "--worker", str(root)],
            env={**environment, **python_env}, cwd=root, require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **pu.no_window_kwargs(),
        )
    try:
        process.wait(timeout=120)
    finally:
        job.terminate_and_wait(5)


def _qualify_native_shell(root: Path, environment, pu) -> list[dict]:
    def literal(path):
        return "'" + str(path).replace("'", "''") + "'"

    shell = Path(os.environ["SystemRoot"]) / r"System32\WindowsPowerShell\v1.0\powershell.exe"
    native = SOURCE / "agent_procutil" / "native_daemon.ps1"
    owned = SOURCE / "agent_procutil" / "native_owned.ps1"
    cycles = []
    for cycle in range(2):
        directory = root / f"native-shell-{cycle}"
        directory.mkdir()
        worker = directory / "worker.ps1"
        worker.write_text(
            "$ErrorActionPreference='Stop'\n"
            f"$root={literal(directory)}\n"
            "$deadline=[DateTime]::UtcNow.AddSeconds(15)\n"
            "while (-not (Test-Path -LiteralPath (Join-Path $root 'continue'))) {\n"
            " if ([DateTime]::UtcNow -gt $deadline) { throw 'caller Job closure was not witnessed' }\n"
            " Start-Sleep -Milliseconds 50\n}\n"
            f". {literal(owned)}\n"
            "$code=Invoke-NativeOwnedProcess -FilePath (Join-Path $env:SystemRoot 'System32\\cmd.exe') "
            "-Arguments @('/d','/c','echo native-post-close') -WorkingDirectory $root "
            "-LogPath (Join-Path $root 'owned.log')\n"
            "@{code=$code;scopes=$env:COPILOT_EXTENSIONS_OWNED_JOB_SCOPES} | ConvertTo-Json -Compress "
            "| Set-Content -LiteralPath (Join-Path $root 'completed.json') -Encoding UTF8\n"
            "exit $code\n",
            encoding="utf-8",
        )
        caller = directory / "caller.ps1"
        caller.write_text(
            "$ErrorActionPreference='Stop'\n"
            "Write-Output 'native caller loading admission'\n"
            f". {literal(native)}\n"
            "Write-Output 'native caller starting daemon'\n"
            f"$p=Start-NativeDaemon -Script {literal(worker)} -Arguments @('fixture') "
            f"-WorkingDirectory {literal(directory)} -LogPath {literal(directory / 'daemon.log')}\n"
            "Write-Output 'native caller received admission'\n"
            "@{pid=$p.Id;birth=[CopilotExtensions.DaemonInstanceNative]::Birth($p)} "
            f"| ConvertTo-Json -Compress | Set-Content -LiteralPath {literal(directory / 'outcome.json')} "
            "-Encoding UTF8\n$p.Dispose()\n",
            encoding="utf-8",
        )
        env = {**environment, "COPILOT_EXTENSIONS_DAEMON_REGISTRY": str(directory / "instances")}
        daemon = None
        with (directory / "caller.log").open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(caller)],
                env=env, cwd=directory, require_job=True, allow_breakaway=True,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
            try:
                if process.wait(timeout=30):
                    raise RuntimeError("native shell caller refused: " + (directory / "caller.log").read_text(
                        encoding="utf-8", errors="replace",
                    )[-8192:])
                outcome = json.loads((directory / "outcome.json").read_text(encoding="utf-8-sig"))
                daemon = _verified_process(outcome["pid"], outcome["birth"], "native shell daemon")
                job.close()
                if daemon.poll() is not None:
                    raise RuntimeError("native shell daemon died with caller cleanup")
                (directory / "continue").write_text("caller Job closed", encoding="utf-8")
                if daemon.wait(timeout=30):
                    raise RuntimeError("native shell post-caller owned work failed")
                result = json.loads((directory / "completed.json").read_text(encoding="utf-8-sig"))
                if result != {"code": 0, "scopes": None} or "native-post-close" not in (
                    directory / "owned.log"
                ).read_text(encoding="utf-8", errors="replace"):
                    raise RuntimeError("native shell did not complete owned work after caller cleanup")
                cycles.append({"caller_job_closed": True, "post_close_owned_work_completed": True})
            except subprocess.TimeoutExpired as error:
                details = "\n".join(
                    path.name + ":\n" + path.read_text(encoding="utf-8", errors="replace")[-8192:]
                    for path in directory.glob("*.log")
                )
                raise RuntimeError("native shell exceeded its unchanged fixture deadline:\n" + details) from error
            finally:
                if job.handle:
                    job.terminate_and_wait(5)
                process.wait(timeout=5)
                if daemon is not None:
                    try:
                        if daemon.poll() is None:
                            daemon.kill()
                        daemon.wait(timeout=5)
                    finally:
                        daemon.close()
    return cycles


def qualify(root: Path, environment: dict[str, str]) -> dict:
    admit(root, True)
    sys.path.insert(0, str(SOURCE))
    import agent_procutil as pu
    cycles = []
    python, python_env = pu.direct_python(sys.executable)
    for cycle, restrictive in enumerate((False, False, True), 1):
        directory = root / f"native-lifetime-{cycle}"
        directory.mkdir()
        env = {**environment, **python_env,
               "COPILOT_EXTENSIONS_DAEMON_REGISTRY": str(directory / "instances")}
        with (directory / "launcher.log").open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [python, str(Path(__file__).resolve()), "--launcher", str(directory)],
                env=env, cwd=directory, require_job=True, allow_breakaway=not restrictive,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
        ordinary = daemon = None
        try:
            outcome = _observe(
                lambda: json.loads((directory / "outcome.json").read_text())
                if (directory / "outcome.json").is_file() else None, process,
            )
            if outcome["scope_count"] != 2:
                raise RuntimeError("native fixture omitted a caller cleanup ancestor")
            ordinary = _verified_process(outcome["ordinary"], outcome["ordinary_birth"], "bounded control")
            if restrictive:
                if (
                    outcome["admitted"] or not outcome.get("cleanup_confirmed")
                    or not outcome.get("process_created") or outcome.get("admission_sent")
                    or (directory / "daemon-ready.json").exists()
                ):
                    raise RuntimeError("shared restrictive outer Job was not refused before execution")
            else:
                if not outcome["admitted"]:
                    raise RuntimeError("normal native daemon was refused: " + json.dumps(outcome))
                daemon = _verified_process(outcome["daemon"], outcome["daemon_birth"], "durable control")
                ready = _observe(
                    lambda: json.loads((directory / "daemon-ready.json").read_text())
                    if (directory / "daemon-ready.json").is_file() else None, process,
                )
                if ready != {"scopes": None, "pid": daemon.pid}:
                    raise RuntimeError("admitted daemon retained escaped scope metadata")
            job.close()
            process.wait(timeout=5)
            ordinary.wait(timeout=5)
            if daemon is not None:
                for _sample in range(2):
                    if daemon.poll() is not None:
                        raise RuntimeError("daemon died with a launcher-owned cleanup Job")
                    time.sleep(1)
            cycles.append({
                "two_owned_cleanup_ancestors": True, "all_caller_jobs_closed": True,
                "bounded_control_exited": True, "durable_survived": not restrictive,
                "shared_outer_refused": restrictive,
            })
        finally:
            if job.handle:
                job.terminate_and_wait(5)
            process.wait(timeout=5)
            for child in (ordinary, daemon):
                if child is not None:
                    try:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=5)
                    finally:
                        child.close()
    return {
        "direct_native_lifetime": cycles,
        "cleanup_confirmed": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ephemeral-ci-consent", action="store_true")
    parser.add_argument("--native-shell-only", action="store_true", required=True)
    args = parser.parse_args(argv)
    temporary = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    admit(temporary / "ce-broker-probe-native-admission", args.ephemeral_ci_consent)
    root = Path(tempfile.mkdtemp(prefix="ce-broker-probe-native-", dir=temporary))
    profile = root / "profile"
    profile.mkdir()
    environment = fixture_environment(dict(os.environ), control=True)
    environment.update(
        HOME=str(profile), USERPROFILE=str(profile),
        HOMEDRIVE=profile.drive, HOMEPATH=str(profile)[len(profile.drive):],
        APPDATA=str(profile / "AppData" / "Roaming"),
        LOCALAPPDATA=str(profile / "AppData" / "Local"),
    )
    for name in ("APPDATA", "LOCALAPPDATA"):
        Path(environment[name]).mkdir(parents=True)
    sys.path.insert(0, str(SOURCE))
    import agent_procutil as pu

    outcome = {"source": subprocess.check_output(
        ["git", "-C", str(SOURCE.parents[2]), "rev-parse", "HEAD"],
        text=True, **pu.no_window_kwargs(),
    ).strip()}
    try:
        outcome.update(
            qualified=True, native_shell_lifetime=_qualify_native_shell(root, environment, pu),
        )
    except Exception as error:
        outcome.update(qualified=False, failure="".join(traceback.format_exception(error))[-32768:])
    (root / "native-result.json").write_text(json.dumps(outcome, indent=2), encoding="utf-8")
    print(json.dumps(outcome, indent=2))
    return 0 if outcome["qualified"] else 1


if __name__ == "__main__":
    sys.path.insert(0, str(SOURCE))
    if len(sys.argv) == 3 and sys.argv[1] in ("--worker", "--launcher", "--intermediate"):
        if sys.argv[1] == "--worker":
            _worker(Path(sys.argv[2]))
        elif sys.argv[1] == "--intermediate":
            _intermediate(Path(sys.argv[2]), dict(os.environ))
        else:
            _launcher(Path(sys.argv[2]), dict(os.environ))
    else:
        raise SystemExit(main())
