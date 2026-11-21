"""Engine acquisition resumes payload timing after preparing authentication."""

from __future__ import annotations

from contextlib import nullcontext
import io
from pathlib import Path
import subprocess

import pytest
import update_telemetry as ut
from update_telemetry.human import HumanOperation, current_operation

from agent_worktrees import (
    activation_preservation,
    reconcile,
    update_acquisition,
    update_auth as auth,
    update_auth_context as context,
    update_auth_sources as sources,
    update_events,
)

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_engine_payload_work_restores_phase_after_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    prepared: bool, failed: bool,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "telemetry"
    monkeypatch.setenv("CE_UPDATE_AUTH", "on")
    monkeypatch.setenv(ut.ENV_HOME, str(home))
    for variable in (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(
        sources, "marketplaces",
        lambda cwd: [auth.source(
            "marketplace:copilot-extensions", "https://example.test/team/core", cwd=cwd,
        )],
    )
    reports: list[Path | None] = []

    def report(items: list[auth.Source]) -> auth.AuthReport:
        reports.extend(item.cwd for item in items)
        return auth.AuthReport([
            auth.SourceAuth(item, [], status="anonymous") for item in items
        ])

    monkeypatch.setattr(auth, "report", report)
    monkeypatch.setattr(activation_preservation, "activation_custody", nullcontext)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: project)
    work: list[tuple[str, str]] = []

    def acquire(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        run = update_events._run
        operation = current_operation()
        assert run is not None and run._step is not None and operation is not None
        work.append((run._step[0], operation.label))
        assert argv == [
            "fixture-copilot", "plugin", "update", "agent-worktrees@copilot-extensions",
        ]
        return subprocess.CompletedProcess(
            argv, 23 if failed else 0, "" if failed else "fixture payload updated",
            "fixture child failed" if failed else "",
        )

    monkeypatch.setattr(update_acquisition, "_run_update", acquire)
    sink = io.StringIO()
    update_events.start()
    run = update_events._run
    assert run is not None
    events_path = run.events_path
    try:
        with HumanOperation("update/engine", stream=sink):
            with context.session() as session:
                assert session is not None
                if prepared:
                    session.prepare(project)
                update_events.enter("payload_refresh")
                status = update_acquisition.acquire(
                    "agent-worktrees", "copilot-extensions", "fixture-copilot",
                    cwd=project, engine=True,
                )
                update_events.record(
                    "payload_refresh", "agent-worktrees@copilot-extensions", status,
                )
                assert session.context_required[project] is True
                assert not session.unresolved
            rc = update_events.finish(1 if failed else 0)
    finally:
        update_events.finish(1)

    assert work == [("payload_refresh", "payload_refresh")]
    assert reports == [project]
    assert status.startswith("plugin update failed") if failed else status == "OK"
    assert rc == (1 if failed else 0)
    events, errors = ut.read_events(events_path)
    assert errors == 0
    starts = [
        event["phase"] for event in events
        if event["component"] == "run" and event["event"] == "start"
        and event["phase"] != "run"
    ]
    ends = [
        event["phase"] for event in events
        if event["component"] == "run" and event["event"] == "end"
        and event["phase"] != "run"
    ]
    assert starts == ends
    assert starts.count("auth_report") == 1
    assert "auth_report failed" not in sink.getvalue()
    assert capsys.readouterr().out == ""
    assert current_operation() is None
