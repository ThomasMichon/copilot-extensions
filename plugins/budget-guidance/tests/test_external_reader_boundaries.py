from datetime import datetime, timezone
from decimal import Decimal
import json
import os

import pytest

from budget_guidance.cli import main
from budget_guidance.external_reader import acquire_config
from budget_guidance.models import ModelError, load_json
from budget_guidance.posture import build_posture
from test_external_reader import _config, _posture, _reader, _reading


def test_unrepresentable_decimal_reader_is_redacted(tmp_path):
    body = json.dumps(_reading()).replace('"allowance": 100', '"allowance": 1e999999999999999999999')
    config = acquire_config(_config(), {"report": _reader(tmp_path, f"print({body!r})")})
    posture = _posture(config)
    assert posture["fields"] == {}
    assert posture["adapters"][0]["error"] == "reader returned an invalid budget reading"
    assert "999999999999999999999" not in json.dumps(posture)


def test_unrepresentable_decimal_file_uses_model_error(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text('{"secret": 1e999999999999999999999}')
    with pytest.raises(ModelError):
        load_json(path)


def test_acquired_capture_later_than_evaluation_withholds_all_fields(tmp_path):
    body = json.dumps(_reading(captured_at="2026-01-01T00:00:01Z"))
    config = acquire_config(_config(), {"report": _reader(tmp_path, f"print({body!r})")})
    posture = _posture(config)
    assert posture["calculated"] is None
    assert posture["fields"] == {}
    state = posture["adapters"][0]
    assert state["availability"] == "error"
    assert state["freshness"] == "future"
    assert state["captured_at"] == "2026-01-01T00:00:01Z"
    assert state["error"] == "reader capture time is later than evaluation"
    assert build_posture(
        config, datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )["calculated"]["remaining"] == "60"


def test_manual_future_capture_keeps_existing_static_semantics():
    from budget_guidance.models import BudgetConfig, StaticAdapter, parse_reading

    reading = json.loads(
        json.dumps(_reading(captured_at="2026-01-01T00:00:01Z")), parse_int=Decimal,
    )
    assert _posture(BudgetConfig((
        StaticAdapter("manual", 1, parse_reading(reading)),
    )))["calculated"]["remaining"] == "60"


def test_default_cli_evaluation_follows_acquisition(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema": "copilot-extensions.budget-guidance-config", "version": 1,
        "adapters": [{
            "type": "external-command", "id": "report", "authority": 10,
            "source": "account-reader",
        }],
    }))
    body = _reading(reset_at="9999-01-01T00:00:00Z")
    code = (
        "import json,time; from datetime import datetime,timezone; time.sleep(0.05); "
        f"body={body!r}; body['captured_at']=datetime.now(timezone.utc).isoformat(); "
        "print(json.dumps(body))"
    )
    reader = _reader(tmp_path, code)
    args = [
        "status", "--config", str(config), "--json", "--reader-id", "report",
        "--reader-cwd", str(tmp_path),
    ]
    assert main([*args, "--reader-command", *reader.argv]) == 0
    posture = json.loads(capsys.readouterr().out)
    assert posture["calculated"] is not None
    assert posture["evaluated_at"] >= posture["adapters"][0]["captured_at"]
    assert main([
        *args, "--at", "2026-01-01T00:00:00Z", "--reader-command", *reader.argv,
    ]) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["fields"] == {}
    assert explicit["adapters"][0]["freshness"] == "future"


@pytest.mark.skipif(os.name == "nt", reason="POSIX cleanup acknowledgment")
def test_unexpected_host_exit_at_cleanup_cannot_report_success(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    spawn = module.spawn_sync_in_kill_on_close_job

    def failed_exit(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        wait = process.wait

        def unexpected_exit(*args, **kwargs):
            wait(*args, **kwargs)
            return 137

        process.wait = unexpected_exit
        return process, job

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", failed_exit)
    config = acquire_config(_config(), {
        "report": _reader(tmp_path, f"print({json.dumps(_reading())!r})"),
    })
    posture = _posture(config)
    assert posture["calculated"] is None
    assert "cleanup could not be confirmed" in posture["adapters"][0]["error"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX explicit cleanup acknowledgment")
def test_zero_host_exit_without_cleanup_acknowledgment_cannot_report_success(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    read_status = module._read_status

    def missing_acknowledgment(stream, status, cleanup):
        discarded = module.Queue()
        read_status(stream, status, discarded)
        assert discarded.get(timeout=1) == b"cleaned\n"
        cleanup.put(b"")

    monkeypatch.setattr(module, "_read_status", missing_acknowledgment)
    config = acquire_config(_config(), {
        "report": _reader(tmp_path, f"print({json.dumps(_reading())!r})"),
    })
    posture = _posture(config)
    assert posture["calculated"] is None
    assert "cleanup was not acknowledged" in posture["adapters"][0]["error"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX explicit cleanup acknowledgment")
def test_host_exit_between_poll_and_control_write_cannot_report_success(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    spawn = module.spawn_sync_in_kill_on_close_job

    def tracked_spawn(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        original = process.stdin

        class LostControl:
            def write(self, data):
                original.close()
                assert process.wait(timeout=5) == 0
                raise BrokenPipeError("private-control-detail")

            def flush(self):
                pytest.fail("lost control pipe was flushed")

            def close(self):
                original.close()

        process.stdin = LostControl()
        return process, job

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", tracked_spawn)
    config = acquire_config(_config(), {
        "report": _reader(tmp_path, f"print({json.dumps(_reading())!r})"),
    })
    posture = _posture(config)
    assert posture["calculated"] is None
    assert "cleanup could not be confirmed" in posture["adapters"][0]["error"]
    assert "private-control-detail" not in json.dumps(posture)
