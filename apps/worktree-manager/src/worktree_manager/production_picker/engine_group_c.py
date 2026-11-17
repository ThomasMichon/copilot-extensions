"""Group C Picker seam over the pinned engine CLI boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .. import engine_client


def _run_group_c_json_verb(
    project: str,
    args: list[str],
    *,
    verb_name: str,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
    runner=None,
) -> dict:
    try:
        return engine_client.run_json(project, args, timeout=timeout, runner=runner)
    except engine_client.EngineError as error:
        detail = engine_client._engine_error_detail(error).casefold()
        unsupported = (
            ("invalid choice" in detail and verb_name in detail)
            or ("unrecognized arguments" in detail and verb_name in detail)
            or ("unknown command" in detail and verb_name in detail)
        )
        if unsupported:
            raise engine_client.EngineFeatureUnavailable(
                f"installed engine does not support {verb_name} reads"
            ) from error
        raise


@dataclass(frozen=True)
class PickerReconcileLocalBatch:
    version: int
    rows: list[dict]
    summary: dict

    @classmethod
    def from_payload(cls, payload: dict) -> "PickerReconcileLocalBatch":
        rows = payload.get("rows")
        summary = payload.get("summary")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("picker-reconcile-local payload rows must be a list of objects")
        if not isinstance(summary, dict):
            raise ValueError("picker-reconcile-local payload summary must be an object")
        version = int(payload.get("version") or 0)
        if version < 1:
            raise ValueError("picker-reconcile-local payload version must be >= 1")
        return cls(version=version, rows=list(rows), summary=dict(summary))


def picker_reconcile_local(
    project: str,
    *,
    worktree_ids: list[str] | None = None,
    timeout: int = engine_client._DEFAULT_TIMEOUT,
    runner=None,
) -> PickerReconcileLocalBatch:
    args = ["picker-reconcile-local", "--json"]
    for worktree_id in worktree_ids or []:
        args += ["--worktree-id", worktree_id]
    payload = _run_group_c_json_verb(
        project,
        args,
        verb_name="picker-reconcile-local",
        timeout=timeout,
        runner=runner,
    )
    return PickerReconcileLocalBatch.from_payload(payload)
