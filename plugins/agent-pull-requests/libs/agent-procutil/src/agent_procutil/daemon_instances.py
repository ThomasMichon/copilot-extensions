"""Service-independent daemon instance custody.

The registry is local filesystem state, not a service or an election protocol.
Production instances have no expiry. Disposable test lifetime is explicit.
"""

from __future__ import annotations

import math
import os
import functools
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Purpose = Literal["production", "unattended-test", "interactive-test"]
INSTANCE_SCHEMA = 1
INSTANCE_ENV = "COPILOT_EXTENSIONS_DAEMON_INSTANCE"
REGISTRY_ENV = "COPILOT_EXTENSIONS_DAEMON_REGISTRY"
TEST_CONTEXT_ENV = "COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT"


@dataclass(frozen=True)
class DaemonLeasePolicy:
    """Renewal is issued by the test lifecycle owner, never by the daemon."""

    ttl_seconds: float = 600
    max_lifetime_seconds: float = 14400

    def __post_init__(self) -> None:
        for value in (self.ttl_seconds, self.max_lifetime_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("daemon lease limits must be finite and positive")
        if self.ttl_seconds > self.max_lifetime_seconds:
            raise ValueError("daemon lease ttl exceeds its hard maximum")


@dataclass(frozen=True)
class DaemonInstanceSpec:
    service: str
    runtime_root: Path
    source: Path
    purpose: Purpose = "production"
    owner: str | None = None
    registry_root: Path | None = None
    lease: DaemonLeasePolicy | None = None
    lifecycle: Literal["resident", "finite-worker"] = "resident"

    def __post_init__(self) -> None:
        if not self.service or len(self.service) > 200 or any(
            ord(c) < 32 for c in self.service
        ):
            raise ValueError("daemon service must be a bounded printable name")
        if self.owner is not None and (
            not self.owner or len(self.owner) > 200 or any(ord(c) < 32 for c in self.owner)
        ):
            raise ValueError("daemon owner must be a bounded printable name")
        if self.purpose not in ("production", "unattended-test", "interactive-test"):
            raise ValueError("daemon purpose must be explicit")
        if self.lifecycle not in ("resident", "finite-worker"):
            raise ValueError("unknown instance lifecycle")
        if self.purpose == "production" and self.lease is not None:
            raise ValueError("production instances do not have an expiry policy")
        if self.purpose == "interactive-test" and self.lease is None:
            raise ValueError("interactive tests require an explicit pause-tolerant lease")
        if self.purpose == "unattended-test" and self.lease is None:
            object.__setattr__(self, "lease", DaemonLeasePolicy())
        for name in ("runtime_root", "source", "registry_root"):
            value = getattr(self, name)
            if value is not None:
                path = Path(value)
                if not path.is_absolute():
                    raise ValueError(f"daemon {name} must be absolute")
                object.__setattr__(self, name, path)


@dataclass(frozen=True)
class DaemonInstanceReceipt:
    instance_id: str
    registry_root: Path

    def __post_init__(self) -> None:
        if len(self.instance_id) != 32 or any(c not in "0123456789abcdef" for c in self.instance_id):
            raise ValueError("invalid daemon instance id")
        root = Path(self.registry_root)
        if not root.is_absolute():
            raise ValueError("daemon instance registry must be absolute")
        object.__setattr__(self, "registry_root", root)

    @property
    def record_path(self) -> Path:
        return self.registry_root / (self.instance_id + ".instance")


@dataclass(frozen=True)
class InstanceCleanupResult:
    instance_id: str
    status: Literal["settled", "held", "live", "missing"]
    reason: str
    removed: tuple[str, ...] = field(default_factory=tuple)


def default_registry_root() -> Path:
    """Resolve the actual OS user profile, independent of isolated HOME roots."""
    from ._daemon_instance_store import registry_root

    return registry_root()


def reserve_instance(spec: DaemonInstanceSpec, *, environment=None) -> DaemonInstanceReceipt:
    from ._daemon_instance_store import reserve

    return reserve(spec, environment=environment)


def instance_environment(receipt: DaemonInstanceReceipt) -> dict[str, str]:
    environment = {
        INSTANCE_ENV: receipt.instance_id,
        REGISTRY_ENV: str(receipt.registry_root),
    }
    return environment


def bind_instance(receipt: DaemonInstanceReceipt, pid: int) -> None:
    """Bind an observed actual child, before returning or admitting its work."""
    from ._daemon_instance_store import bind_process

    bind_process(receipt, pid)


def cancel_instance_reservation(
    receipt: DaemonInstanceReceipt, *, creation_refused: bool,
) -> None:
    """Settle a positively refused creation, never a lost/unknown spawn result."""
    from ._daemon_instance_store import cancel_reservation

    cancel_reservation(receipt, creation_refused)


def spawn_instance(argv, *, instance: DaemonInstanceSpec, popen=None, **kwargs):
    """Track a spawn without changing its existing stdio or containment policy."""
    import subprocess
    from ._daemon_instance_store import _test_context

    command = [os.fspath(part) for part in argv]
    environment = kwargs.pop("env", None)
    environment = dict(os.environ if environment is None else environment)
    if not environment.get(TEST_CONTEXT_ENV) and os.environ.get(TEST_CONTEXT_ENV):
        environment[TEST_CONTEXT_ENV] = os.environ[TEST_CONTEXT_ENV]
    python_environment = {}
    if (
        instance.lease is not None or instance.lifecycle == "finite-worker"
        or _test_context(environment) is not None
    ):
        from ._daemon_instance_child import leased_python_command
        command, python_environment = leased_python_command(command)
    else:
        from ._daemon_instance_child import normalize_python_command
        command, python_environment = normalize_python_command(command)
    receipt = reserve_instance(instance, environment=environment)
    environment.update(python_environment)
    environment.update(instance_environment(receipt))
    try:
        process = (popen or subprocess.Popen)(command, env=environment, **kwargs)
    except OSError:
        cancel_instance_reservation(receipt, creation_refused=True)
        raise
    from ._daemon_instance_identity import created_process_identity
    identity = created_process_identity(process)
    try:
        if identity is None:
            bind_instance(receipt, process.pid)
        else:
            from ._daemon_instance_store import bind_process
            bind_process(receipt, process.pid, observed_birth=identity[1])
    except BaseException as error:
        # The process object owns the actual child; do not reconstruct a PID.
        exited = False
        try:
            process.kill()
            process.wait(timeout=5)
            exited = True
        except (OSError, subprocess.SubprocessError):
            pass
        from ._daemon_instance_store import finish_failed_launch
        try:
            finish_failed_launch(
                receipt, identity, exit_confirmed=exited, admission_possible=True,
            )
        except (OSError, ValueError, KeyError):
            pass
        from . import DaemonSpawnError
        failure = DaemonSpawnError(
            "instance commit failed", cleanup_confirmed=exited,
            process_created=True, process_identity=identity,
        )
        failure.instance_receipt = receipt
        raise failure from error
    process.instance_receipt = receipt
    return process


def spawn_finite_worker(argv, *, instance: DaemonInstanceSpec, **kwargs):
    """Finite Python work with normal daemon escape/stdio policy, not a TTL."""
    from dataclasses import replace
    from . import spawn_daemon

    return spawn_daemon(argv, instance=replace(instance, lifecycle="finite-worker"), **kwargs)


def admit_daemon_instance(
    service: str,
    *,
    runtime_root: str | os.PathLike[str] | None = None,
    source: str | os.PathLike[str] | None = None,
    purpose: Purpose | None = None,
    owner: str | None = None,
    registry_root: str | os.PathLike[str] | None = None,
    lease: DaemonLeasePolicy | None = None,
) -> DaemonInstanceGuard:
    """Admit once, before endpoints or application work; retain the guard."""
    from ._daemon_instance_store import admit

    return admit(
        service, runtime_root=runtime_root, source=source, purpose=purpose,
        owner=owner, registry_root=registry_root, lease=lease,
    )


def renew_instance(receipt: DaemonInstanceReceipt) -> None:
    """Renew from the exact launching process; expiry and hard cap cannot revive."""
    from ._daemon_instance_store import renew

    renew(receipt)


def inspect_instances(registry_root: Path | None = None) -> list[dict[str, str]]:
    from ._daemon_instance_store import inspect

    return inspect(registry_root)


def cleanup_instance(
    receipt: DaemonInstanceReceipt, *, terminate: bool = True,
) -> InstanceCleanupResult:
    """Stop only expired disposable instances, then settle exact owned artifacts."""
    from ._daemon_instance_store import cleanup

    return cleanup(receipt, terminate=terminate)


def reconcile_instances(
    *, registry_root: Path | None = None, limit: int = 128, max_seconds: float = 5,
) -> list[InstanceCleanupResult]:
    """Bounded explicit installer backstop; never called by import or discovery."""
    from ._daemon_instance_store import reconcile

    return reconcile(registry_root, limit, max_seconds)


def daemon_entrypoint(service: str, *, source, runtime_root=None, purpose: Purpose | None = None):
    """Retain admission across a synchronous or asynchronous service callable.

    ``runtime_root`` may be a literal path or a resolver with the decorated
    callable's arguments. Source defaults never classify a test as disposable.
    """
    def decorate(function):
        def admission(args, kwargs):
            from ._daemon_instance_store import require_declaration
            require_declaration(purpose)
            root = runtime_root(*args, **kwargs) if callable(runtime_root) else runtime_root
            return admit_daemon_instance(
                service, source=source, runtime_root=root, purpose=purpose,
            )

        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                with admission(args, kwargs):
                    return await function(*args, **kwargs)
            return asynchronous

        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            with admission(args, kwargs):
                return function(*args, **kwargs)
        return synchronous
    return decorate


def daemon_test_lifecycle(
    *, owner: str, registry_root: Path, purpose: Purpose = "unattended-test",
    lease: DaemonLeasePolicy | None = None,
):
    """Context yielding an explicit test environment, renewed by this owner.

    The caller layers the yielded mapping into the actual test process's
    environment. No ambient environment is changed by entering this context.
    """
    from ._daemon_instance_store import test_lifecycle

    return test_lifecycle(owner, Path(registry_root), purpose, lease)


class DaemonInstanceGuard:
    """Current-process admission and local expiry observation."""

    def __init__(self, receipt: DaemonInstanceReceipt) -> None:
        self.receipt = receipt
        self._closed = False

    def add_artifact(self, path: str | os.PathLike[str]) -> None:
        """Declare one already-created lifecycle file, never a recursive root."""
        from ._daemon_instance_store import add_artifact

        add_artifact(self.receipt, Path(path))

    def close(self) -> None:
        from ._daemon_instance_store import close_guard

        if not self._closed:
            close_guard(self.receipt)
            self._closed = True

    def __enter__(self) -> DaemonInstanceGuard:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


if __name__ == "__main__":
    import argparse
    import dataclasses
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("reconcile", "inspect"))
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--limit", type=int, default=128)
    parser.add_argument("--max-seconds", type=float, default=5)
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(inspect_instances(args.registry_root)))
    else:
        print(json.dumps([
            dataclasses.asdict(result) for result in reconcile_instances(
                registry_root=args.registry_root, limit=args.limit,
                max_seconds=args.max_seconds,
            )
        ]))
