"""Opt-in source checks over retained emitter receipts, under the receipt owner."""

from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from ..client import DispatchClient
from ..client_errors import DispatchError
from ..queue_records import Status
from ..repository_resources import resolve_execution_resources, validate_resource_arguments
from ..task_relevance_contract import emitter_spec_revision
from . import emitter


def validate_config(spec: dict) -> None:
    config = spec["relevance"]
    if (
        not isinstance(config, dict)
        or set(config) - {"command", "batch_size", "timeout_seconds"}
        or not spec.get("command") or spec.get("task_output") != "json"
    ):
        raise emitter.EmitterError("relevance requires a task-authoring command emitter")
    command = config.get("command")
    if (
        not isinstance(command, list) or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise emitter.EmitterError("relevance.command must be a nonempty argument list")
    validate_resource_arguments(command)
    size = config.get("batch_size", 25)
    if type(size) is not int or not 1 <= size <= 100:
        raise emitter.EmitterError("relevance.batch_size must be an integer from 1 to 100")
    timeout = config.get("timeout_seconds", 30)
    if (
        type(timeout) not in (int, float) or not math.isfinite(timeout)
        or not 0 < timeout <= 300
    ):
        raise emitter.EmitterError("relevance.timeout_seconds must be finite and at most 300")


def _load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "cursor": 0, "pending": {}, "recovery_cursor": ""}
    state = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(state, dict) or state.get("version") != 1
        or type(state.get("cursor")) is not int or state["cursor"] < 0
        or not isinstance(state.get("pending"), dict)
        or not isinstance(state.get("recovery_cursor"), str)
    ):
        raise emitter.EmitterError("invalid retained emitter relevance state")
    for task_id, report in state["pending"].items():
        if (
            not isinstance(task_id, str) or not isinstance(report, dict)
            or not report.get("registration_id") or not report.get("request_id")
        ):
            raise emitter.EmitterError("invalid retained emitter relevance request")
    return state


def _save(path: Path, state: dict) -> None:
    emitter._write_receipt_lines(path, [json.dumps(state, sort_keys=True, allow_nan=False)])


def _observations(stdout: str, tasks: list[dict]) -> dict[str, dict]:
    result = json.loads(stdout)
    if (
        not isinstance(result, dict) or set(result) != {"version", "observations"}
        or type(result["version"]) is not int or result["version"] != 1
        or not isinstance(result["observations"], list)
    ):
        raise emitter.EmitterError("source check must return version-1 observations")
    expected = {item["snapshot"]["task_id"] for item in tasks}
    observations = {}
    for row in result["observations"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"task_id", "verdict", "reason", "observation_ref"}
            or not isinstance(row["task_id"], str)
            or row["task_id"] not in expected or row["task_id"] in observations
            or row["verdict"] not in ("needed", "obsolete", "unknown")
        ):
            raise emitter.EmitterError("invalid, duplicate or foreign source observation")
        for key in ("reason", "observation_ref"):
            text = row[key]
            if (
                not isinstance(text, str) or not text.strip() or "\0" in text
                or len(text.encode("utf-8")) > 4096
            ):
                raise emitter.EmitterError(f"source observation needs a bounded {key}")
        observations[row["task_id"]] = row
    if set(observations) != expected:
        raise emitter.EmitterError("incomplete source check; missing observations are unknown")
    return observations


def reconcile_receipts(
    client: DispatchClient, spec: dict, *, registration_id: str | None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Check a bounded rotating batch; never infer closure or retry an uncertain POST."""
    validate_config(spec)
    if not isinstance(registration_id, str) or not registration_id.strip():
        raise emitter.EmitterError("relevance needs the actual emitter --registration-id")
    config = spec["relevance"]
    size = config.get("batch_size", 25)
    result: dict[str, Any] = {"accepted": [], "recovered": [], "errors": []}
    with emitter.locked_receipts_path(spec["id"], name="relevance.json") as path:
        state = _load(path)
        pending = sorted(state["pending"])
        pending = [
            task_id for task_id in pending if task_id > state["recovery_cursor"]
        ] + [task_id for task_id in pending if task_id <= state["recovery_cursor"]]
        for task_id in pending[:size]:
            report = state["pending"][task_id]
            state["recovery_cursor"] = task_id
            try:
                receipt = client.relevance_receipt(
                    task_id, report["request_id"], registration_id=report["registration_id"],
                )
                acceptance = receipt["acceptance"]
                if (
                    acceptance.get("task_id") != task_id
                    or acceptance.get("request_id") != report["request_id"]
                ):
                    raise emitter.EmitterError("relevance receipt identity does not match")
            except Exception as exc:
                result["errors"].append({"task_id": task_id, "error": str(exc)})
                continue
            del state["pending"][task_id]
            _save(path, state)
            result["recovered"].append(acceptance)

        receipts = emitter.read_receipts(spec["id"])
        rows = {}
        for row in receipts["receipts"]:
            if isinstance(row.get("task_id"), str):
                rows[row["task_id"]] = row
        ordered = sorted(rows.values(), key=lambda row: row["seq"])
        candidates = [
            row for row in ordered if row["seq"] > state["cursor"]
        ] + [row for row in ordered if row["seq"] <= state["cursor"]]
        available = max(0, emitter.DEFAULT_MAX_RECEIPTS - len(state["pending"]))
        result["capacity_held"] = available == 0
        if not available:
            result["errors"].append({"error": "recover retained relevance requests before admitting more"})
        tasks = []
        for row in candidates[:min(size, available)]:
            task_id = row["task_id"]
            state["cursor"] = row["seq"]
            if task_id in state["pending"]:
                continue
            try:
                snapshot = client.relevance_snapshot(
                    task_id, registration_id=registration_id, effective_scope=True,
                )
                if (
                    snapshot["task_id"] != task_id
                    or snapshot["registration_id"] != registration_id
                    or snapshot["source"] != (spec.get("source") or "emitter")
                    or snapshot["origin_ref"] != spec["id"]
                    or not snapshot.get("effective_revision")
                    or snapshot.get("emitter_spec_revision") != emitter_spec_revision(spec)
                ):
                    raise emitter.EmitterError("source snapshot does not match this emitter")
                if snapshot["status"] in {*Status.TERMINAL, Status.DEAD_LETTER} or snapshot["closeout"]:
                    continue
                tasks.append({"receipt": row, "snapshot": snapshot})
            except Exception as exc:
                result["errors"].append({"task_id": task_id, "error": str(exc)})
        _save(path, state)
        result["retained_receipts"] = len(rows)
        result["receipt_history_trimmed"] = bool(
            receipts["receipts"] and receipts["receipts"][0]["seq"] > 1
        )
        if tasks:
            try:
                command, cwd = resolve_execution_resources(
                    emitter._render_command(config["command"]), spec.get("cwd"),
                    bind_relative_executable=True,
                )
                completed = runner(
                    command, cwd=cwd,
                    env={
                        **os.environ, **(spec.get("env") or {}),
                        emitter.RECEIPTS_PATH_ENV: str(emitter.receipts_path(spec["id"])),
                    },
                    input=json.dumps({"version": 1, "tasks": tasks}),
                    capture_output=True, text=True, check=False,
                    timeout=config.get("timeout_seconds", 30), **no_window_kwargs(),
                )
                if completed.returncode != 0:
                    raise emitter.EmitterError(f"source check exited {completed.returncode}")
                observations = _observations(completed.stdout, tasks)
            except Exception as exc:
                result["errors"].append({"error": str(exc)})
            else:
                for task in tasks:
                    snapshot = task["snapshot"]
                    task_id = snapshot["task_id"]
                    row = observations[task_id]
                    report = {
                        "version": 1, "registration_id": registration_id,
                        "registration_revision": snapshot["registration_revision"],
                        "effective_revision": snapshot["effective_revision"],
                        "request_id": str(uuid.uuid4()),
                        "expected_generation": snapshot["generation"],
                        "expected_revision": snapshot["revision"],
                        **{key: row[key] for key in ("verdict", "reason", "observation_ref")},
                    }
                    state["pending"][task_id] = report
                    _save(path, state)
                    try:
                        acceptance = client.report_task_relevance(task_id, report=report)
                        if (
                            acceptance.get("task_id") != task_id
                            or acceptance.get("request_id") != report["request_id"]
                        ):
                            raise emitter.EmitterError("relevance acceptance identity does not match")
                    except Exception as exc:
                        if (
                            isinstance(exc, DispatchError) and exc.status_code == 409
                            and isinstance(exc.detail, dict)
                            and exc.detail.get("code") == "relevance_not_accepted"
                            and exc.detail.get("task_id") == task_id
                            and exc.detail.get("request_id") == report["request_id"]
                        ):
                            del state["pending"][task_id]
                            _save(path, state)
                        result["errors"].append({
                            "task_id": task_id, "request_id": report["request_id"], "error": str(exc),
                        })
                        continue
                    del state["pending"][task_id]
                    _save(path, state)
                    result["accepted"].append(acceptance)
        result["pending"] = len(state["pending"])
    return result
