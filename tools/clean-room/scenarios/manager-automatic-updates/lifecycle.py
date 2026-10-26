"""Actual process/lease/health witnesses using unchanged production workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

import fixtures
from identity import ProcessPin, read_event, until
import native


def held(root: Path, kind: str) -> bool:
    from zdd.cutover_lock import CutoverLock, CutoverLockedError

    lease = CutoverLock(root / "automatic-updates" / kind)
    try:
        lease.acquire()
    except CutoverLockedError:
        return True
    lease.release()
    return False


def ready(process: Any, root: Path, *, version: str | None = None, timeout: float = 35) -> dict:
    return until(
        lambda: process.worker_status(root),
        lambda status: status.get("running") is True and (
            version is None or status.get("runtime") == str(root / "versions" / version)
        ),
        timeout=timeout, label="actual worker health/readiness",
    )


def summarize(status: dict) -> dict:
    return {
        key: status[key] for key in ("worker_id", "pid", "state", "last_exit_code", "next_due")
        if key in status
    }


def pin_worker(root: Path, status: dict, pins: list[ProcessPin], observer: Any = None) -> None:
    pins.append(ProcessPin(status["pid"]))
    if observer is not None:
        observer.add_pid(status["pid"])
    if os.name == "nt":
        return
    from worktree_manager.automatic_updates_startup import process_snapshot

    identity = process_snapshot(status["pid"])
    command = identity[2]
    slot = Path(status["runtime"])
    if slot.parent != root / "versions" or not worker_command_matches(root, slot, command):
        raise AssertionError("health PID does not execute this fixture's worker argv")


def worker_command_matches(root: Path, slot: Path, command: tuple[str, ...]) -> bool:
    import agent_procutil
    from agent_procutil._daemon_instance_child import leased_python_command

    expected = [
        str(slot / "bin/python"), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap/runtime_launcher.py"),
        "-m", "worktree_manager", "automatic-updates", "run", "--root", str(root),
    ]
    leased, _environment = leased_python_command(expected)
    helper_index = leased.index("-c") + 2
    libraries = [
        Path(agent_procutil.__file__).resolve().parent.parent,
        *(root / "versions" / version / "payload/libs/agent-procutil/src"
          for version in (fixtures.OLD, fixtures.NEW)),
    ]
    candidates = [expected, *(
        [*leased[:helper_index], str(library), *leased[helper_index + 1:]]
        for library in libraries
    )]
    for candidate in candidates:
        if tuple(command[1:len(candidate)]) != tuple(candidate[1:]):
            continue
        tail = command[len(candidate):]
        if not tail or (
            len(tail) == 2 and tail[0] == "--standby"
            and len(tail[1]) == 32 and all(char in "0123456789abcdef" for char in tail[1])
        ):
            return True
    return False


def launcher(root: Path, env: dict[str, str]) -> subprocess.Popen:
    from agent_procutil.daemon_instances import DaemonInstanceSpec, spawn_instance

    output = (root / "probe" / "launcher.log").open("ab")
    try:
        return spawn_instance(
            fixtures.command(root, ["automatic-updates", "run", "--root", str(root)]),
            instance=DaemonInstanceSpec(
                "worktree-manager-automatic-updates", root, Path(__file__).resolve(),
            ),
            cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    finally:
        output.close()


def cleanup(
    root: Path, manager: dict[str, Any], env: dict[str, str],
    pins: list[ProcessPin], handles: list[subprocess.Popen], unit: str | None,
) -> None:
    (root / "probe" / "release").touch()
    # The public guard may be held by the finite command; release it before disable.
    until(lambda: held(root, "update-transaction"), lambda busy: not busy,
          timeout=65, label="fixture update drain before policy disable")
    manager["automatic_updates_policy"].configure(root, owner="disabled", provenance="user")
    until(lambda: manager["automatic_updates_process"].worker_status(root),
          lambda status: status.get("running") is False, timeout=25, label="disabled worker exit")
    for pin in pins:
        until(pin.exited, bool, timeout=15, label="pinned fixture process exit")
    for handle in handles:
        handle.wait(timeout=20)
    if held(root, "worker-lease") or held(root, "update-transaction"):
        raise AssertionError("fixture cleanup left canonical custody held")
    if unit is not None:
        native.retire(root, unit)


def case(
    source: Path, root: Path, manager: dict[str, Any], *, cycles: int,
    failing_standby: bool = False, native_startup: bool = False,
    windows_observer: Any = None,
    instance_environment: dict[str, str] | None = None,
) -> dict:
    metadata = fixtures.build(source, root, manager, failing_standby=failing_standby)
    env = fixtures.environment(root, native=native_startup and os.name != "nt")
    env.update(instance_environment or {})
    fixtures.warm(root, env)
    process = manager["automatic_updates_process"]
    pins: list[ProcessPin] = []
    handles: list[subprocess.Popen] = []
    unit = None
    windows_session = None
    result: dict[str, Any] = {
        "case": "failed-successor" if failing_standby else "in-flight-handover",
        "production_runtime_hashes": metadata["runtime_hashes"],
        "resolution_environment": metadata["resolution_environment"],
        "cycle_kind": "finite-guarded-command-fixture", "real_installation_cycles": False,
        "fixture_project": "fixture", "registered_project_qualification": False,
        "cadence_seconds": 300, "requested_cycles": cycles,
        "native_startup": native_startup, "cleanup_verified": False,
    }
    try:
        if native_startup and os.name == "nt":
            from windows_native import StartupSession

            if windows_observer is None:
                raise RuntimeError("Windows native startup requires continuous desktop observation")
            windows_session = StartupSession(root, env, windows_observer)
            windows_session.start()
        elif native_startup:
            unit = native.unit_name(root)
            native.registration(root)
        else:
            handles.append(launcher(root, env))
        old = ready(process, root, version=fixtures.OLD)
        if old.get("self_cycle_enabled") is not True:
            raise AssertionError("fixture worker's supported self-upgrade policy is disabled")
        pin_worker(root, old, pins, windows_observer)
        old_pin = pins[0]
        if ready(process, root)["worker_id"] != old["worker_id"]:
            raise AssertionError("worker changed while pinning its process identity")
        result["old"] = summarize(old)
        if windows_session is not None:
            result["windows_job_lifetime"] = windows_session.verify_job_close(old, process)
        native_before = native.assert_mainpid(unit, old, root) if unit else None
        first_begin = until(
            lambda: read_event(root / "probe/cycle-001-begin.json"), bool,
            timeout=25, label="first guarded child active",
        )
        if (
            first_begin["version"] != fixtures.OLD
            or Path(first_begin["runtime"]) != root / "versions" / fixtures.OLD
        ):
            raise AssertionError("first cycle did not execute the original retained runtime")
        old = ready(process, root, version=fixtures.OLD)
        if old["state"] != "updating" or not held(root, "update-transaction"):
            raise AssertionError("finite command does not own the canonical update transaction")
        if not held(root, "worker-lease"):
            raise AssertionError("worker does not own canonical scheduling custody")

        duplicate = launcher(root, env)
        handles.append(duplicate)
        if duplicate.wait(timeout=20) != 0:
            raise AssertionError("duplicate worker did not stand down cleanly")
        if ready(process, root)["worker_id"] != old["worker_id"]:
            raise AssertionError("duplicate worker displaced the active owner")
        conflict = subprocess.run(
            fixtures.command(root, [
                "update", "--automatic-trigger", "external", "--controller", "fixture-other-owner",
                "--project", "fixture",
                "--detach", "--json",
            ]),
            env=env, cwd=root, capture_output=True, text=True, timeout=20, check=False,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if conflict.returncode:
            raise AssertionError(f"concurrent canonical request failed: {conflict.stderr}")
        request = json.loads(conflict.stdout)
        if request["state"] != "pending" or request["exit_code"] is not None:
            raise AssertionError("concurrent request was not queued behind the active update")
        if (root / "probe/cycle-002-begin.json").exists():
            raise AssertionError("queued request started a competing installation")
        result["duplicate_stood_down"] = True
        result["concurrent_update_exit"] = conflict.returncode
        result["concurrent_request_state"] = request["state"]
        (root / "current-version").write_text(fixtures.NEW, encoding="utf-8")
        observed = ready(process, root)
        if (
            observed["worker_id"] != old["worker_id"] or observed["state"] != "updating"
            or (root / "probe/cycle-001-end.json").exists()
        ):
            raise AssertionError("selection advance disrupted an active guarded command")
        result["health_responsive_during_update"] = True
        (root / "probe/release").touch()
        until(lambda: read_event(root / "probe/cycle-001-end.json"), bool,
              timeout=20, label="old command completed before replacement")
        from update_driver import status as request_status

        request_root = Path(request["log_path"]).parent
        if not request_root.is_relative_to(root):
            raise AssertionError("concurrent request escaped the private fixture")
        refused = until(
            lambda: request_status(request["request_id"], root=request_root),
            lambda value: value.state == "completed",
            timeout=20, label="queued foreign controller refused under execution-time policy",
        )
        if refused.exit_code != 3 or (root / "probe/cycle-002-begin.json").exists():
            raise AssertionError("foreign controller bypassed the active maintenance owner")
        result["concurrent_ownership_exit"] = refused.exit_code
        result["no_concurrent_installation"] = True

        if failing_standby:
            retained = until(
                lambda: process.worker_status(root),
                lambda status: status.get("running") is True
                and status.get("state") == "replacement-deferred",
                timeout=35, label="failed candidate preserves healthy old controller",
            )
            if retained["worker_id"] != old["worker_id"] or old_pin.exited():
                raise AssertionError("failed successor replaced or killed its predecessor")
            if not held(root, "worker-lease"):
                raise AssertionError("failed successor lost scheduling custody")
            if unit:
                native_after = native.assert_mainpid(unit, retained, root)
                if native_after["NRestarts"] != native_before["NRestarts"]:
                    raise AssertionError("failed native successor retired its healthy predecessor")
                result["native_failed_successor_preserved_mainpid"] = True
            result["failed_candidate_preserved_owner"] = True
        else:
            successor = ready(
                process, root, version=fixtures.NEW, timeout=110 if native_startup else 40,
            )
            pin_worker(root, successor, pins, windows_observer)
            if successor["worker_id"] == old["worker_id"] or not held(root, "worker-lease"):
                raise AssertionError("new source did not acquire exclusive scheduling custody")
            until(old_pin.exited, bool, timeout=15, label="old worker retired after handover")
            result["new"] = summarize(successor)
            result["old_process_exit_verified"] = True
            if unit:
                native_after = native.assert_mainpid(unit, successor, root)
                if int(native_after["NRestarts"]) <= int(native_before["NRestarts"]):
                    raise AssertionError("native worker did not restart through its failure sentinel")
                result["native_mainpid_direct"] = True
                result["native_restart_observed"] = True
            if cycles == 2:
                until(lambda: read_event(root / "probe/cycle-002-end.json"), bool,
                      timeout=340, label="second genuine minimum-cadence background cycle")
                begin = read_event(root / "probe/cycle-002-begin.json")
                first_end = read_event(root / "probe/cycle-001-end.json")
                if (
                    begin["version"] != fixtures.NEW
                    or Path(begin["runtime"]) != root / "versions" / fixtures.NEW
                ):
                    raise AssertionError("second cycle did not use the advanced runtime")
                spacing = begin["monotonic"] - first_end["monotonic"]
                if spacing < 300:
                    raise AssertionError("minimum real cadence was shortened")
                result["observed_cycle_spacing_seconds"] = spacing
                result["cadence_observed"] = True
            else:
                result["cadence_observed"] = False
        result["observed_cycles"] = len(list((root / "probe").glob("cycle-*-end.json")))
        if (root / "probe/active-command").exists():
            raise AssertionError("unexpected extra active cycle during qualification")
        if any(
            fixtures.runtime_hashes(root / "versions" / version / "payload") != metadata["runtime_hashes"]
            for version in (fixtures.OLD, fixtures.NEW)
        ):
            raise AssertionError("qualification changed immutable production worker modules")
        result["passed"] = True
        return result
    except BaseException as exc:
        result.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            if windows_session is not None:
                try:
                    windows_session.close_job()
                    cleanup(root, manager, env, pins, handles, unit)
                finally:
                    windows_session.retire()
            else:
                cleanup(root, manager, env, pins, handles, unit)
            result["cleanup_verified"] = True
        except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as exc:
            result.update(passed=False, cleanup_error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            for pin in pins:
                pin.close()
            (root / "probe/result.json").write_text(
                json.dumps(result, sort_keys=True, indent=2), encoding="utf-8",
            )


def signal_drain_case(
    source: Path, root: Path, manager: dict[str, Any], *,
    instance_environment: dict[str, str] | None = None,
) -> dict:
    fixtures.build(source, root, manager)
    env = fixtures.environment(root, native=True)
    env.update(instance_environment or {})
    fixtures.warm(root, env)
    process = manager["automatic_updates_process"]
    pins: list[ProcessPin] = []
    unit = native.unit_name(root)
    result: dict[str, Any] = {
        "case": "native-sigterm-drain", "passed": False, "cleanup_verified": False,
        "cycle_kind": "finite-guarded-command-fixture", "real_installation_cycles": False,
    }
    try:
        native.registration(root)
        worker = ready(process, root, version=fixtures.OLD)
        pin_worker(root, worker, pins)
        before = native.assert_mainpid(unit, worker, root)
        until(lambda: read_event(root / "probe/cycle-001-begin.json"), bool,
              timeout=25, label="native child active before pinned SIGTERM")
        if not held(root, "update-transaction"):
            raise AssertionError("native signal fixture has no active guarded command")
        pins[0].request_term()
        until(
            lambda: process.worker_status(root),
            lambda status: status.get("running") is True and status.get("state") == "draining-update",
            timeout=10, label="real SIGTERM observed cooperatively",
        )
        if pins[0].exited() or not held(root, "update-transaction"):
            raise AssertionError("SIGTERM terminated the worker or its in-flight command")
        (root / "probe/release").touch()
        until(lambda: read_event(root / "probe/cycle-001-end.json"), bool,
              timeout=20, label="signal-drained child completion")
        until(pins[0].exited, bool, timeout=15, label="cooperative native worker exit")
        after = native.fields(unit)
        if int(after["NRestarts"]) != int(before["NRestarts"]):
            raise AssertionError("cooperative signal stop unexpectedly restarted the worker")
        result.update(passed=True, sigterm_drained_active_cycle=True, restart_suppressed=True)
        return result
    finally:
        try:
            cleanup(root, manager, env, pins, [], unit)
            result["cleanup_verified"] = True
        except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as exc:
            result.update(passed=False, cleanup_error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            for pin in pins:
                pin.close()
            (root / "probe/result.json").write_text(
                json.dumps(result, sort_keys=True, indent=2), encoding="utf-8",
            )
