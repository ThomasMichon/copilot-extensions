"""Compare-before-replace boundary shared by all repository registry writers."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
import time
from typing import TYPE_CHECKING

from dropin_registry import ScanAuthority

from . import config_acquisition as acquisition
from .acquisition_report import failed, observe
from .repos_registry_reader import RepositoryResolutionError

if TYPE_CHECKING:
    from agent_procutil.owned_budget import OwnedExecutionBudget

    from .repos import ReposRegistry


class RegistryConflictError(RepositoryResolutionError):
    """A registry changed after it was read; the operation must be restarted."""


def revision(content: bytes | None) -> str:
    return hashlib.sha256(content).hexdigest() if content is not None else "absent"


def read_bytes(path: Path) -> bytes | None:
    observations = acquisition.current_observations()
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        observe(observations, str(path), "config-source-bytes", ScanAuthority.ABSENT)
        return None
    except Exception as exc:
        failed(observations, str(path), "config-source-bytes", "unreadable-source", type(exc).__name__)
        raise
    observe(observations, str(path), "config-source-bytes", ScanAuthority.COMPLETE)
    return content


def remaining_timeout(budget: OwnedExecutionBudget, maximum: float) -> float:
    remaining = budget.execution_deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Registry execution budget expired.")
    return min(maximum, remaining)


@contextmanager
def record_lock(path: Path, *, budget: OwnedExecutionBudget | None = None):
    from .tracking import _RecordLock

    if budget is None:
        with _RecordLock(path, timeout=10.0, require_sidecar=True):
            yield
        return
    deadline = time.monotonic() + remaining_timeout(budget, 10.0)
    while True:
        remaining_timeout(budget, 10.0)
        if time.monotonic() >= deadline:
            raise TimeoutError("Registry lock acquisition budget expired.")
        with _RecordLock(path, blocking=False, require_sidecar=True) as lock:
            if lock.acquired:
                remaining_timeout(budget, 10.0)
                yield
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Registry lock acquisition budget expired.")
        time.sleep(min(0.05, remaining))


@contextmanager
def registry_lock(path: Path, *, budget: OwnedExecutionBudget | None = None):
    from . import pr_authority

    arguments = {"timeout": remaining_timeout(budget, float("inf"))} if budget is not None else {}
    with pr_authority.guard(**arguments):
        with record_lock(path, budget=budget):
            yield


def write_checked(
    path: Path, registry: ReposRegistry, content: str, *, budget: OwnedExecutionBudget | None = None,
) -> None:
    from .tracking import _atomic_write

    arguments = {"budget": budget} if budget is not None else {}
    with registry_lock(path, **arguments):
        current = read_bytes(path)
        baseline = registry._baseline
        expected = (str(path.resolve()), revision(current))
        if baseline is None:
            if current is not None:
                raise RegistryConflictError(
                    "repository registry must be read before replacing an existing file"
                )
        elif baseline != expected:
            raise RegistryConflictError(
                "repository registry changed; read again before retrying the operation"
            )
        _atomic_write(path, content)
        registry._baseline = (str(path.resolve()), revision(content.encode("utf-8")))
