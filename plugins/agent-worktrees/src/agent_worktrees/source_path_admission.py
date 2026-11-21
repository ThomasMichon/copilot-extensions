"""Exclude native source adoption while publication retains its checkout."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import os
from pathlib import Path
import time
from typing import ParamSpec, TypeVar

from single_instance_lease import AlreadyRunningError, SingleInstance

from . import config as cfg

P = ParamSpec("P")
R = TypeVar("R")
_nonblocking_creation: ContextVar[bool] = ContextVar("source-nonblocking-creation", default=False)


@contextmanager
def nonblocking_creation() -> Iterator[None]:
    token = _nonblocking_creation.set(True)
    try:
        yield
    finally:
        _nonblocking_creation.reset(token)


@contextmanager
def guard(workspace: str | Path | None, *, timeout: float = 3) -> Iterator[None]:
    if workspace is None:
        yield
        return
    identity = os.path.normcase(str(Path(workspace).resolve()))
    key = hashlib.sha256(os.fsencode(identity)).hexdigest()
    lease = SingleInstance(
        cfg.install_dir() / "source-path-admission" / key,
        service="source publication or adoption", lock_name="admission.lock",
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            lease.acquire()
            break
        except AlreadyRunningError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Source publication or adoption is in progress") from exc
            time.sleep(min(0.01, remaining))
    try:
        yield
    finally:
        lease.release()


def creation(function: Callable[P, R]) -> Callable[P, R]:
    signature = inspect.signature(function)

    @wraps(function)
    def admitted(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature.bind(*args, **kwargs).arguments
        with guard(bound["worktree_path"], timeout=0 if _nonblocking_creation.get() else 3):
            return function(*args, **kwargs)

    return admitted
