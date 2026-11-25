"""Explicit source pilot; the loaded Worktrees owner retains all custody."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
import secrets
import sys
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, TypeVar, cast, overload

from plugin_test_containment import ContainmentError, Limits, UnreapedProcessError, run_contained

_Sandbox = TypeVar("_Sandbox", bound=AbstractContextManager[str])


class _GroupResult(TypedDict):
    group: int
    exit_code: int


class _ScratchProvider(Protocol):
    def allocate(
        self, path: Path, owner: str, *, root_path: Path, forbidden: tuple[Path, ...],
        operation_id: str, failure_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def settle_after_cleanup(
        self, path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...],
        diagnostic: bytes | None = None,
    ) -> dict[str, Any]: ...

    def dispose_consumed(
        self, path: Path, owner: str, token: str, *, forbidden: tuple[Path, ...],
    ) -> dict[str, Any]: ...


@overload
def test_sandbox(
    owner: None, repository: Path, fallback: Callable[..., _Sandbox], *, stage: str, **options: Any,
) -> _Sandbox: ...


@overload
def test_sandbox(
    owner: str, repository: Path, fallback: Callable[..., AbstractContextManager[str]],
    *, stage: str, **options: Any,
) -> OwnedTestSandbox: ...


def test_sandbox(
    owner: str | None, repository: Path, fallback: Callable[..., AbstractContextManager[str]],
    *, stage: str, **options: Any,
) -> AbstractContextManager[str]:
    return fallback(**options) if owner is None else OwnedTestSandbox(owner, repository, stage=stage)


def add_cli_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--owned-scratch-owner", metavar="MACHINE/PROJECT/WORKTREE",
        help="opt-in source pilot using an already-available compatible Worktrees Python owner; no rollout",
    )


def admit_owned_root(owner: str, repository: Path) -> None:
    OwnedTestSandbox(owner, repository, stage="admission")


@contextmanager
def finish_owned_run(
    owner: str | None, scope: AbstractContextManager[str], outcome: Callable[[], int],
    deadline: float, clock: Callable[[], float],
) -> Iterator[None]:
    yield
    if owner is not None:
        owned_scope = cast(OwnedTestSandbox, scope)
        code = 124 if clock() > deadline else outcome()
        if code == 124:
            owned_scope.record_result(0, code)
        owned_scope.record_outcome(code)


def prepare_owned(
    name: str, uv: str, reinstall: bool, *, retained: Path, environment: dict[str, str],
    sandbox: str | os.PathLike[str], limits: Limits | None = None, wall_seconds: float | None = None,
) -> None:
    import subprocess
    from dataclasses import replace
    selected = limits or Limits()
    if wall_seconds is not None:
        if wall_seconds <= 0:
            raise ContainmentError("preparation exhausted the plugin deadline before worker launch")
        selected = replace(selected, wall_seconds=min(selected.wall_seconds, wall_seconds))
    command = [
        sys.executable, str(Path(__file__).with_name("plugin_test_preparation_worker.py")),
        name, uv, str(retained), "reinstall" if reinstall else "reuse",
    ]
    result = run_contained(
        command, cwd=Path(__file__).resolve().parents[1], env=environment,
        sandbox=Path(sandbox), limits=selected,
    )
    if result:
        raise subprocess.CalledProcessError(result, command)


class OwnedTestSandbox:
    diagnostic_schema = "copilot-extensions.test-runner-diagnostic/v1"

    def __init__(self, owner: str, repository: Path, *, stage: str) -> None:
        try:
            from agent_worktrees import artifact_scratch, artifact_storage, config, config_sources
            from agent_worktrees import claims_cli, sessions, tracking
            from agent_worktrees.execution_spaces import resolve_owner_record_path
        except ImportError as import_error:
            raise ContainmentError(
                "owned scratch requires an already-available compatible Worktrees Python owner; "
                "no bootstrap or ordinary sandbox fallback"
            ) from import_error
        declaration = getattr(artifact_scratch, "capabilities", None)
        support = declaration() if callable(declaration) else {}
        if (
            support.get("schema") != artifact_scratch.SCHEMA
            or any(type(support.get(key)) is not int or support[key] != 1
                   for key in ("allocator", "artifact_retention"))
        ):
            raise ContainmentError("loaded scratch owner does not support allocation and retained disposition")
        if TYPE_CHECKING:
            from agent_worktrees.tracking_claims import parse_claim_ref
        else:
            parse_claim_ref = tracking.parse_claim_ref
        parsed = parse_claim_ref(owner)
        if parsed is None or not parsed.is_qualified or parsed.session:
            raise ContainmentError("owned scratch requires a qualified worktree owner without a session")
        assert parsed.project is not None
        selected = config.load_project_config(parsed.project)
        readiness = claims_cli._coordination_readiness_for_owner_ref(owner, selected)
        if not readiness.ready:
            raise ContainmentError(readiness.error or readiness.code)
        path, _, error = resolve_owner_record_path(owner, selected)
        if error or path is None:
            raise ContainmentError(error or "scratch owner ledger is unavailable")
        report = config_sources.configuration_report(selected, base_anchor=str(repository), cwd=str(repository))
        self.policy = artifact_storage.load_storage(report, session_root=sessions._session_state_dir())
        self.forbidden = tuple(
            Path(value) for key, value in report["context"].items()
            if key in {"base_root", "knowledge_root"}
        )
        self.forbidden += artifact_scratch.protected_git_paths(repository)
        from agent_worktrees.artifact_root import admit_root
        record = artifact_scratch._record(path, owner, mutable=True)
        with admit_root(
            Path(self.policy["scratch_root"]),
            forbidden=(*self.forbidden, *artifact_scratch._boundaries(record)), create=True,
        ):
            pass
        self.provider: _ScratchProvider = artifact_scratch
        self.owner, self.ledger = owner, path
        self.stage, self.operation_id = stage, secrets.token_urlsafe(24)
        self.results: list[_GroupResult] = []
        self.group_count = 0
        self.failed = False
        self.retained = False
        self.allocation: dict[str, Any] | None = None

    def __enter__(self) -> str:
        self.allocation = self.provider.allocate(
            self.ledger, self.owner, root_path=Path(self.policy["scratch_root"]),
            forbidden=self.forbidden, operation_id=self.operation_id,
            failure_evidence=self.policy["failure_evidence"],
        )
        return cast(str, self.allocation["path"])

    def record_result(self, group: int, code: int) -> None:
        # A fixed producer-owned summary, not arbitrary captured stdout/files.
        # Keep a bounded tail in memory even before the configured byte bound.
        self.results = [*self.results[-15:], {"group": group, "exit_code": code}]
        self.group_count += 1
        self.failed |= code != 0

    def record_outcome(self, code: int) -> None:
        self.failed = code != 0

    def retain(self, reason: str) -> None:
        self.retained = True
        print(f"[KEEP] {reason}: scratch allocation remains active", file=sys.stderr)

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        allocation = self.allocation
        assert allocation is not None
        if self.retained or isinstance(exc_value, UnreapedProcessError):
            print(
                f"[KEEP] Unsettled cleanup; active scratch token {allocation['token']}",
                file=sys.stderr,
            )
            return False
        failed = exc_value is not None or self.failed
        diagnostic = None
        if failed:
            diagnostic = json.dumps({
                "schema": self.diagnostic_schema, "stage": self.stage,
                "results_tail": self.results,
                "omitted_result_count": self.group_count - len(self.results),
                "exception_type": exc_type.__name__ if exc_type is not None else None,
                "omitted": "stdout, arbitrary sandbox files and caller-owned external reports not collected",
            }, sort_keys=True).encode("utf-8")
        try:
            # The runner exits its joined cleanup-worker context before this
            # boundary. UnreapedProcessError above never becomes a failure bundle.
            self.provider.settle_after_cleanup(
                self.ledger, self.owner, allocation["token"], forbidden=self.forbidden,
                diagnostic=diagnostic,
            )
            if failed:
                print(
                    f"[KEEP] Bounded runner diagnostics; retained scratch token {allocation['token']}",
                    file=sys.stderr,
                )
            else:
                result = self.provider.dispose_consumed(
                    self.ledger, self.owner, allocation["token"], forbidden=self.forbidden,
                )
                if result["outcome"] != "removed":
                    print(
                        f"[KEEP] Partial scratch removal; retained token {allocation['token']}",
                        file=sys.stderr,
                    )
        except (ValueError, OSError, RuntimeError) as error:
            print(
                f"[FAIL] Scratch disposition incomplete for {allocation['token']}: {error}",
                file=sys.stderr,
            )
            if exc_value is None:
                raise ContainmentError("scratch responsibility remains unresolved") from error
        return False
