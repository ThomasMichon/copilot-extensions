"""Periodic agent-index maintenance tick for agent-dispatch's script embodiment."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any


def _agent_index_launch_prefix() -> list[str]:
    try:
        from agent_dispatch.procutil import _sibling_runtime_launch_prefix

        prefix = _sibling_runtime_launch_prefix(
            "agent-index",
            "agent_index",
            "agent-index",
        )
        if prefix is not None:
            return list(prefix)
    except Exception:
        pass
    return ["agent-index"]


def _subprocess_kwargs() -> dict[str, object]:
    try:
        from agent_dispatch.procutil import no_window_kwargs

        return no_window_kwargs()
    except Exception:
        return {}


def _run_agent_index(
    args: Sequence[str],
    *,
    expect_json: bool,
    allow_exit_codes: set[int] | None = None,
    runner=subprocess.run,
) -> tuple[subprocess.CompletedProcess[str], Any]:
    command = [*_agent_index_launch_prefix(), *args]
    completed = runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **_subprocess_kwargs(),
    )
    allowed = allow_exit_codes or {0}
    if completed.returncode not in allowed:
        detail = (completed.stderr or completed.stdout or "").strip() or "command failed"
        raise RuntimeError(
            f"{' '.join(command)} exited {completed.returncode}: {detail}"
        )
    if not expect_json:
        return completed, (completed.stdout or "").strip()
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError(f"{' '.join(command)} did not emit JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{' '.join(command)} did not emit a JSON object")
    return completed, payload


def _service_ready(service_status: dict[str, Any]) -> bool:
    return (
        service_status.get("role") == "host"
        and service_status.get("state") == "ready"
        and service_status.get("running") is True
    )


def plan_maintenance_actions(
    service_status: dict[str, Any],
    engine_status: dict[str, Any],
) -> dict[str, bool]:
    return {
        "recover_service": not _service_ready(service_status),
        "start_engine": not bool(engine_status.get("healthy")),
    }


def run_maintenance(worker: Any, *, runner=subprocess.run) -> dict[str, Any]:
    _completed, service_status = _run_agent_index(["status"], expect_json=True, runner=runner)
    if service_status.get("setup_required"):
        raise RuntimeError("agent-index setup is required before maintenance can run")
    role = service_status.get("role")
    if role != "host":
        raise RuntimeError(f"agent-index maintenance requires role=host, not {role!r}")

    _completed, engine_status = _run_agent_index(
        ["engine", "status"],
        expect_json=True,
        runner=runner,
    )
    actions = plan_maintenance_actions(service_status, engine_status)
    worker.progress(
        phase="health-checked",
        summary=(
            f"service={service_status.get('state')} running={service_status.get('running')} "
            f"engine_healthy={engine_status.get('healthy')}"
        ),
    )

    service_recovered = False
    engine_started = False
    engine_pid_before = engine_status.get("pid")

    if actions["recover_service"]:
        _run_agent_index(["restart"], expect_json=False, runner=runner)
        _completed, service_status = _run_agent_index(["status"], expect_json=True, runner=runner)
        if not _service_ready(service_status):
            raise RuntimeError(
                "agent-index service is still not ready after restart"
            )
        service_recovered = True

    if actions["start_engine"]:
        _run_agent_index(["engine", "start"], expect_json=False, runner=runner)
        _completed, engine_status = _run_agent_index(
            ["engine", "status"],
            expect_json=True,
            runner=runner,
        )
        if not bool(engine_status.get("healthy")):
            raise RuntimeError("agent-index engine is still unhealthy after engine start")
        engine_started = True

    worker.progress(
        phase="self-healed",
        summary=(
            f"service_recovered={service_recovered} "
            f"engine_started={engine_started}"
        ),
    )
    worker.progress(
        phase="reindex-started",
        summary="running incremental agent-index index",
    )
    _completed, reindex = _run_agent_index(
        ["index"],
        expect_json=True,
        allow_exit_codes={0, 1},
        runner=runner,
    )
    failed_sources = reindex.get("sources_failed")
    if not isinstance(failed_sources, list):
        failed_sources = []
    chunks_total = int(reindex.get("chunks_total") or 0)
    worker.progress(
        phase="reindex-complete",
        summary=(
            f"incremental index finished: chunks_total={chunks_total} "
            f"sources_failed={len(failed_sources)}"
        ),
    )
    return {
        "summary": (
            f"chunks_total={chunks_total}; sources_failed={len(failed_sources)}; "
            f"service_recovered={service_recovered}; engine_started={engine_started}"
        ),
        "chunks_total": chunks_total,
        "sources_failed": len(failed_sources),
        "service_recovered": service_recovered,
        "engine_started": engine_started,
        "engine_pid_before": engine_pid_before,
        "engine_pid_after": engine_status.get("pid"),
        "service_state": service_status.get("state"),
    }


def main() -> int:
    from agent_dispatch.script_worker import ScriptTaskRuntime

    with ScriptTaskRuntime.from_env() as worker:
        worker.announce_start()
        result = run_maintenance(worker)
        worker.report_success(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
