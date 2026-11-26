"""Explicit source pilot; the loaded Worktrees owner retains all custody."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
import secrets
import sys
import subprocess
import time
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, TypeVar, cast, overload

from plugin_test_containment import ContainmentError, Limits, UnreapedProcessError, run_contained

_Sandbox = TypeVar("_Sandbox", bound=AbstractContextManager[str])


class PreallocationRefusal(ContainmentError):
    """The owned sandbox refused readiness before its allocation entrypoint."""


class UnsettledPreparationError(UnreapedProcessError):
    """Keep the original native custody and callbacks, not a reconstructed Job."""

    def __init__(self, native: Any, retained_inputs: ExitStack | None = None, transaction: Any = None) -> None:
        super().__init__("source preparation cleanup is unconfirmed", job=native.job)
        self.native_cleanup = native
        self.retained_inputs, self.transaction = retained_inputs, transaction

    def retry_cleanup(self, *, deadline: float) -> None:
        retry = getattr(self.native_cleanup, "retry_cleanup", None)
        if not callable(retry):
            raise ContainmentError("the original containment owner must reconcile retained cleanup")
        retry(deadline=deadline)


class UnsettledPreparationInputsError(UnreapedProcessError):
    """Keep the original retention failure and transaction for its owner."""

    def __init__(self, error: BaseException, transaction: Any, scope: ExitStack) -> None:
        super().__init__("retained preparation input cleanup is unconfirmed")
        self.retention_error, self.transaction, self.retained_scope = error, transaction, scope


def ensure_preparation(
    runner: Any, name: str, uv: str, *, reinstall: bool,
    limits: Limits | None, wall_seconds: float | None,
    scope: Callable[..., AbstractContextManager[str]],
) -> Path:
    from plugin_test_environment import preparation_environment

    started = runner.time.monotonic()
    parent = runner.runs_root(runner.REPO) if runner.OWNED_SCRATCH_OWNER is None else None
    limits = limits or runner.Limits()
    deadline = started + min(wall_seconds if wall_seconds is not None else limits.wall_seconds, limits.wall_seconds)
    if limits.absolute_deadline is not None:
        deadline = min(deadline, limits.absolute_deadline)
    with scope(
        runner.OWNED_SCRATCH_OWNER, runner.REPO, runner._TestSandbox, runner._venv_root() / name,
        deadline=deadline, prefix="ce-prep-", dir=parent,
    ) as private:
        retained = runner._venv_root()
        runner.record_owner(Path(private), runner.REPO, retention="disposable after verified preparation cleanup")
        environment = preparation_environment(
            runner.os.environ, Path(private), runner._plugin_dir(name), uv=uv, deadline=deadline,
        )
        cache = retained / ".uv-cache"
        cache.mkdir(parents=True, exist_ok=True)
        environment["UV_CACHE_DIR"] = str(cache.resolve())
        interpreters = retained / ".python"
        interpreters.mkdir(parents=True, exist_ok=True)
        environment["UV_PYTHON_INSTALL_DIR"] = str(interpreters.resolve())
        return cast(Path, runner._prepare_venv(
            name, uv, reinstall=reinstall, environment=environment,
            deadline=deadline, limits=limits, sandbox=Path(private),
        ))


def run_preparation_command(
    arguments: list[str], *, deadline: float, limits: Limits, sandbox: Path,
    env: dict[str, str], cwd: Path, check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    from dataclasses import replace

    deadline = min(deadline, limits.absolute_deadline) if limits.absolute_deadline is not None else deadline
    remaining = min(limits.wall_seconds, deadline - time.monotonic())
    if remaining <= 0:
        raise subprocess.TimeoutExpired(arguments, 0)
    result = run_contained(
        arguments, cwd=cwd, env=env, sandbox=sandbox,
        limits=replace(limits, wall_seconds=remaining, absolute_deadline=deadline),
    )
    completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(arguments, result)
    if check:
        completed.check_returncode()
    return completed


def run_preparation_install(
    uv: str, arguments: list[str], *, deadline: float, limits: Limits, sandbox: Path,
    check: bool = True,
    **retention_options: Any,
) -> Any:
    """Keep retained inputs and native settlement callbacks in their caller."""
    from installer_dependency_policy import retain_uv_install
    from source_preparation_process import procutil_module

    native = procutil_module("owned_process")
    retained = ExitStack()
    transaction = None
    def close() -> None:
        try:
            retained.close()
        except BaseException as error:
            raise UnsettledPreparationInputsError(error, transaction, retained) from error
    try:
        transaction = retained.enter_context(retain_uv_install(
            uv, arguments, deadline=deadline, **retention_options,
        ))
        result = run_preparation_command(
            list(transaction.argv), env=transaction.environment, cwd=transaction.cwd,
            deadline=deadline, limits=limits, sandbox=sandbox, check=check,
        )
    except native.OwnedCleanupError as error:
        error._retain_on_settled(close)
        raise
    except UnreapedProcessError as error:
        raise UnsettledPreparationError(error, retained, transaction) from error
    except subprocess.CalledProcessError as error:
        if error.stderr:
            diagnostic = error.stderr[:16 << 20]
            sys.stderr.write(diagnostic.decode("utf-8", "replace") if isinstance(diagnostic, bytes) else diagnostic)
            sys.stderr.flush()
        close()
        raise
    except BaseException:
        close()
        raise
    close()
    return result


@contextmanager
def preparation_scope(
    owner: str | None, repository: Path, fallback: Callable[..., AbstractContextManager[str]],
    venv: Path, *, deadline: float | None, **options: Any,
) -> Iterator[str]:
    from plugin_test_source_versions import preparation_ready, bind_preparation_owner
    from promotion_source_archive import composition_budget
    from source_preparation_process import procutil_module

    native = procutil_module("owned_process")
    with composition_budget(deadline), preparation_ready(venv) as cleanup:
        scope = None
        private: str | None = None
        try:
            try:
                scope = test_sandbox(owner, repository, fallback, stage="preparation",
                                     cleanup_deadline=deadline, **options)
            except PreallocationRefusal:
                cleanup["confirmed"] = True
                raise
            with scope as private:
                bind_preparation_owner(venv, private)
                try:
                    yield private
                except native.OwnedCleanupError as error:
                    raise UnsettledPreparationError(error) from error
        finally:
            if scope is not None:
                cleanup["confirmed"] = (
                    bool(getattr(scope, "cleanup_settled", False)) if owner is not None else
                    private is not None and not getattr(scope, "retained", True) and not Path(private).exists()
                )


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
    if owner is None:
        return fallback(**options)
    try:
        return OwnedTestSandbox(owner, repository, stage=stage)
    except (ContainmentError, ValueError, OSError) as error:
        # Allocation belongs exclusively to __enter__, never the readiness constructor.
        raise PreallocationRefusal(str(error)) from error


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
    import time
    from dataclasses import replace
    selected = limits or Limits()
    if wall_seconds is not None:
        if wall_seconds <= 0:
            raise ContainmentError("preparation exhausted the plugin deadline before worker launch")
        selected = replace(selected, wall_seconds=min(selected.wall_seconds, wall_seconds))
    command = [
        sys.executable, str(Path(__file__).with_name("plugin_test_preparation_worker.py")),
        name, uv, str(retained), "reinstall" if reinstall else "reuse",
        str(time.monotonic() + selected.wall_seconds),
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
        self.cleanup_settled = False
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
            self.cleanup_settled = True
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
