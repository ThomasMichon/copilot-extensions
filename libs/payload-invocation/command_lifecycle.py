"""Hold legacy command ownership across runtime mutation and publication."""
from __future__ import annotations

import contextvars
import importlib.util
import json
import math
import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

if __package__:
    from agent_procutil import no_window_kwargs
else:
    def no_window_kwargs() -> dict[str, int]:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}  # headless-guard: allow stdlib-only payload helper; the runtime branch uses agent_procutil

TICKET_ENV = "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"
_CURRENT: contextvars.ContextVar[Any] = contextvars.ContextVar("command_lifecycle", default=None)


def _purpose(binding: Any, owner: Any) -> dict[str, Any]:
    purpose = owner.get("runtimeDeployment", {
        "version": 1, "origin": "plugin", "operation": "publish",
    })
    if (
        not isinstance(purpose, dict)
        or set(purpose) != {"version", "origin", "operation"}
        or type(purpose.get("version")) is not int
        or purpose["version"] != 1
        or not isinstance(purpose["origin"], str)
        or purpose["origin"] not in {"plugin", "management"}
        or not isinstance(purpose["operation"], str)
        or purpose["operation"] not in {"publish", "teardown"}
        or (purpose["origin"] == "management" and purpose["operation"] == "teardown")
    ):
        raise binding.CommandOwnershipConflict("Invalid runtime deployment lease purpose.")
    return dict(purpose)


def _publication(
    binding: Any, primitive: Any, arguments: dict[str, Any], purpose: dict[str, Any],
) -> None:
    if purpose["operation"] == "teardown":
        return
    resolver = getattr(primitive, "resolve_runtime_deployment", None)
    root = Path(arguments["legacy_root"])
    if resolver is None:
        if not os.path.lexists(root / "runtime-deployment.json"):
            return
        raise binding.CommandOwnershipConflict(
            "Runtime deployment policy requires a capable installation helper."
        )
    result = resolver(
        **{key: arguments[key] for key in (
            "payload_root", "plugin_id", "legacy_root", "project_root", "environment",
        ) if key in arguments},
        **dict(arguments.get("mode_arguments") or {}),
    )
    deployment = result["deployment"]
    actual_root = result["runtimeRoot"] or result["legacy"]["root"]
    if Path(actual_root).resolve() != root.resolve():
        raise binding.CommandOwnershipConflict("Runtime deployment policy names another root.")
    if deployment["allowPluginPublication"] is True or (
        purpose["origin"] == "management"
        and deployment["state"] == "valid"
        and deployment["managed"] is True
    ):
        return
    raise binding.CommandOwnershipConflict(
        f"Runtime publication refused: {deployment['reason']}. "
        f"{deployment['diagnostic'] or ''} {deployment['recovery'] or ''}".strip()
    )


def _owner(binding: Any, primitive: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        receipt = binding.publish_legacy_binding(primitive, **arguments)
    except binding.SourceProvenanceUnavailable:
        decision = primitive.probe_legacy_entrypoint(
            **{key: arguments[key] for key in (
                "payload_root", "plugin_id", "legacy_root", "project_root", "environment",
            ) if key in arguments},
            **dict(arguments.get("mode_arguments") or {}),
        )
        if decision["allowMutation"] is not True:
            raise binding.CommandOwnershipConflict(
                f"Unattributed legacy runtime mutation refused: {decision['probeReason']}."
            )
        # This is only a mutation reservation, not normalized source identity
        # or selectable installation ownership.
        return {
            "unattributed": True, "payloadRoot": str(Path(arguments["payload_root"]).resolve()),
            "pluginId": arguments["plugin_id"], "runtimeRoot": decision["legacy"]["root"],
            "environment": decision["environment"],
        }
    return {key: value for key, value in receipt.items() if key != "payloadRoot"}


def _reservation(
    binding: Any, primitive: Any, root: Path, owner: dict[str, Any],
) -> dict[str, Any]:
    path = root / "deploy-manifest.json"
    manifest = primitive.read_json(path) if path.exists() else {
        "schema_version": 3, "service": owner["pluginId"],
    }
    previous = manifest.get("commandLifecycle")
    if previous is not None and (
        not isinstance(previous, dict)
        or type(previous.get("version")) is not int
        or previous.get("version") != 1
        or previous.get("state") not in {"pending", "complete"}
        or previous.get("owner") != owner
        or set(previous) != {"version", "state", "owner"}
    ):
        raise binding.CommandOwnershipConflict(
            "The runtime lifecycle reservation belongs to another owner or is invalid."
        )
    return {
        **manifest, "commandLifecycle": {"version": 1, "state": "pending", "owner": owner},
    }


class LifecycleLease:
    def __init__(
        self, binding: Any, primitive: Any, arguments: dict[str, Any],
        lock: Any, owner: dict[str, Any], ticket: dict[str, Any],
    ) -> None:
        self.binding = binding
        self.primitive = primitive
        self.arguments = arguments
        self.lock = lock
        self.owner = owner
        self.ticket = ticket
        self.purpose = _purpose(binding, lock._owner())

    def assert_owned(self) -> None:
        self.lock.assert_owned()
        current = self.lock._owner()
        if (
            current["host"] != self.lock.host
            or current["pid"] != self.ticket["pid"]
            or not self.primitive._pid_is_live(current["pid"])
            or _purpose(self.binding, current) != self.purpose
            or _owner(self.binding, self.primitive, self.arguments) != self.owner
        ):
            raise self.binding.CommandOwnershipConflict("Runtime lifecycle admission changed.")
        _reservation(self.binding, self.primitive, Path(self.arguments["legacy_root"]), self.owner)
        _publication(self.binding, self.primitive, self.arguments, self.purpose)

    def assert_operation(self, operation: str) -> None:
        if self.purpose["operation"] != operation:
            raise self.binding.CommandOwnershipConflict(
                f"Runtime lifecycle lease does not authorize {operation}."
            )


def _lock(primitive: Any, arguments: dict[str, Any]) -> Any:
    root = Path(arguments["legacy_root"]).resolve()
    return primitive._DirectoryLock(
        root.with_name(root.name + ".command-binding-publication.lock"),
        kind="command-binding-publication", marketplace_id="legacy",
        plugin_id=arguments["plugin_id"],
    )


def borrowed_lease(
    binding: Any, primitive: Any, *, operation: str = "publish", **arguments: Any,
) -> LifecycleLease | None:
    """An inherited ticket is valid only against its live durable lock owner."""
    if operation not in {"publish", "teardown"}:
        raise ValueError("Runtime lifecycle operation must be publish or teardown.")
    current = _CURRENT.get()
    if current is not None:
        if Path(arguments["legacy_root"]) != Path(current.arguments["legacy_root"]):
            raise binding.CommandOwnershipConflict("Runtime lifecycle root changed.")
        if _owner(binding, primitive, arguments) != current.owner:
            raise binding.CommandOwnershipConflict("Runtime lifecycle source changed.")
        current.assert_owned()
        current.assert_operation(operation)
        return current
    raw = os.environ.get(TICKET_ENV)
    if raw is None:
        return None
    ticket = json.loads(raw)
    lock = _lock(primitive, arguments)
    if (
        not isinstance(ticket, dict)
        or set(ticket) != {"token", "pid", "host", "root", "owner"}
        or ticket["root"] != str(Path(arguments["legacy_root"]))
        or ticket["host"] != lock.host
        or type(ticket["pid"]) is not int
        or ticket["pid"] < 1
        or not isinstance(ticket["token"], str)
        or len(ticket["token"]) != 32
    ):
        raise binding.CommandOwnershipConflict("Invalid runtime lifecycle ticket.")
    owner = _owner(binding, primitive, arguments)
    if ticket["owner"] != owner:
        raise binding.CommandOwnershipConflict("Runtime lifecycle ticket names another source.")
    lock.token = ticket["token"]
    lock.acquired = True
    lease = LifecycleLease(binding, primitive, arguments, lock, owner, ticket)
    lease.assert_owned()
    lease.assert_operation(operation)
    return lease


@contextmanager
def legacy_lifecycle(
    binding: Any, primitive: Any, *, reserve: bool = True, teardown: bool = False,
    managed_deployment: bool = False, diagnostic: Any = None, **arguments: Any,
) -> Iterator[LifecycleLease]:
    """Reserve the existing manifest before the first shared-runtime write."""
    if managed_deployment and teardown:
        raise ValueError("Managed deployment cannot authorize teardown.")
    if teardown and reserve:
        raise ValueError("Teardown must not reserve an unfinished runtime build.")
    borrowed = borrowed_lease(
        binding, primitive, operation="teardown" if teardown else "publish", **arguments,
    )
    if borrowed is not None:
        if managed_deployment and borrowed.purpose["origin"] != "management":
            raise binding.CommandOwnershipConflict("Runtime deployment lease cannot change purpose.")
        yield borrowed
        if teardown:
            borrowed.lock.assert_owned()
        else:
            borrowed.assert_owned()
        return
    root = Path(arguments["legacy_root"])
    if not root.is_absolute():
        raise ValueError("Runtime lifecycle requires an absolute runtime root.")
    purpose = {
        "version": 1, "origin": "management" if managed_deployment else "plugin",
        "operation": "teardown" if teardown else "publish",
    }
    # Refuse obvious foreign ownership without creating even a lock directory;
    # admission is repeated after acquisition to close the concurrent-writer race.
    _publication(binding, primitive, arguments, purpose)
    _reservation(binding, primitive, root, _owner(binding, primitive, arguments))
    with _lock(primitive, arguments) as lock:
        owner = _owner(binding, primitive, arguments)
        manifest = _reservation(binding, primitive, root, owner)
        _publication(binding, primitive, arguments, purpose)
        primitive._atomic_write_json(
            lock.owner_path, {**lock._owner(), "runtimeDeployment": purpose}, lock=lock,
        )
        if owner.get("unattributed"):
            message = (
                "Runtime source is unavailable; legacy mutation is admitted by the "
                "canonical default-policy probe, but command ownership remains unresolved."
            )
            if diagnostic is None:
                print(message, file=sys.stderr)
            else:
                diagnostic(message)
        if reserve:
            primitive._atomic_write_json(root / "deploy-manifest.json", manifest, lock=lock)
        ticket = {
            "token": lock.token, "pid": os.getpid(), "host": lock.host,
            "root": str(root), "owner": owner,
        }
        lease = LifecycleLease(binding, primitive, arguments, lock, owner, ticket)
        token = _CURRENT.set(lease)
        try:
            yield lease
            if teardown:
                lock.assert_owned()
            else:
                lease.assert_owned()
        finally:
            _CURRENT.reset(token)


def _procutil(payload_root: Path) -> Any:
    if __package__:
        import agent_procutil
        return agent_procutil
    helper = payload_root / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"
    spec = importlib.util.spec_from_file_location("_lifecycle_procutil", helper)
    if spec is None or spec.loader is None:
        raise ValueError(f"Lifecycle containment helper is unavailable: {helper}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _human_output() -> Any:
    name = f"{__package__}._lifecycle_human" if __package__ else "_payload_lifecycle_human"
    if name in sys.modules:
        return sys.modules[name]
    helper = Path(__file__).with_name("_lifecycle_human") / "__init__.py"
    spec = importlib.util.spec_from_file_location(name, helper)
    if spec is None or spec.loader is None:
        raise ValueError(f"Lifecycle human-output helper is unavailable: {helper}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def run_lifecycle(
    binding: Any, primitive: Any, command: list[str], *,
    deferred_runtime: bool = False, teardown: bool = False,
    managed_deployment: bool = False,
    timeout: float | None = None, human_operation: str | None = None, **arguments: Any,
) -> int:
    """Keep the native installer child contained under its admitting parent."""
    if not command or not Path(command[0]).is_absolute():
        raise ValueError("A lifecycle child requires an exact absolute interpreter.")
    if managed_deployment and teardown:
        raise ValueError("Managed deployment cannot authorize teardown.")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("A lifecycle deadline must be finite and positive.")
    if timeout is not None and os.name != "nt":
        raise ValueError("A lifecycle deadline requires Windows Job containment.")
    if human_operation is not None and (human_operation not in {"install", "update"} or teardown):
        raise ValueError("Human lifecycle output requires install/update without teardown.")
    if teardown:
        root = Path(arguments["legacy_root"]).resolve()
        if any(Path(path).resolve().is_relative_to(root) for path in (sys.executable, command[0])):
            raise ValueError("Teardown requires parent and child interpreters outside the runtime.")
    with ExitStack() as stack:
        human = _human_output() if human_operation else None
        operation = None
        environment = dict(os.environ)
        if human is not None:
            from importlib import import_module

            capabilities = import_module(f"{human.__name__}.capabilities")
            target = capabilities.powershell_file_target(command)
            if target is not None:
                if not capabilities.powershell_human_utf8_supported(target):
                    raise ValueError("Lifecycle human output requires declared PowerShell UTF-8 support.")
                environment[capabilities.HUMAN_UTF8_ENV] = "1"
            operation = stack.enter_context(human.HumanOperation(
                f"{arguments['plugin_id']}/{human_operation}",
            ))
        def diagnostic(message: str) -> None:
            if operation is None:
                print(message, file=sys.stderr)
            else:
                operation.progress(message, state="warning")

        # Sample elapsed at child exit, before Job cleanup and output draining.
        # Finish cleanup and verify/release the lease before reporting completion.
        work = stack.enter_context(ExitStack())
        lease = work.enter_context(legacy_lifecycle(
            binding, primitive, reserve=not (deferred_runtime or teardown),
            teardown=teardown, managed_deployment=managed_deployment,
            diagnostic=diagnostic if operation else None, **arguments,
        ))
        environment[TICKET_ENV] = json.dumps(lease.ticket)
        output = work.enter_context(human.native_output()) if human is not None else None
        stdout = sys.stdout if output is None else output
        stderr = sys.stderr if output is None else output
        job = None
        if timeout is not None and os.name == "nt":
            child, job = _procutil(Path(arguments["payload_root"])).spawn_sync_in_kill_on_close_job(
                command, require_job=True, allow_breakaway=False,
                env=environment, stdout=stdout, stderr=stderr, **no_window_kwargs(),
            )
        else:
            child = subprocess.Popen(
                command, env=environment, stdout=stdout, stderr=stderr, **no_window_kwargs(),
            )
        while True:
            try:
                result = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                diagnostic("Runtime lifecycle deadline exceeded; stopping the admitted installer tree.")
                try:
                    job.terminate_and_wait(2)
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    diagnostic(f"Runtime lifecycle cancellation failed: {error}")
                    # Without an empty-Job witness, preserve the durable
                    # reservation even if this parent can no longer wait.
                    lease.lock.acquired = False
                    job.close()
                    child.wait()
                    raise
                child.wait()
                elapsed = operation.monotonic() - operation.started if operation is not None else None
                work.close()
                if operation is not None:
                    operation.complete("failed", "deadline exceeded (exit 124)", elapsed=elapsed)
                return 124
            except KeyboardInterrupt:
                continue
            elapsed = operation.monotonic() - operation.started if operation is not None else None
            if job is not None:
                try:
                    job.terminate_and_wait(2)
                except (OSError, RuntimeError) as error:
                    diagnostic(f"Runtime lifecycle containment cleanup failed: {error}")
                    lease.lock.acquired = False
                    job.close()
                    raise
            work.close()
            if operation is not None:
                operation.complete(
                    "done" if result == 0 else "failed", f"exit {result}", elapsed=elapsed,
                )
            return result
