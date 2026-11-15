"""Explicit source pilot; the loaded Worktrees owner retains all custody."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import secrets
import sys

from plugin_test_containment import ContainmentError, Limits, UnreapedProcessError, run_contained


def test_sandbox(owner, repository, fallback, *, stage: str, **options):
    return fallback(**options) if owner is None else OwnedTestSandbox(owner, repository, stage=stage)


def add_cli_argument(parser):
    parser.add_argument(
        "--owned-scratch-owner", metavar="MACHINE/PROJECT/WORKTREE",
        help="opt-in source pilot using an already-available compatible Worktrees Python owner; no rollout",
    )


def admit_owned_root(owner, repository):
    OwnedTestSandbox(owner, repository, stage="admission")


@contextmanager
def finish_owned_run(owner, scope, outcome, deadline, clock):
    yield
    if owner is not None:
        code = 124 if clock() > deadline else outcome()
        if code == 124:
            scope.record_result(0, code)
        scope.record_outcome(code)


def prepare_owned(name, uv, reinstall, *, retained, environment, sandbox, limits=None, wall_seconds=None):
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
    def __init__(self, owner: str, repository: Path, *, stage: str):
        try:
            from agent_worktrees import artifact_scratch, artifact_storage, config, config_sources
            from agent_worktrees import claims_cli, sessions, tracking
            from agent_worktrees.execution_spaces import resolve_owner_record_path
        except ImportError as error:
            raise ContainmentError(
                "owned scratch requires an already-available compatible Worktrees Python owner; "
                "no bootstrap or ordinary sandbox fallback"
            ) from error
        declaration = getattr(artifact_scratch, "capabilities", None)
        support = declaration() if callable(declaration) else {}
        if (
            support.get("schema") != artifact_scratch.SCHEMA
            or any(type(support.get(key)) is not int or support[key] != 1
                   for key in ("allocator", "artifact_retention"))
        ):
            raise ContainmentError("loaded scratch owner does not support allocation and retained disposition")
        parsed = tracking.parse_claim_ref(owner)
        if parsed is None or not parsed.is_qualified or parsed.session:
            raise ContainmentError("owned scratch requires a qualified worktree owner without a session")
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
        self.provider, self.owner, self.ledger = artifact_scratch, owner, path
        self.stage, self.operation_id = stage, secrets.token_urlsafe(24)
        self.results: list[dict] = []
        self.group_count = 0
        self.failed = False
        self.allocation: dict | None = None

    def __enter__(self) -> str:
        self.allocation = self.provider.allocate(
            self.ledger, self.owner, root_path=Path(self.policy["scratch_root"]),
            forbidden=self.forbidden, operation_id=self.operation_id,
            failure_evidence=self.policy["failure_evidence"],
        )
        return self.allocation["path"]

    def record_result(self, group: int, code: int) -> None:
        # A fixed producer-owned summary, not arbitrary captured stdout/files.
        # Keep a bounded tail in memory even before the configured byte bound.
        self.results = [*self.results[-15:], {"group": group, "exit_code": code}]
        self.group_count += 1
        self.failed |= code != 0

    def record_outcome(self, code: int) -> None:
        self.failed = code != 0

    def __exit__(self, exc_type, exc_value, traceback):
        allocation = self.allocation
        assert allocation is not None
        if isinstance(exc_value, UnreapedProcessError):
            print(
                f"[KEEP] Unknown process cleanup; active scratch token {allocation['token']}",
                file=sys.stderr,
            )
            return False
        failed = exc_value is not None or self.failed
        diagnostic = None
        if failed:
            diagnostic = json.dumps({
                "schema": "copilot-extensions.test-runner-diagnostic/v1", "stage": self.stage,
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
