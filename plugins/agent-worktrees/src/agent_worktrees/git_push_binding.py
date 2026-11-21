"""Capture a push's source object, implicit target and native hook identity once."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import inspect
from pathlib import Path
import re
import subprocess
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, ParamSpec

if TYPE_CHECKING:
    from .git_ops import PushResult

P = ParamSpec("P")


@dataclass(frozen=True)
class Binding:
    refspec: str
    object_id: str
    hook_ref: str


current: ContextVar[Binding | None] = ContextVar("push-source-binding", default=None)


def _capture(refspec: str, *, cwd: str | Path, timeout: float | None) -> Binding:
    from . import git_acquisition_budget, git_ops

    source, separator, target = refspec.partition(":")
    force, source = ("+" if source.startswith("+") else ""), source.removeprefix("+")
    if any(char.isspace() or char in "*?\0\r\n" for char in refspec):
        raise ValueError("Publication requires one exact source and destination ref.")
    if not source:
        if not separator or not target:
            raise ValueError("Deletion requires an exact destination ref.")
        return Binding(refspec, "", "")
    symbolic = git_ops.git(
        "rev-parse", "--verify", "--symbolic-full-name", source,
        cwd=cwd, isolated_repository=True,
        timeout=git_acquisition_budget.remaining_timeout(timeout), kill_tree=True,
    ).stdout.strip()
    if not separator:
        if not symbolic.startswith(("refs/heads/", "refs/tags/")):
            raise ValueError("An implicit destination requires a named branch or tag.")
        target = symbolic
    oid = git_ops.git(
        "rev-parse", "--verify", (symbolic or source) + "^{object}",
        cwd=cwd, isolated_repository=True,
        timeout=git_acquisition_budget.remaining_timeout(timeout), kill_tree=True,
    ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", oid):
        raise ValueError("The exact pushed source object is unavailable.")
    return Binding(force + oid + ":" + target, oid, "HEAD" if source == "HEAD" else symbolic or source)


def capture(function: Callable[P, PushResult]) -> Callable[P, PushResult]:
    signature = inspect.signature(function)

    @wraps(function)
    def captured(*args: P.args, **kwargs: P.kwargs) -> PushResult:
        from . import git_acquisition_budget, publication_deadline, push_timeout
        from .git_ops import GitError, PushResult

        invocation = signature.bind(*args, **kwargs)
        bound = invocation.arguments
        if bound.get("allow_history_rewrite") and not bound.get("force_with_lease_expect"):
            return PushResult(ok=False, stderr="History rewrites require an exact expected-head lease.")
        existing = current.get()
        if existing is not None and bound["branch"] == existing.refspec:
            return function(*args, **kwargs)
        try:
            requested = bound.get("timeout")
            timeout = publication_deadline.validate(
                push_timeout.DEFAULT_PUSH_TIMEOUT if requested is None else requested,
            )
            with git_acquisition_budget.acquisition_budget(time.monotonic() + timeout):
                binding = _capture(bound["branch"], cwd=bound["cwd"], timeout=timeout)
        except (GitError, OSError, ValueError, subprocess.SubprocessError) as exc:
            return PushResult(ok=False, stderr=f"Cannot bind the pushed source and destination: {exc}")
        bound["branch"] = binding.refspec
        token = current.set(binding)
        try:
            return function(*invocation.args, **invocation.kwargs)
        finally:
            current.reset(token)

    return captured
