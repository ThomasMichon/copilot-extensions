"""Queue hook mutations without changing the canonical JSON/read-only contracts."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import update_driver_json_cli as boundary


def _args(**values):
    return argparse.Namespace(**{
        "apply": False, "check_update": False, "peek": False,
        "with_payload_refresh": False, "repo": None, "status": None,
        "machine": None, "indicator_state": False, "json": False, **values,
    })


@pytest.mark.parametrize("values", [{}, {"peek": True}, {"apply": True, "check_update": True}])
def test_read_only_reconcile_never_queues(values, monkeypatch):
    seen = []
    monkeypatch.setattr(boundary.update_cli, "cmd_reconcile_plugins", lambda args: seen.append(args) or 19)
    assert boundary.cmd_reconcile_request(_args(**values)) == 19
    assert len(seen) == 1


def test_indicator_read_retains_its_existing_stdout_contract(monkeypatch):
    seen = []
    monkeypatch.setattr(boundary.update_stage, "cmd_stage_update", lambda args: seen.append(args) or 0)
    assert boundary.cmd_stage_request(_args(indicator_state=True, json=True)) == 0
    assert seen[0].json


def test_stage_records_json_in_worker_but_default_caller_stays_quiet(monkeypatch):
    seen = {}

    def dispatch(verb, flags, args, execute, **kwargs):
        seen.update(verb=verb, flags=flags, emit=kwargs["emit_json"])
        return 0

    monkeypatch.setattr(boundary, "_run_request", dispatch)
    assert boundary.cmd_stage_request(_args()) == 0
    assert seen == {"verb": "stage-update", "flags": ["--json"], "emit": False}


def test_apply_retains_machine_repo_status_and_payload_intent(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.chdir(tmp_path)

    def dispatch(verb, flags, args, execute):
        seen.update(verb=verb, flags=flags)
        return 0

    monkeypatch.setattr(boundary, "_run_request", dispatch)
    assert boundary.cmd_reconcile_request(_args(
        apply=True, repo="repo", status="status.json", machine="fixture",
        with_payload_refresh=True,
    )) == 0
    assert seen["flags"] == [
        "--apply", "--repo", str(tmp_path / "repo"), "--status", str(tmp_path / "status.json"),
        "--machine", "fixture", "--with-payload-refresh",
    ]


def test_result_publication_preserves_json_and_unowned_temporary(tmp_path):
    request = SimpleNamespace(id="fixture", log_path=tmp_path / "fixture.log")
    foreign = tmp_path / "fixture.result.result.tmp"
    foreign.write_text("unrelated", encoding="utf-8")
    boundary._publish_result(request, '{"executed":[]}\n')
    assert json.loads(boundary._result_path(request).read_text(encoding="utf-8")) == {"executed": []}
    assert foreign.read_text(encoding="utf-8") == "unrelated"
    with pytest.raises(ValueError, match="already exists"):
        boundary._publish_result(request, '{"ok":true}')


def test_protocol_output_limit_fails_without_silent_truncation():
    buffer = boundary._ProtocolBuffer()
    with pytest.raises(ValueError, match="output limit"):
        buffer.write("x" * (boundary._PROTOCOL_LIMIT + 1))
    assert buffer.getvalue() == ""


@pytest.mark.parametrize("emit_json", [False, True])
def test_early_stage_failure_respects_stdout_contract(tmp_path, monkeypatch, capsys, emit_json):
    import update_driver

    def refuse():
        raise update_driver.ParticipantError("synthetic custody refusal")

    status = tmp_path / "stage.json"
    status.write_text('{"stage_done":true,"plugin_changed":true}')
    monkeypatch.setattr(boundary.update_stage, "status_path", lambda: status)
    monkeypatch.setattr(update_driver, "borrowed_custody", refuse)
    assert boundary.cmd_stage_request(_args(json=emit_json)) == 1
    result = capsys.readouterr()
    assert "synthetic custody refusal" in result.err
    if emit_json:
        assert json.loads(result.out)["reason"] == "update-driver"
    else:
        assert result.out == ""
    failed = json.loads(status.read_text())
    assert failed["stage_done"] is False and failed["plugin_changed"] is False
    assert failed["runtime_apply_blocked"] == "update-driver"


@pytest.mark.parametrize("phase", ["custody", "enqueue", "launch"])
def test_driver_refusal_replaces_previous_success_status(tmp_path, monkeypatch, capsys, phase):
    import update_driver

    repo, status = tmp_path / "repo", tmp_path / "status.json"
    status.write_text(json.dumps({"version": 1, "ok": True, "repo": str(repo)}))

    def refuse(*args, **kwargs):
        raise update_driver.ParticipantError("synthetic custody refusal")

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: None)
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(update_driver, "runtime_command", lambda *args: (("synthetic",), {}))
    monkeypatch.setattr(update_driver, "enqueue", lambda *args, **kwargs: SimpleNamespace(
        id="synthetic-request", log_path=tmp_path / "request.log",
    ))
    monkeypatch.setattr(update_driver, {
        "custody": "borrowed_custody", "enqueue": "enqueue", "launch": "ensure_worker",
    }[phase], refuse)
    assert boundary.cmd_reconcile_request(_args(apply=True, repo=str(repo), status=str(status))) == 1
    failed = json.loads(status.read_text())
    assert failed["version"] == 1 and failed["ok"] is False
    assert failed["repo"] == str(repo)
    assert failed["reason"] == "update-driver"
    assert failed["failed"] == []
    assert "synthetic custody refusal" in failed["message"]
    assert failed["updated_at"]
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("verb", ["stage-update", "reconcile-plugins"])
def test_status_publication_failure_retains_original_json_error(tmp_path, monkeypatch, capsys, verb):
    import update_driver

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("unrelated")

    def refuse():
        raise update_driver.ParticipantError("synthetic custody refusal")

    monkeypatch.setattr(update_driver, "borrowed_custody", refuse)
    args = _args(apply=True, json=True, repo=str(tmp_path), status=str(blocked / "status.json"))
    command = boundary.cmd_stage_request if verb == "stage-update" else boundary.cmd_reconcile_request
    assert command(args) == 1
    result = capsys.readouterr()
    failure = json.loads(result.out)
    assert failure["reason"] == "update-driver"
    assert failure["message"] == "synthetic custody refusal"
    assert failure["status_error"]
    assert "synthetic custody refusal" in result.err
    assert "Update failure status could not be written" in result.err
    assert blocked.read_text() == "unrelated"


def test_queued_json_round_trip_returns_original_payload_and_exit(tmp_path, monkeypatch, capsys):
    import update_driver

    request = SimpleNamespace(
        id="fixture", state="completed", exit_code=5, error="canonical failure",
        log_path=tmp_path / "fixture.log",
    )
    boundary._publish_result(request, '{"executed":[{"ok":false}]}')
    seen = {}

    def enqueue(spec, *, runner):
        seen["spec"] = spec
        return request

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (str(tmp_path / "python.exe"), "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(update_driver, "wait", lambda *args, **kwargs: request)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: None)
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path)
    assert boundary.cmd_reconcile_request(_args(apply=True)) == 5
    assert json.loads(capsys.readouterr().out) == {"executed": [{"ok": False}]}
    assert seen["spec"].backend == "engine-reconcile-plugins"


@pytest.mark.parametrize("backend", ["engine", "engine-reconcile-plugins"])
def test_nested_update_participant_returns_json_without_recursive_enqueue(
    tmp_path, monkeypatch, capsys, backend,
):
    import update_driver

    request = SimpleNamespace(
        id="fixture", state="running", log_path=tmp_path / "fixture.log",
        spec=SimpleNamespace(
            backend=backend, context=(), argv=("--apply",), source=str(tmp_path),
        ),
    )
    scoped = []

    class Custody:
        root = tmp_path
        request_id = "fixture"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            pass

        def execution_scope(self):
            from contextlib import contextmanager

            @contextmanager
            def scope():
                scoped.append("begin")
                yield
                scoped.append("end")

            return scope()

    def execute(args):
        scoped.append("execute")
        print('{"executed":[]}')
        return 0

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kwargs: request)
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(boundary.update_cli, "cmd_reconcile_plugins", execute)
    assert boundary.cmd_reconcile_request(_args(apply=True)) == 0
    output = capsys.readouterr().out
    if backend == "engine":
        assert json.loads(output) == {"executed": []}
    else:
        assert output == ""
        assert json.loads(boundary._result_path(request).read_text()) == {"executed": []}
    assert scoped == ["begin", "execute", "end"]


def test_actual_empty_inventory_apply_uses_worker_and_retains_json(tmp_path, monkeypatch):
    import sys
    from agent_procutil.owned_process import run_owned
    import update_driver

    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    result = run_owned(
        [sys.executable, "-m", "agent_worktrees", "reconcile-plugins", "--repo", str(repo), "--apply"],
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "Update request " in result.stderr
    summary = json.loads(result.stdout)
    assert isinstance(summary, dict) and summary["executed"] == []
    queue_root = home / ".copilot-extensions" / "update-driver"
    assert (queue_root / "queue.sqlite3").is_file()
    requests = update_driver.status(root=queue_root)
    assert len(requests) == 1
    assert requests[0].spec.backend == "engine-reconcile-plugins"
    assert requests[0].state == "completed"
