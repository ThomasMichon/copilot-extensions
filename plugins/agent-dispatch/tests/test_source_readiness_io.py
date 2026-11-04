from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_source_supervisor_startup import _sources


@pytest.mark.parametrize("observation", ["transient", "persistent", "corrupt"])
def test_readiness_retains_unavailable_observation_until_valid_or_deadline(tmp_path, monkeypatch, observation):
    from agent_dispatch import companion, config, single_instance, source_start, supervisor_source, supervisor_status_io

    _sources(monkeypatch)
    (tmp_path / "supervisor.env").write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n", encoding="utf-8")
    plan = source_start.supervisor_plan(tmp_path)
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path / "control")
    monkeypatch.setattr(single_instance, "is_locked", lambda _: True)
    monkeypatch.setattr(single_instance, "read_holder_pid", lambda _: 41)
    monkeypatch.setattr(companion, "process_start_token", lambda _: "birth")
    monkeypatch.setattr(supervisor_source, "read_prepared", lambda *_args, **_kwargs: {
        "generation": {"pid": 41}, "settings": supervisor_source.effective_settings(plan.args),
    })
    monkeypatch.setattr(supervisor_source, "spawn_candidate", lambda *_args, **_kwargs: pytest.fail("unknown I/O is not absence"))
    clock, calls = [0.0], []
    monkeypatch.setattr(source_start, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda duration: clock.__setitem__(0, clock[0] + duration),
    ))

    def read(_path):
        calls.append(True)
        if observation == "corrupt":
            return None, "status-json-invalid"
        if observation == "persistent" or len(calls) <= 2:
            return None, "status-read-unavailable"
        return {
            "generation": {"pid": 41, "start_token": "birth"},
            "runtime_version": source_start.__version__, "cycle_id": 1,
            "updated_at": 2, "cycle_started_at": 1,
        }, None

    monkeypatch.setattr(supervisor_status_io, "read_status", read)
    if observation == "transient":
        assert source_start.ensure_supervisor(tmp_path, plan=plan) == {"status": "ready", "pid": 41}
        assert len(calls) == 3 and clock[0] > 0
    else:
        with pytest.raises(RuntimeError, match="status-json-invalid" if observation == "corrupt" else "status-read-unavailable"):
            source_start.ensure_supervisor(tmp_path, plan=plan)
        if observation == "persistent":
            assert 60 <= clock[0] < 61
        else:
            assert len(calls) == 1 and clock[0] == 0
