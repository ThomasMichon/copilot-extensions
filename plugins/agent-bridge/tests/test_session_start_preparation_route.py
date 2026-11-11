"""``POST /api/v1/sessions`` surfaces local launch preparation failures.

A plugin/marketplace resolution failure during local launch preparation must
reach the caller as a typed, actionable refusal and be logged at ERROR, not
escape as a bare HTTP 500 whose cause only reaches the daemon's stderr.
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.local_launch_preparation import LocalLaunchPreparationError
from agent_bridge.routes import sessions as sessions_route

_AMBIGUOUS = "Installed marketplace catalog is ambiguous: example-plugins (candidates: a, b)"


class _FailingPreparationMgr:
    is_draining = False

    def start_admission(self):
        return contextlib.nullcontext()

    def list_sessions(self, status=None):  # noqa: ANN001, ARG002
        return []

    async def start_session(self, target, **kwargs):  # noqa: ANN001, ARG002
        raise LocalLaunchPreparationError(_AMBIGUOUS)


def test_preparation_failure_returns_typed_refusal_and_logs_error(caplog):
    app = FastAPI()
    app.state.session_manager = _FailingPreparationMgr()
    app.include_router(sessions_route.router)

    with caplog.at_level(logging.ERROR, logger=sessions_route.log.name):
        response = TestClient(app).post("/api/v1/sessions", json={})

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "local_launch_preparation_failed",
        "message": _AMBIGUOUS,
        "effect": "not_started",
    }
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and "catalog is ambiguous" in errors[0].getMessage()
    assert errors[0].exc_info is not None
