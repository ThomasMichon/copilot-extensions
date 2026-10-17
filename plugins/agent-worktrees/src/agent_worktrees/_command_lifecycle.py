"""Hold legacy command ownership across runtime mutation and publication."""
from __future__ import annotations

import contextvars
import importlib.util
import json
import math
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

if __package__:
    from agent_procutil import no_window_kwargs
else:
    def no_window_kwargs() -> dict[str, int]:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}  # headless-guard: allow stdlib-only payload helper; the runtime branch uses agent_procutil

TICKET_ENV = "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"
_CURRENT: contextvars.ContextVar[Any] = contextvars.ContextVar("command_lifecycle", default=None)


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

    def assert_owned(self) -> None:
        self.lock.assert_owned()
        current = self.lock._owner()
        if (
            current["host"] != self.lock.host
            or current["pid"] != self.ticket["pid"]
            or not self.primitive._pid_is_live(current["pid"])
            or _owner(self.binding, self.primitive, self.arguments) != self.owner
        ):
            raise self.binding.CommandOwnershipConflict("Runtime lifecycle admission changed.")
        _reservation(self.binding, self.primitive, Path(self.arguments["legacy_root"]), self.owner)


def _lock(primitive: Any, arguments: dict[str, Any]) -> Any:
    root = Path(arguments["legacy_root"]).resolve()
    return primitive._DirectoryLock(
        root.with_name(root.name + ".command-binding-publication.lock"),
        kind="command-binding-publication", marketplace_id="legacy",
        plugin_id=arguments["plugin_id"],
    )


def borrowed_lease(binding: Any, primitive: Any, **arguments: Any) -> LifecycleLease | None:
    """An inherited ticket is valid only against its live durable lock owner."""
    current = _CURRENT.get()
    if current is not None:
        if Path(arguments["legacy_root"]) != Path(current.arguments["legacy_root"]):
            raise binding.CommandOwnershipConflict("Runtime lifecycle root changed.")
        if _owner(binding, primitive, arguments) != current.owner:
            raise binding.CommandOwnershipConflict("Runtime lifecycle source changed.")
        current.assert_owned()
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
    return lease


@contextmanager
def legacy_lifecycle(
    binding: Any, primitive: Any, *, reserve: bool = True, teardown: bool = False,
    **arguments: Any,
) -> Iterator[LifecycleLease]:
    """Reserve the existing manifest before the first shared-runtime write."""
    borrowed = borrowed_lease(binding, primitive, **arguments)
    if borrowed is not None:
        yield borrowed
        if teardown:
            borrowed.lock.assert_owned()
        else:
            borrowed.assert_owned()
        return
    root = Path(arguments["legacy_root"])
    if not root.is_absolute():
        raise ValueError("Runtime lifecycle requires an absolute runtime root.")
    if teardown and reserve:
        raise ValueError("Teardown must not reserve an unfinished runtime build.")
    # Refuse obvious foreign ownership without creating even a lock directory;
    # admission is repeated after acquisition to close the concurrent-writer race.
    _reservation(binding, primitive, root, _owner(binding, primitive, arguments))
    with _lock(primitive, arguments) as lock:
        owner = _owner(binding, primitive, arguments)
        manifest = _reservation(binding, primitive, root, owner)
        if owner.get("unattributed"):
            print(
                "Runtime source is unavailable; legacy mutation is admitted by the "
                "canonical default-policy probe, but command ownership remains unresolved.",
                file=sys.stderr,
            )
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


def run_lifecycle(
    binding: Any, primitive: Any, command: list[str], *,
    deferred_runtime: bool = False, teardown: bool = False,
    timeout: float | None = None, **arguments: Any,
) -> int:
    """Keep the native installer child contained under its admitting parent."""
    if not command or not Path(command[0]).is_absolute():
        raise ValueError("A lifecycle child requires an exact absolute interpreter.")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("A lifecycle deadline must be finite and positive.")
    if timeout is not None and os.name != "nt":
        raise ValueError("A lifecycle deadline requires Windows Job containment.")
    if teardown:
        root = Path(arguments["legacy_root"]).resolve()
        if any(Path(path).resolve().is_relative_to(root) for path in (sys.executable, command[0])):
            raise ValueError("Teardown requires parent and child interpreters outside the runtime.")
    with legacy_lifecycle(
        binding, primitive, reserve=not (deferred_runtime or teardown),
        teardown=teardown, **arguments,
    ) as lease:
        environment = {**os.environ, TICKET_ENV: json.dumps(lease.ticket)}
        job = None
        if timeout is not None and os.name == "nt":
            child, job = _procutil(Path(arguments["payload_root"])).spawn_sync_in_kill_on_close_job(
                command, require_job=True, allow_breakaway=False,
                env=environment, stdout=sys.stdout, stderr=sys.stderr, **no_window_kwargs(),
            )
        else:
            child = subprocess.Popen(
                command, env=environment, stdout=sys.stdout, stderr=sys.stderr, **no_window_kwargs(),
            )
        while True:
            try:
                result = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                print(
                    "Runtime lifecycle deadline exceeded; stopping the admitted installer tree.",
                    file=sys.stderr,
                )
                try:
                    job.terminate_and_wait(2)
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    print(f"Runtime lifecycle cancellation failed: {error}", file=sys.stderr)
                    # Without an empty-Job witness, preserve the durable
                    # reservation even if this parent can no longer wait.
                    lease.lock.acquired = False
                    job.close()
                    child.wait()
                    raise
                child.wait()
                return 124
            except KeyboardInterrupt:
                continue
            if job is not None:
                try:
                    job.terminate_and_wait(2)
                except (OSError, RuntimeError) as error:
                    print(f"Runtime lifecycle containment cleanup failed: {error}", file=sys.stderr)
                    lease.lock.acquired = False
                    job.close()
                    raise
            return result
