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
from single_instance_lease.operation import OperationLock
from update_driver import foundation_state
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


def test_same_boot_native_timeout_retains_failure_but_allows_later_execution(area):
    from update_driver import kernel_boot_identity, recovery_status

    root = area / "state"
    before = kernel_boot_identity()
    started = area / "timed-out-product"
    request = enqueue(
        RequestSpec("fixture"),
        runner=TrustedRunner(
            (sys.executable, "-c",
             "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('started'); time.sleep(60)",
             str(started)), str(area), timeout=3,
        ),
        root=root,
    )
    process = worker(area)
    try:
        out, err = process.communicate(timeout=15)
        assert process.returncode != 0, (out, err)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert started.read_text() == "started"
    failed = wait(request.id, root=root, timeout=1)
    assert failed.state == "interrupted" and failed.exit_code is None
    assert failed.settlement_kind == "native_scope"
    assert failed.boot_id == failed.settled_boot_id == before
    assert failed.settled_at is not None and "TimeoutExpired" in failed.error
    assert recovery_status(root=root).refused == ()
    subsequent = enqueue(RequestSpec("fixture", source="next"), runner=runner(area), root=root)
    collect(worker(area))
    assert wait(subsequent.id, root=root, timeout=1).exit_code == 0
    assert status(request.id, root=root) == failed
    assert kernel_boot_identity() == before


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


@pytest.mark.parametrize("coalesce_running", [False, True])
def test_real_active_request_respects_target_admission_and_streams(area, coalesce_running):
    root = area / "state"
    first = enqueue(RequestSpec("fixture"), runner=runner(area, delay=".7", code="23"), root=root)
    process = worker(area)
    try:
        eventually(lambda: first.log_path.exists() and "fixture-start" in first.log_path.read_text())
        assert process.poll() is None
        second = enqueue(
            first.spec, runner=first.runner, root=root, coalesce_running=coalesce_running,
        )
        assert (second.id == first.id) is coalesce_running
        output = io.StringIO()
        result = wait(first.id, root=root, timeout=10, output=output)
        assert result.exit_code == 23
        assert "fixture-start" in output.getvalue() and "fixture-end" in output.getvalue()
        assert wait(second.id, root=root, timeout=10).exit_code == 23
    finally:
        collect(process)
    assert len((area / "effects").read_text().splitlines()) == (2 if coalesce_running else 4)


def test_real_active_request_queues_distinct_arrival(area):
    root = area / "state"
    first = enqueue(RequestSpec("fixture"), runner=runner(area, delay=".7"), root=root)
    process = worker(area)
    try:
        eventually(lambda: first.log_path.exists() and "fixture-start" in first.log_path.read_text())
        second = enqueue(
            RequestSpec("fixture", source="different-target"), runner=first.runner, root=root,
            coalesce_running=True,
        )
        assert second.id != first.id
        assert second.state == "pending"
        assert wait(first.id, root=root, timeout=10).exit_code == 0
        assert wait(second.id, root=root, timeout=10).exit_code == 0
    finally:
        collect(process)
    lines = (area / "effects").read_text().splitlines()
    assert len(lines) == 4
    assert all(lines[i].startswith("start ") and lines[i + 1].startswith("end ")
               for i in range(0, 4, 2))


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


def test_cross_process_enqueue_shares_running_request(area):
    root = area / "state"
    started, release = area / "started", area / "release"
    command = (
        "import pathlib,sys,time\n"
        "started,release=map(pathlib.Path,sys.argv[1:])\n"
        "started.write_text('started')\n"
        "deadline=time.monotonic()+15\n"
        "while not release.exists():\n"
        " if time.monotonic()>=deadline: raise TimeoutError('fixture release missing')\n"
        " time.sleep(.02)\n"
        "sys.exit(37)\n"
    )
    request = enqueue(
        RequestSpec("fixture"),
        runner=TrustedRunner(
            (sys.executable, "-c", command, str(started), str(release)), str(area),
            timeout=20, env=(("PYTHONPATH", environment()["PYTHONPATH"]),),
        ),
        root=root,
    )
    process = worker(area)
    clients = []
    try:
        eventually(started.exists)
        script = (
            "import sys; from pathlib import Path; from update_driver import *; "
            "root=Path(sys.argv[1]); r=status(sys.argv[2],root=root); "
            "print(enqueue(r.spec,runner=r.runner,root=root,coalesce_running=True).id)"
        )
        clients = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(root), request.id],
                env=environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **agent_procutil.no_window_kwargs(),
            )
            for _ in range(3)
        ]
        for client in clients:
            out, err = client.communicate(timeout=10)
            assert client.returncode == 0, err
            assert out.decode().strip() == request.id
        rows = status(root=root)
        assert len(rows) == 1 and rows[0].state == "running"
    finally:
        release.write_text("release")
        for client in clients:
            if client.poll() is None:
                client.communicate(timeout=10)
        collect(process)
    assert wait(request.id, root=root, timeout=1).exit_code == 37


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
    with OperationLock(root / "execution.lock") as owned:
        with pytest.raises(foundation_state.FoundationStateError, match="unsettled queue"):
            foundation_state.begin(root, "a" * 64, owned)
    assert foundation_state.read(root) is None
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


def test_real_product_exit_cannot_hide_abandoned_execution_custody(area):
    from update_driver import ParticipantError

    root = area / "state"
    script = (
        "import sys\nfrom pathlib import Path\n"
        "from update_driver import borrowed_custody\n"
        "with borrowed_custody(root=Path(sys.argv[1])) as custody:\n"
        " try:\n"
        "  with custody.execution_scope():\n"
        "   raise RuntimeError('synthetic unconfirmed cleanup')\n"
        " except RuntimeError:\n"
        "  pass\n"
        "raise SystemExit(7)\n"
    )
    first = enqueue(
        RequestSpec("uncertain"),
        runner=TrustedRunner(
            (sys.executable, "-c", script, str(root)), str(area), timeout=15,
            env=(("PYTHONPATH", environment()["PYTHONPATH"]),),
        ),
        root=root,
    )
    marker = area / "later-executed"
    later = enqueue(
        RequestSpec("later"),
        runner=TrustedRunner(
            (sys.executable, "-c", "import sys;from pathlib import Path;Path(sys.argv[1]).touch()",
             str(marker)), str(area),
        ),
        root=root,
    )
    with pytest.raises(ParticipantError):
        drain(root=root)
    failed = status(first.id, root=root)
    assert failed.state == "interrupted" and failed.exit_code is None
    assert status(later.id, root=root).state == "pending"
    assert not marker.exists()
    with pytest.raises(InterruptedError):
        drain(root=root)


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


@pytest.mark.parametrize("failure", [
    "boot", "executor",
    pytest.param("ignored-sigchld", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX signal")),
    pytest.param("custom-sigchld", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX signal")),
    pytest.param("unreaped", marks=pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="Linux waitid wrapper prerequisite",
    )),
])
def test_failed_worker_admission_never_acknowledges_startup(area, failure):
    root = area / "state"
    request = enqueue(RequestSpec("fixture"), runner=runner(area), root=root)
    reason = "boot_identity_unavailable" if failure == "boot" else "executor_unavailable"
    setup = {
        "boot": (
            "from update_driver import boot; "
            "boot.kernel_boot_identity=lambda: (_ for _ in ()).throw(OSError('synthetic')); "
        ),
        "executor": (
            "from agent_procutil import owned_process; "
            "owned_process.require_caller_owned_support="
            "lambda: (_ for _ in ()).throw(OSError('synthetic')); "
        ),
        "ignored-sigchld": "import signal; signal.signal(signal.SIGCHLD,signal.SIG_IGN); ",
        "custom-sigchld": "import signal; signal.signal(signal.SIGCHLD,lambda *_args: None); ",
        "unreaped": "import os; delattr(os,'WNOWAIT'); ",
    }[failure]
    script = (
        "import sys; from pathlib import Path; from update_driver import drain; "
        + setup +
        "drain(root=Path(sys.argv[1]))"
    )
    with pytest.raises(SpawnError, match=f"worker admission failed: {reason}"):
        ensure_worker(
            (sys.executable, "-c", script, str(root)), root=root,
            env=environment(), startup_timeout=5,
        )
    result = status(request.id, root=root)
    assert result.state == "pending" and result.started_at is None and result.boot_id is None
    kind = "OSError" if failure in ("boot", "executor") else "RuntimeError"
    assert result.error == f"worker admission failed: {reason}:{kind}"
    assert not list(root.glob(".worker-started-*"))
    with pytest.raises(SpawnError, match=reason):
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
