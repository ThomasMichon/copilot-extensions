"""Disposable CI proof of native retirement custody after caller boundary death.

Windows is composed into the existing explicitly adopted broker fixture.
Linux uses an owned caller group and anonymous inherited native handles.
Neither shape is an installed-runtime removal or host deployment drill.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _admit(root: Path) -> None:
    if os.name == "nt":
        sys.path.insert(0, str(Path(__file__).absolute().parent))
        from windows_broker_fixture import admit
        admit(root, True)
        return
    temporary = os.environ.get("RUNNER_TEMP")
    if (
        not sys.platform.startswith("linux") or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or not temporary
        or not root.resolve().is_relative_to(Path(temporary).resolve())
        or not root.name.startswith("retirement-custody-")
    ):
        raise RuntimeError("Retirement fixture requires an owned ephemeral CI root")


def caller(packages: Path, root: Path, target: int, birth: str, report: Path, pause: bool) -> None:
    _admit(root)
    sys.path.insert(0, str(packages))
    from agent_procutil._daemon_instance_identity import birth_identity
    from agent_procutil.retirement_custody import RetirementCustody

    if os.name == "nt" and sys.stdin.readline() != "launch\n":
        raise RuntimeError("Retirement caller was not released by its fixture owner")
    with RetirementCustody(root) as custody:
        if pause:
            custody.pin(target, birth).pause()
        process = custody._process
        report.write_text(json.dumps({
            "pid": process.pid, "birth": birth_identity(process.pid),
            "handle": int(process._handle) if os.name == "nt" else None,
            "instance": process.instance_receipt.instance_id,
            "registry": str(process.instance_receipt.registry_root),
        }), encoding="utf-8")
        time.sleep(60)


def _progress(path: Path, previous: int = 0) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > previous:
            return path.stat().st_size
        time.sleep(.01)
    raise RuntimeError("Original retirement target did not resume")


def qualify(root: Path, packages: Path, pu) -> dict:
    _admit(root)
    from agent_procutil._daemon_instance_identity import birth_identity
    from agent_procutil.daemon_instances import (
        DaemonInstanceReceipt,
        cleanup_instance,
        daemon_test_lifecycle,
    )
    from agent_procutil.process_retirement import PinnedProcess

    if os.name == "nt":
        from windows_broker_fixture import caller_job_memberships, fixture_environment, make_job
        environment = fixture_environment(dict(os.environ), control=True)
    else:
        environment = {
            key: value for key, value in os.environ.items()
            if key in {
                "PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT",
                "RUNNER_TEMP", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT",
            }
        }
    script = Path(__file__).absolute()
    progress = root / "retirement-progress"
    code = (
        "import sys,time\n"
        "with open(sys.argv[1],'ab',buffering=0) as stream:\n"
        " while True:\n"
        "  stream.write(b'.')\n"
        "  time.sleep(.01)\n"
    )
    target = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-c", code, str(progress)],
        env=environment, **pu.no_window_kwargs(),
    )
    cycles = []
    try:
        _progress(progress)
        with daemon_test_lifecycle(
            owner="native-retirement-boundary", registry_root=root / "retirement-instances",
        ) as lifetime:
            environment.update(lifetime)
            for cycle, contained in enumerate((False, False, True) if os.name == "nt" else (False, False)):
                offer_path = root / f"retirement-offer-{cycle}.json"
                env = dict(environment)
                if contained:
                    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
                arguments = [
                    getattr(sys, "_base_executable", sys.executable), "-I", str(script),
                    "caller", str(packages), str(root), str(target.pid),
                    birth_identity(target.pid), str(offer_path), "0" if contained else "1",
                ]
                jobs = []
                guardian = None
                process = None
                try:
                    if os.name == "nt":
                        process, job = pu.spawn_sync_in_kill_on_close_job(
                            arguments, require_job=True, allow_breakaway=False,
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            **pu.no_window_kwargs(),
                        )
                        if job is None:
                            raise RuntimeError("Retirement caller Job is unavailable")
                        jobs.append(job)
                        inner = make_job(pu, jobs)
                        api = pu._kernel32()
                        pu._configure_job_api(api)
                        if not api.AssignProcessToJobObject(
                            inner.handle, int(process._handle),
                        ):
                            import ctypes
                            raise ctypes.WinError(ctypes.get_last_error())
                        process.stdin.write(b"launch\n")
                        process.stdin.flush()
                    else:
                        process = subprocess.Popen(
                            arguments, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True,
                        )
                    deadline = time.monotonic() + 40
                    while True:
                        try:
                            offer = json.loads(offer_path.read_text(encoding="utf-8"))
                            break
                        except (FileNotFoundError, json.JSONDecodeError):
                            if process.poll() is not None:
                                raise RuntimeError("Retirement caller failed: " + process.stderr.read().decode())
                            if time.monotonic() >= deadline:
                                raise TimeoutError("Retirement caller did not offer native custody")
                            time.sleep(.02)
                    if os.name == "nt":
                        from agent_procutil.daemon_broker import _kernel32 as process_api
                        from agent_procutil.windows_broker_native import (
                            NativeChild,
                            _duplicate,
                        )
                        guardian = NativeChild.from_handle(
                            _duplicate(int(process._handle), offer["handle"]),
                            offer["pid"], ["retirement-custodian"],
                        )
                        memberships = caller_job_memberships(process_api(), guardian, jobs)
                        # Disposable admission deliberately owns a separate expiry Job.
                        if memberships != [contained] * len(jobs):
                            raise RuntimeError(
                                f"Retirement custody caller-Job mismatch (cycle={cycle}, "
                                f"contained={contained}, memberships={memberships})"
                            )
                    else:
                        guardian = PinnedProcess(offer["pid"], offer["birth"])
                        if os.getpgid(offer["pid"]) == process.pid:
                            raise RuntimeError("Retirement custody still belongs to the caller group")
                    before = progress.stat().st_size
                    if not contained:
                        time.sleep(.05)
                        if progress.stat().st_size != before:
                            raise RuntimeError("Retirement pause was not observed")
                    if jobs:
                        for job in reversed(jobs):
                            job.terminate_and_wait(5)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    _progress(progress, before)
                    if os.name == "nt":
                        guardian.wait(timeout=10)
                    elif not guardian.exited(10):
                        raise RuntimeError("Retirement custodian did not exit after rollback")
                    receipt = DaemonInstanceReceipt(offer["instance"], Path(offer["registry"]))
                    if cleanup_instance(receipt, terminate=False).status != "settled":
                        raise RuntimeError("Retirement instance custody did not settle")
                    cycles.append({
                        "contained": contained, "caller_boundary_closed": True,
                        "original_target_alive": target.poll() is None,
                        "native_custodian_exit": True,
                        "caller_jobs": len(jobs),
                    })
                finally:
                    if process is not None and process.poll() is None:
                        if jobs:
                            for job in reversed(jobs):
                                job.terminate_and_wait(5)
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    if guardian is not None:
                        if os.name == "nt":
                            guardian.wait(timeout=10)
                        elif not guardian.exited(10):
                            raise RuntimeError("Fixture retains a live retirement custodian")
                        guardian.close()
                    for job in jobs:
                        job.close()
                    if process is not None:
                        for stream in (process.stdin, process.stdout, process.stderr):
                            if stream is not None:
                                stream.close()
        return {"cycles": cycles, "native_boundary_rollback": True}
    finally:
        if target.poll() is None:
            target.kill()
            target.wait(timeout=5)


def main() -> None:
    if len(sys.argv) == 8 and sys.argv[1] == "caller":
        caller(
            Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), sys.argv[5],
            Path(sys.argv[6]), sys.argv[7] == "1",
        )
        return
    if (
        sys.argv[1:] != ["--ephemeral-ci-consent"] or not sys.platform.startswith("linux")
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not os.environ.get("RUNNER_TEMP")
    ):
        raise RuntimeError("Retirement group fixture requires explicit ephemeral Linux CI consent")
    source = Path(__file__).resolve().parents[1]
    packages = source / "libs" / "agent-procutil" / "src"
    sys.path[:0] = [str(packages), str(source / "libs" / "zdd" / "src")]
    import agent_procutil as pu
    import zdd

    with tempfile.TemporaryDirectory(prefix="retirement-custody-", dir=os.environ["RUNNER_TEMP"]) as temporary:
        root = Path(temporary)
        # The caller admits both canonical packages under isolated Python.
        joined = root / "packages"
        joined.mkdir()
        import shutil
        for package in (pu, zdd):
            shutil.copytree(Path(package.__file__).parent, joined / package.__name__)
        print(json.dumps(qualify(root, joined, pu)))


if __name__ == "__main__":
    main()
