"""Periodic agent-index maintenance tick for agent-dispatch's script embodiment."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from collections.abc import Sequence
from typing import Any

#: Bounded wait for this tick's own triggered reindex to finish before giving
#: up and letting the service keep running it in the background (the NEXT
#: tick will see indexing.running=True and step aside cleanly). Keeps one
#: script-embodied task from blocking its dispatch slot indefinitely.
_DEFAULT_POLL_TIMEOUT_S = 20 * 60.0
_DEFAULT_POLL_INTERVAL_S = 5.0


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


def _http_post_json(url: str, payload: dict, *, timeout: float = 20.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, *, timeout: float = 20.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


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


def run_maintenance(
    worker: Any,
    *,
    runner=subprocess.run,
    post_json=_http_post_json,
    get_json=_http_get_json,
    sleep=time.sleep,
    poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
) -> dict[str, Any]:
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

    # Route the reindex through the LIVE SERVICE's own queued POST /reindex
    # endpoint rather than invoking the engine directly via a second, raw
    # `agent-index index` CLI process. A raw CLI invocation writes
    # unserialized: it is invisible to the service's own `indexing.running`
    # tracking, so it can both (a) race a reindex someone else triggers
    # AFTER this tick's raw process has already started (a one-time
    # "is anything running?" check at tick-start cannot catch that -- a
    # TOCTOU gap), and (b) race in the other direction, outliving this tick
    # and colliding with a LATER externally-triggered reindex that correctly
    # believed it was safe to start. Both directions were observed in
    # production: a raw `index` CLI process idle for 8+ hours on a rate-limit
    # bug (fixed separately) outlived this tick and collided with a
    # subsequently-triggered full reindex. Posting to the service's own
    # endpoint means the service's single-task-at-a-time queue is the ONLY
    # place serialization needs to happen -- no raw concurrent writer can
    # ever exist again.
    _completed, pre_index_status = _run_agent_index(["status"], expect_json=True, runner=runner)
    if bool((pre_index_status.get("indexing") or {}).get("running")):
        worker.progress(
            phase="reindex-skipped",
            summary=(
                "service already has an indexing task in flight; skipping "
                "this tick's own trigger to avoid piling up redundant queued work"
            ),
        )
        return {
            "summary": (
                f"reindex skipped (already running); "
                f"service_recovered={service_recovered}; engine_started={engine_started}"
            ),
            "chunks_total": None,
            "sources_failed": 0,
            "sources_purged": [],
            "reindex_skipped": True,
            "service_recovered": service_recovered,
            "engine_started": engine_started,
            "engine_pid_before": engine_pid_before,
            "engine_pid_after": engine_status.get("pid"),
            "service_state": service_status.get("state"),
        }

    endpoint = pre_index_status.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise RuntimeError(
            "agent-index status did not report a service endpoint; cannot route "
            "the reindex through it"
        )
    reindex_url = endpoint.rstrip("/") + "/reindex"
    status_url = endpoint.rstrip("/") + "/status"
    chunks_before = int((pre_index_status.get("index") or {}).get("chunks") or 0)

    worker.progress(
        phase="reindex-started",
        summary="triggered a queued incremental reindex via the service's own /reindex endpoint",
    )
    triggered = post_json(reindex_url, {"full": False})
    task = triggered.get("task") if isinstance(triggered, dict) else None
    task_id = (task or {}).get("id") if isinstance(task, dict) else None

    deadline = time.monotonic() + poll_timeout_s
    final_status: dict[str, Any] = pre_index_status
    still_running = True
    while time.monotonic() < deadline:
        sleep(poll_interval_s)
        final_status = get_json(status_url)
        if not bool((final_status.get("indexing") or {}).get("running")):
            still_running = False
            break

    if still_running:
        worker.progress(
            phase="reindex-still-running",
            summary=(
                f"queued reindex task {task_id!r} is still running after "
                f"{poll_timeout_s:.0f}s; leaving it with the service -- the "
                "next tick will see indexing.running=True and skip, rather "
                "than this tick blocking its dispatch slot indefinitely"
            ),
        )
        return {
            "summary": (
                f"reindex still running after {poll_timeout_s:.0f}s; "
                f"service_recovered={service_recovered}; engine_started={engine_started}"
            ),
            "chunks_total": None,
            "sources_failed": 0,
            "sources_purged": [],
            "reindex_skipped": False,
            "reindex_still_running": True,
            "reindex_task_id": task_id,
            "service_recovered": service_recovered,
            "engine_started": engine_started,
            "engine_pid_before": engine_pid_before,
            "engine_pid_after": engine_status.get("pid"),
            "service_state": service_status.get("state"),
        }

    chunks_after = int((final_status.get("index") or {}).get("chunks") or 0)
    stats = (final_status.get("indexing") or {}).get("stats") or {}
    sources_failed = int(stats.get("failed") or 0)
    worker.progress(
        phase="reindex-complete",
        summary=(
            f"queued reindex finished: chunks_total={chunks_after} "
            f"(delta {chunks_after - chunks_before:+d}) sources_failed={sources_failed}"
        ),
    )
    return {
        "summary": (
            f"chunks_total={chunks_after}; sources_failed={sources_failed}; "
            f"service_recovered={service_recovered}; engine_started={engine_started}"
        ),
        "chunks_total": chunks_after,
        "sources_failed": sources_failed,
        # The service's /status does not expose a purged-sources list (unlike
        # the raw CLI's direct engine.run_reindex() result this replaced);
        # kept for return-shape compatibility only.
        "sources_purged": [],
        "reindex_skipped": False,
        "reindex_still_running": False,
        "reindex_task_id": task_id,
        "service_recovered": service_recovered,
        "engine_started": engine_started,
        "engine_pid_before": engine_pid_before,
        "engine_pid_after": engine_status.get("pid"),
        "service_state": final_status.get("state") or service_status.get("state"),
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
