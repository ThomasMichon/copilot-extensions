"""Real process boundaries against fixture products, never a live installation."""

import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_procutil
import single_instance_lease
import update_driver
from update_driver import (
    InterruptedError, RequestSpec, SpawnError, TrustedRunner, drain, enqueue, ensure_worker,
    status, wait,
)


def environment():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        str(Path(package.__file__).resolve().parent.parent)
        for package in (update_driver, agent_procutil, single_instance_lease)
    )
    return env


def runner(area, delay=".15", code="0"):
    script = Path(__file__).with_name("fixture_runner.py").resolve()
    return TrustedRunner(
        (sys.executable, str(script), str(area / "state"), str(area / "effects"), delay, code),
        str(area), timeout=15, env=(("PYTHONPATH", environment()["PYTHONPATH"]),),
    )


def worker(area):
    return subprocess.Popen(
        [sys.executable, "-m", "update_driver", "worker", "--root", str(area / "state")],
        cwd=area, env=environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **agent_procutil.no_window_kwargs(),
    )


def collect(process):
    try:
        out, err = process.communicate(timeout=20)
    except BaseException:
        process.kill()
        process.wait(timeout=5)
        raise
    assert process.returncode == 0, (out, err)


def eventually(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError("fixture observation timed out")


def test_two_process_workers_have_no_transaction_overlap(area):
    root = area / "state"
    requests = [
        enqueue(RequestSpec("fixture", argv=(str(i),)), runner=runner(area), root=root)
        for i in range(4)
    ]
    processes = [worker(area), worker(area)]
    for process in processes:
        collect(process)
    assert all(status(r.id, root=root).exit_code == 0 for r in requests), [
        r.log_path.read_text() if r.log_path.exists() else "missing log" for r in requests
    ]
    assert (area / "effects").exists(), [r.log_path.read_text() for r in requests]
    lines = (area / "effects").read_text().splitlines()
    assert len(lines) == 8
    assert all(lines[i].startswith("start ") and lines[i + 1].startswith("end ")
               for i in range(0, 8, 2))
    assert all(wait(request.id, root=root, timeout=1).exit_code == 0 for request in requests)


def test_each_queued_scope_is_applied_before_product_routing(area, monkeypatch):
    import json

    root = area / "state"
    monkeypatch.setenv("FIXTURE_SCOPE", "wrong-worker-scope")
    monkeypatch.setenv("FIXTURE_OPTION", "must-not-leak")
    script = (
        "import json,os,pathlib,sys;"
        "before=os.getcwd();"
        "os.chdir(pathlib.Path(os.environ['FIXTURE_SCOPE'])/'anchor');"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps("
        "{'before':before,'anchor':os.getcwd(),'option':os.environ.get('FIXTURE_OPTION')}))"
    )
    requests = []
    for name in ("first", "second"):
        scope = area / name
        (scope / "anchor").mkdir(parents=True)
        request = enqueue(
            RequestSpec("fixture", project="same-project", context=(("FIXTURE_SCOPE", str(scope)),)),
            runner=TrustedRunner(
                (sys.executable, "-c", script, str(scope / "result.json")), str(area),
                context_keys=("FIXTURE_SCOPE", "FIXTURE_OPTION"),
            ),
            root=root,
        )
        requests.append(request)
    collect(worker(area))
    for request, name in zip(requests, ("first", "second")):
        assert status(request.id, root=root).exit_code == 0
        assert json.loads((area / name / "result.json").read_text()) == {
            "before": str(area), "anchor": str(area / name / "anchor"), "option": None,
        }


def test_real_active_request_does_not_swallow_arrival_and_streams(area):
    root = area / "state"
    first = enqueue(RequestSpec("fixture"), runner=runner(area, delay=".7", code="23"), root=root)
    process = worker(area)
    try:
        eventually(lambda: first.log_path.exists() and "fixture-start" in first.log_path.read_text())
        assert process.poll() is None
        second = enqueue(first.spec, runner=first.runner, root=root)
        assert second.id != first.id
        output = io.StringIO()
        result = wait(first.id, root=root, timeout=10, output=output)
        assert result.exit_code == 23
        assert "fixture-start" in output.getvalue() and "fixture-end" in output.getvalue()
        assert wait(second.id, root=root, timeout=10).exit_code == 23
    finally:
        collect(process)
    assert len((area / "effects").read_text().splitlines()) == 4


def test_cross_process_enqueue_coalesces_pending(area):
    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area), root=root)
    script = (
        "import json,sys; from pathlib import Path; from update_driver import *; "
        "r=status(sys.argv[2],root=Path(sys.argv[1])); "
        "print(enqueue(r.spec,runner=r.runner,root=Path(sys.argv[1])).id)"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(root), request.id],
                         env=environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         **agent_procutil.no_window_kwargs())
        for _ in range(3)
    ]
    identifiers = []
    for process in processes:
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err
        identifiers.append(out.decode().strip())
    assert identifiers == [request.id] * 3
    assert len(status(root=root)) == 1


def test_submitter_exit_does_not_own_worker_lifetime(area):
    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area, delay=".5"), root=root)
    script = (
        "import sys; from pathlib import Path; from update_driver import ensure_worker; "
        "p=ensure_worker((sys.executable,'-m','update_driver','worker','--root',sys.argv[1]),"
        "root=Path(sys.argv[1])); print(p.pid)"
    )
    submitter = subprocess.run(
        [sys.executable, "-c", script, str(root)], env=environment(),
        capture_output=True, timeout=10, **agent_procutil.no_window_kwargs(),
    )
    assert submitter.returncode == 0, submitter.stderr
    observer = None
    pidfd = None
    try:
        pid = int(submitter.stdout.decode().strip())
        if os.name == "nt":
            from agent_procutil.daemon_broker import WindowsDaemonProcess
            try:
                observer = WindowsDaemonProcess(pid, ["fixture-worker"])
            except OSError as error:
                if getattr(error, "winerror", None) != 87:
                    raise
        elif hasattr(os, "pidfd_open"):
            try:
                pidfd = os.pidfd_open(pid)
            except ProcessLookupError:
                pass
        result = wait(request.id, root=root, timeout=15)
        assert result.exit_code == 0
        assert (area / "effects").read_text().count("end ") == 1
        # Completion precedes finite worker exit. Observe its actual handle
        # before removing fixture files; never kill or reclaim by PID/age.
        if observer is not None:
            assert observer.wait(timeout=10) == 0
        if pidfd is not None:
            import select
            assert select.select([pidfd], [], [], 10)[0]
    finally:
        if observer is not None:
            observer.close()
        if pidfd is not None:
            os.close(pidfd)


def test_worker_death_leaves_conservative_fence(area):
    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area, delay="1"), root=root)
    process = worker(area)
    eventually(lambda: request.log_path.exists() and "fixture-start" in request.log_path.read_text())
    later = enqueue(RequestSpec("fixture", argv=("later",)), runner=runner(area), root=root)
    process.kill()
    process.communicate(timeout=10)
    # The waiter itself observes free kernel custody and journals interruption;
    # no second update/drain invocation is required.
    assert wait(request.id, root=root).state == "interrupted"
    with pytest.raises(InterruptedError):
        enqueue(RequestSpec("fixture", argv=("refused",)), runner=runner(area), root=root)
    with pytest.raises(InterruptedError):
        ensure_worker((sys.executable, "-m", "update_driver", "worker"), root=root)
    with pytest.raises(InterruptedError):
        drain(root=root)
    assert status(request.id, root=root).state == "interrupted"
    assert status(later.id, root=root).state == "pending"
    # Canonical guardian/Job cleanup has time to settle; no unknown PID is killed.
    time.sleep(.15)
    assert (area / "effects").read_text().count("start ") == 1


def test_distinct_product_runners_need_no_cross_import(area):
    root = area / "state"
    requests = [
        enqueue(RequestSpec(backend), runner=runner(area), root=root)
        for backend in ("manager-fixture", "engine-fixture")
    ]
    process = worker(area)
    collect(process)
    assert [status(r.id, root=root).exit_code for r in requests] == [0, 0]


def test_missing_worker_entrypoint_is_explicit(area):
    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area), root=root)
    with pytest.raises(SpawnError, match="before queue startup"):
        ensure_worker(
            (sys.executable, "-m", "update_driver_missing_fixture"),
            root=root, env=environment(), startup_timeout=5,
        )
    result = status(request.id, root=root)
    assert result.state == "pending" and result.exit_code is None
    assert "worker exited" in result.error
    assert not list(root.glob(".worker-started-*"))
    # A later genuine worker can recover pending work, not an ambiguous running transaction.
    launched = ensure_worker(
        (sys.executable, "-m", "update_driver", "worker", "--root", str(root)),
        root=root, env=environment(),
    )
    assert wait(request.id, root=root, timeout=10).exit_code == 0
    assert launched.wait(timeout=10) == 0


def test_enqueue_after_empty_scan_before_release_cannot_lose_wakeup(area, monkeypatch):
    from single_instance_lease.operation import OperationLock

    root = area / "state"
    original_release = OperationLock.release
    submitted, workers = [], []

    def release_with_late_submission(lock):
        # drain already observed an empty queue, but still owns kernel custody.
        if not submitted:
            request = enqueue(RequestSpec("fixture"), runner=runner(area), root=root)
            submitted.append(request)
            workers.append(ensure_worker(
                (sys.executable, "-m", "update_driver", "worker", "--root", str(root)),
                root=root, env=environment(),
            ))
            assert workers[0].poll() is None
            assert status(request.id, root=root).state == "pending"
        original_release(lock)

    monkeypatch.setattr(OperationLock, "release", release_with_late_submission)
    assert drain(root=root) == 0
    assert wait(submitted[0].id, root=root, timeout=10).exit_code == 0
    assert workers[0].wait(timeout=10) == 0


def test_failed_boot_admission_never_acknowledges_startup(area):
    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area), root=root)
    script = (
        "import sys; from pathlib import Path; from update_driver import boot,drain; "
        "boot.kernel_boot_identity=lambda: (_ for _ in ()).throw(OSError('synthetic')); "
        "drain(root=Path(sys.argv[1]))"
    )
    with pytest.raises(SpawnError, match="worker admission failed: boot_identity_unavailable"):
        ensure_worker(
            (sys.executable, "-c", script, str(root)), root=root,
            env=environment(), startup_timeout=5,
        )
    result = status(request.id, root=root)
    assert result.state == "pending" and result.started_at is None and result.boot_id is None
    assert result.error == "worker admission failed: boot_identity_unavailable:OSError"
    assert not list(root.glob(".worker-started-*"))
    with pytest.raises(SpawnError, match="boot_identity_unavailable"):
        wait(request.id, root=root)


def test_wait_observes_exact_worker_death_between_ack_and_claim(area):
    from single_instance_lease.operation import OperationLock

    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area), root=root)
    launched = None
    with OperationLock(root / "execution.lock"):
        launched = ensure_worker(
            (sys.executable, "-m", "update_driver", "worker", "--root", str(root)),
            root=root, env=environment(),
        )
        try:
            assert status(request.id, root=root).state == "pending"
            launched.terminate()
            launched.wait(timeout=10)
            # An exited handle alone cannot overrule another active custodian.
            with pytest.raises(TimeoutError):
                wait(request.id, root=root, worker=launched, timeout=.1)
            assert status(request.id, root=root).error is None
        finally:
            if launched.poll() is None:
                launched.terminate()
                launched.wait(timeout=10)
    with pytest.raises(SpawnError, match="worker exited before claiming request"):
        wait(request.id, root=root, worker=launched)
    result = status(request.id, root=root)
    assert result.state == "pending" and result.exit_code is None
    assert "worker exited before claiming request" in result.error


def test_exited_redundant_worker_does_not_override_other_active_drainer(area):
    root = area / "state"
    first = enqueue(RequestSpec("fixture"), runner=runner(area, delay=".8"), root=root)
    active = worker(area)
    redundant = None
    try:
        eventually(lambda: first.log_path.exists() and "fixture-start" in first.log_path.read_text())
        second = enqueue(RequestSpec("fixture", argv=("second",)), runner=runner(area), root=root)
        redundant = ensure_worker(
            (sys.executable, "-m", "update_driver", "worker", "--root", str(root)),
            root=root, env=environment(),
        )
        redundant.terminate()
        redundant.wait(timeout=10)
        assert wait(second.id, root=root, worker=redundant, timeout=15).exit_code == 0
    finally:
        collect(active)
        if redundant is not None and redundant.poll() is None:
            redundant.terminate()
            redundant.wait(timeout=10)
