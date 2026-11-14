"""Runner-owned daemon leases and post-cleanup environment auditing."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys


class _NativeInstancePins:
    def __init__(self, root, limit):
        self.root = root
        self.limit = limit
        self.run_id = None
        self.processes = {}

    def bind(self, environment):
        from agent_procutil._daemon_instance_store import _test_context

        context = _test_context(environment)
        if (
            context is None or int(context["pid"]) != os.getpid()
            or Path(context["registry_root"]).resolve() != self.root
        ):
            raise ValueError("Native instance observation requires its existing lifecycle owner.")
        self.run_id = context["run_id"]

    def observe(self):
        from agent_procutil._daemon_instance_identity import _windows_birth, _windows_handle
        from agent_procutil.daemon_instances import inspect_instances

        for count, _entry in enumerate(self.root.iterdir(), 1):
            if count > self.limit * 4:
                raise RuntimeError("Native instance inventory exceeded its existing process budget.")
        for record in inspect_instances(self.root):
            key = record["instance_id"]
            if (
                record.get("run_id") != self.run_id or key in self.processes
                or not record.get("pid") or not record.get("birth")
            ):
                continue
            if len(self.processes) >= self.limit:
                raise RuntimeError("Native instance handle retention exceeded its existing process budget.")
            process = None
            try:
                process = _windows_handle(int(record["pid"]))
                if _windows_birth(process._handle) != record["birth"]:
                    continue
                self.processes[key] = process
                process = None
            except (OSError, ValueError):
                continue
            finally:
                if process is not None:
                    process.close()

    def close(self):
        error = None
        for process in reversed(tuple(self.processes.values())):
            try:
                process.close()
            except OSError as failure:
                error = failure
        self.processes.clear()
        if error is not None:
            raise error


@contextmanager
def _native_instance_pins(selected, sandbox, limit):
    pins = _NativeInstancePins(sandbox.resolve() / "daemon-instances", limit) if (
        selected and os.name == "nt"
    ) else None
    try:
        yield pins
    finally:
        if pins is not None:
            pins.close()


def _native_scope_environment(environment):
    if sys.platform != "linux":
        return environment
    from agent_procutil.linux_daemon_scope import caller_unit

    if caller_unit() is None:
        return environment
    uid = os.getuid()
    bus = Path(f"/run/user/{uid}/bus")
    try:
        info = bus.lstat()
    except OSError:
        raise ValueError("Native application testing requires an existing user-manager bus.") from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
        raise ValueError("Native application user-manager bus is not owned by the current user.")
    # Keep XDG_RUNTIME_DIR and application state private to the sandbox.
    return {**environment, "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(bus)}


def run_contained(
    owner, command, *, cwd, env, sandbox, limits, daemon_purpose,
    daemon_lease, qualification, native_application,
):
    # Keep the original owner's native operations and instrumentation seams.
    if type(native_application) is not bool:
        raise ValueError("native application testing must be explicitly selected")
    if native_application:
        temporary = owner.os.environ.get("RUNNER_TEMP")
        if (
            qualification is not None
            or owner.os.environ.get("GITHUB_ACTIONS") != "true"
            or owner.os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or not temporary or not owner.Path(temporary).is_absolute()
            or not sandbox.resolve().is_relative_to(owner.Path(temporary).resolve())
            or owner.os.environ.get(owner.CONTAINED_ENV) == "1"
        ):
            raise owner.ContainmentError("Native application testing requires its disposable hosted CI owner.")
        env = dict(env)
        env.pop(owner.CONTAINED_ENV, None)
        env = _native_scope_environment(env)
    lifecycle_results = []
    if qualification is not None:
        if owner.os.name != "nt":
            raise owner.QualificationError("Absolute native qualification requires the original Windows Job owner.")
        if limits != owner.Limits(wall_seconds=owner.WORK_SECONDS):
            raise owner.QualificationError(
                "Native qualification must preserve the original process, memory and poll limits."
            )
        qualification.validate(sandbox, owner.time.monotonic)
    environment_snapshot = owner._read_registry_environment()
    if qualification is not None:
        qualification.boundary.require(owner.time.monotonic, work=True)
    unreaped = None
    try:
        execution_error = None
        controller_error = None
        teardown_complete = False
        try:
            with _native_instance_pins(
                native_application, sandbox, limits.max_processes,
            ) as pins, owner.daemon_test_lifecycle(
                owner=f"contained-test-runner:{owner.os.getpid()}",
                registry_root=sandbox.resolve() / "daemon-instances",
                purpose=daemon_purpose,
                lease=daemon_lease,
                **(
                    {"deadline": qualification.boundary.whole_deadline, "cleanup_results": lifecycle_results}
                    if qualification is not None else {"cleanup_results": lifecycle_results}
                ),
            ) as daemon_environment:
                if pins is not None:
                    pins.bind(daemon_environment)
                try:
                    result = owner._run_contained_process(
                        command, cwd=cwd, env={**env, **daemon_environment},
                        sandbox=sandbox, limits=limits,
                        **({"qualification": qualification} if qualification is not None else {}),
                        **({"native_application": True} if native_application else {}),
                        **({"native_observer": pins.observe} if pins is not None else {}),
                    )
                except BaseException as error:
                    controller_error = error
                    raise
            teardown_complete = True
        except BaseException as error:
            teardown_complete = error is controller_error
            execution_error = error
            raise
        finally:
            if (
                not teardown_complete
                or any(outcome.status not in ("settled", "missing") for outcome in lifecycle_results)
            ):
                raise owner.UnreapedProcessError(
                    "Test daemon cleanup remains unsettled."
                ) from execution_error
        if qualification is not None:
            qualification.boundary.require(owner.time.monotonic)
            qualification.lifecycle_settled = all(
                outcome.status in ("settled", "missing") for outcome in lifecycle_results
            )
    except owner.UnreapedProcessError as exc:
        unreaped = exc
        raise
    finally:
        try:
            if qualification is not None:
                qualification.boundary.require(owner.time.monotonic)
            changed = owner._registry_environment_drift(environment_snapshot)
            if changed:
                print(
                    "[HOST-STATE] contained test changed persistent Windows "
                    f"environment state; detected without rollback: {', '.join(changed)}",
                    file=owner.sys.stderr,
                )
        except BaseException as audit_error:
            if unreaped is None:
                raise
            raise owner.UnreapedProcessError(
                f"{unreaped}; environment audit also failed: "
                f"{type(audit_error).__name__}: {audit_error}", job=unreaped.job,
            ) from unreaped
    if changed:
        return 125
    if qualification is not None:
        qualification.registry_unchanged = True
        qualification.require_settled(owner.time.monotonic)
    return result
