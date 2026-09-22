"""Small helper runtime for non-agentic script workers.

The supervisor's ``script`` embodiment launches a plain subprocess, passes task
identity + coordinator routing through environment variables, and expects the
process to drive the ordinary claim/start/progress/complete/abandon lifecycle
itself. This module keeps that contract small and reusable:

.. code-block:: python

   from agent_dispatch.script_worker import ScriptTaskRuntime

   with ScriptTaskRuntime.from_env() as worker:
       worker.announce_start()
       worker.progress(phase="checking", summary="health-checking service")
       ...
       worker.report_success({"summary": "done"})

If the body raises, or returns without an explicit terminal call, the helper
abandones the task with a clear reason instead of silently leaving a live
reservation behind.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import DispatchClient
from .config import client_token, client_url, shared_token, shared_url

ENV_TASK_ID = "AGENT_DISPATCH_SCRIPT_TASK_ID"
ENV_WORKER_ID = "AGENT_DISPATCH_SCRIPT_WORKER_ID"
ENV_ROUTE = "AGENT_DISPATCH_SCRIPT_ROUTE"
ENV_REPO = "AGENT_DISPATCH_SCRIPT_REPO"
ENV_ALL_REPOS = "AGENT_DISPATCH_SCRIPT_ALL_REPOS"
ENV_TASK_FILE = "AGENT_DISPATCH_SCRIPT_TASK_FILE"
ENV_HEARTBEAT_SECONDS = "AGENT_DISPATCH_SCRIPT_HEARTBEAT_SECONDS"


class ScriptTaskProtocolError(RuntimeError):
    """The script exited without honoring the dispatch lifecycle contract."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _client_for_route(route: str) -> DispatchClient:
    route_name = (route or "local").strip().lower()
    if route_name == "shared":
        return DispatchClient(shared_url(), token=shared_token())
    return DispatchClient(client_url(), token=client_token())


@dataclass
class ScriptTaskRuntime:
    """Lifecycle wrapper for a plain deterministic worker subprocess."""

    client: DispatchClient
    task_id: str
    worker_id: str
    repo: str | None = None
    all_repos: bool = False
    heartbeat_seconds: int = 60
    task: dict[str, Any] | None = None
    task_file: str | None = None

    _terminal: bool = False
    _started: bool = False
    _closed: bool = False
    _generation: int | None = None
    _owner_session_id: str | None = None
    _heartbeat_stop: threading.Event | None = None
    _heartbeat_thread: threading.Thread | None = None

    @classmethod
    def from_env(cls) -> "ScriptTaskRuntime":
        task_id = os.environ.get(ENV_TASK_ID, "").strip()
        worker_id = os.environ.get(ENV_WORKER_ID, "").strip()
        if not task_id or not worker_id:
            raise RuntimeError(
                f"{ENV_TASK_ID} and {ENV_WORKER_ID} must be set for a script worker"
            )
        task_file = os.environ.get(ENV_TASK_FILE, "").strip() or None
        task: dict[str, Any] | None = None
        if task_file:
            task = json.loads(Path(task_file).read_text(encoding="utf-8"))
            if not isinstance(task, dict):
                raise RuntimeError(f"{ENV_TASK_FILE} must contain a task JSON object")
        heartbeat_seconds = int(os.environ.get(ENV_HEARTBEAT_SECONDS, "60") or "60")
        return cls(
            client=_client_for_route(os.environ.get(ENV_ROUTE, "local")),
            task_id=task_id,
            worker_id=worker_id,
            repo=(os.environ.get(ENV_REPO, "").strip() or None),
            all_repos=_env_flag(ENV_ALL_REPOS),
            heartbeat_seconds=max(1, heartbeat_seconds),
            task=task,
            task_file=task_file,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._heartbeat_stop is not None:
            self._heartbeat_stop.set()
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)
        if self.task_file:
            Path(self.task_file).unlink(missing_ok=True)
        self.client.close()

    def __enter__(self) -> "ScriptTaskRuntime":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None and not self._terminal:
            self.report_failure(f"{exc_type.__name__}: {exc}")
        if exc is None and not self._terminal:
            reason = "script exited without report_success() or report_failure()"
            self.report_failure(reason)
            self.close()
            raise ScriptTaskProtocolError(reason)
        self.close()
        return False

    def _remember(self, payload: dict[str, Any]) -> dict[str, Any]:
        generation = payload.get("generation")
        if isinstance(generation, int):
            self._generation = generation
        owner_session_id = payload.get("owner_session_id")
        if isinstance(owner_session_id, str) and owner_session_id:
            self._owner_session_id = owner_session_id
        return payload

    def payload_json(self) -> dict[str, Any] | None:
        raw = self.task.get("payload_inline") if isinstance(self.task, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    def announce_start(self) -> dict[str, Any]:
        claim = self.client.claim(
            worker_id=self.worker_id,
            repo=self.repo,
            all_repos=self.all_repos,
            task_id=self.task_id,
        )
        if not isinstance(claim, dict) or claim.get("id") != self.task_id:
            raise RuntimeError(f"claim did not return task {self.task_id!r}")
        started = self.client.start(self.task_id, self.worker_id)
        self._started = True
        self._start_heartbeats()
        return self._remember(started if isinstance(started, dict) else claim)

    def _start_heartbeats(self) -> None:
        if self._heartbeat_thread is not None or self.heartbeat_seconds <= 0:
            return
        self._heartbeat_stop = threading.Event()

        def _loop() -> None:
            while not self._heartbeat_stop.wait(self.heartbeat_seconds):
                if self._terminal:
                    return
                try:
                    beat = self.client.heartbeat(self.task_id, self.worker_id)
                except Exception:
                    return
                if isinstance(beat, dict):
                    self._remember(beat)

        self._heartbeat_thread = threading.Thread(
            target=_loop,
            name=f"dispatch-script-heartbeat-{self.worker_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def progress(
        self,
        *,
        phase: str = "",
        summary: str,
        blocker: str | None = None,
        pr: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.client.progress(
            self.task_id,
            self.worker_id,
            phase=phase,
            summary=summary,
            blocker=blocker,
            pr=pr,
        )
        return self._remember(payload)

    def set_card(self, *, card: dict[str, Any]) -> dict[str, Any]:
        payload = self.client.set_card(self.task_id, self.worker_id, card=card)
        return self._remember(payload)

    def steer_take(self) -> dict[str, Any]:
        payload = self.client.steer_take(self.task_id, self.worker_id)
        return self._remember(payload)

    def report_success(self, result: Any = None) -> dict[str, Any]:
        self._terminal = True
        payload = self.client.complete(
            self.task_id,
            self.worker_id,
            result=result,
            expected_status="started" if self._started else None,
            expected_generation=self._generation,
            expected_owner_session_id=self._owner_session_id,
        )
        return self._remember(payload)

    def report_failure(self, reason: str) -> dict[str, Any]:
        self._terminal = True
        payload = self.client.abandon(
            self.task_id,
            worker_id=self.worker_id,
            permitted=True,
            reason=reason,
            expected_status=None,
            expected_generation=self._generation,
            expected_owner_session_id=self._owner_session_id,
        )
        return self._remember(payload)
