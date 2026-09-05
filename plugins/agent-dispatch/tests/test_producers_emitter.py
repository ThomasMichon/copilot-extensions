"""Tests for lease-gated periodic command emitters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_dispatch.producers import emitter


class FakeClient:
    def __init__(self, *, granted: bool = True):
        self.granted = granted
        self.calls = []
        self.created = []

    def acquire_schedule_lease(self, scope, holder, **kwargs):
        self.calls.append((scope, holder, kwargs))
        return {
            "granted": self.granted,
            "lease": {"scope": scope, "holder": holder if self.granted else "other"},
        }

    def create(self, title, **kwargs):
        task = {"id": f"t-{len(self.created) + 1}", "title": title, **kwargs}
        self.created.append(task)
        return task


def _spec(**over):
    spec = {
        "id": "review-inbox",
        "command": ["review-emitter", "tick"],
        "interval_seconds": 3600,
    }
    spec.update(over)
    return spec


def test_run_tick_acquires_lease_and_runs_command(monkeypatch):
    client = FakeClient()
    calls = []
    times = iter([10.0, 12.5])
    monkeypatch.setattr(
        emitter, "no_window_kwargs", lambda: {"creationflags": 0x08000000}
    )

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    result = emitter.run_tick(
        client, _spec(cwd="/repo", env={"MODE": "test"}),
        holder="host-a", runner=runner, clock=lambda: next(times),
    )

    assert client.calls[0][0:2] == ("emitter:review-inbox", "host-a")
    assert calls[0][0] == ["review-emitter", "tick"]
    assert calls[0][1]["cwd"] == "/repo"
    assert calls[0][1]["env"]["MODE"] == "test"
    assert calls[0][1]["creationflags"] == 0x08000000
    assert result["held"] is True
    assert result["returncode"] == 0
    assert result["duration_seconds"] == 2.5


def test_run_tick_idles_when_another_holder_owns_lease():
    client = FakeClient(granted=False)
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True

    result = emitter.run_tick(client, _spec(), holder="host-a", runner=runner)
    assert result["held"] is False
    assert called is False
    assert result["lease"]["holder"] == "other"
    assert result["created"] == []


def test_run_tick_preserves_literal_braces_in_existing_commands():
    client = FakeClient()
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    emitter.run_tick(
        client,
        _spec(command=["review-emitter", "--query", '{"state":"open"}']),
        holder="host-a",
        runner=runner,
    )
    assert calls[0] == ["review-emitter", "--query", '{"state":"open"}']


def test_run_tick_expands_runtime_python_token():
    client = FakeClient()
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    emitter.run_tick(
        client,
        _spec(command=["{python}", "reviewer.py"]),
        holder="host-a",
        runner=runner,
    )
    assert calls[0][0] == emitter.sys.executable


@pytest.mark.parametrize(
    "spec, needle",
    [
        ({"command": ["tick"], "interval_seconds": 1}, "id"),
        (_spec(command="tick"), "list"),
        (_spec(interval_seconds=0), "> 0"),
        (_spec(env={"COUNT": 1}), "string"),
    ],
)
def test_validate_spec_rejects_malformed_specs(spec, needle):
    with pytest.raises(emitter.EmitterError) as exc:
        emitter.validate_spec(spec)
    assert needle in str(exc.value)


def test_run_tick_authors_json_tasks_with_emitter_provenance():
    client = FakeClient()
    spec = _spec(task_output="json", evaluator_ref="review-loop")

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"title":"review o/n#7","repo":"o/n","dedup_key":"review:o/n#7"}',
        )

    result = emitter.run_tick(client, spec, holder="host-a", runner=runner)
    assert result["created"][0]["source"] == "emitter"
    assert result["created"][0]["origin_ref"] == "review-inbox"
    assert result["created"][0]["evaluator_ref"] == "review-loop"


def test_run_tick_uses_configured_task_source():
    client = FakeClient()
    spec = _spec(task_output="json", source="repository-backlog")

    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout='{"title":"work"}')

    result = emitter.run_tick(client, spec, holder="host-a", runner=runner)
    assert result["created"][0]["source"] == "repository-backlog"


def test_run_tick_dispatches_builtin_repository_issue_loop(monkeypatch):
    client = FakeClient()
    config = {"kind": "repository-issue-loop"}
    spec = _spec(command=None, repository_issue_loop=config)
    observed = {}

    monkeypatch.setattr(emitter, "validate_spec", lambda _spec: None)

    def fake_run_tick(actual_client, actual_config, **kwargs):
        observed.update(
            client=actual_client,
            config=actual_config,
            kwargs=kwargs,
        )
        return {"created": [{"id": "task-1"}]}

    monkeypatch.setattr(
        "agent_dispatch.repository_issue_loops.run_tick", fake_run_tick
    )
    times = iter([10.0, 12.0])

    result = emitter.run_tick(
        client, spec, holder="host-a", clock=lambda: next(times)
    )

    assert observed["client"] is client
    assert observed["config"] is config
    assert result["created"] == [{"id": "task-1"}]
    assert result["duration_seconds"] == 2.0


def test_run_tick_accepts_empty_json_task_list_as_noop():
    client = FakeClient()

    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="[]")

    result = emitter.run_tick(
        client,
        _spec(task_output="json"),
        holder="host-a",
        runner=runner,
    )
    assert result["created"] == []


def test_registered_side_load_uses_same_task_contract_and_association():
    client = FakeClient()
    registration = {
        "id": "emitter-reg",
        "kind": "emitter",
        "spec": _spec(
            task_output="json",
            evaluator_ref="review-loop",
            side_load={"command": ["review-emitter", "side-load", "{change_ref}"]},
        ),
    }
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout='{"title":"review o/n#9","repo":"o/n","dedup_key":"review:o/n#9"}',
            stderr="",
        )

    out = emitter.run_side_load(
        client, registration, "o/n#9", runner=runner
    )
    assert calls == [["review-emitter", "side-load", "o/n#9"]]
    assert out["created"][0]["source"] == "emitter"
    assert out["created"][0]["origin_ref"] == "review-inbox"
    assert out["created"][0]["evaluator_ref"] == "review-loop"


def test_registered_side_load_rejects_wrong_host():
    registration = {
        "id": "emitter-reg",
        "kind": "emitter",
        "machine": "host-a",
        "env": "default",
        "spec": _spec(
            side_load={"command": ["review-emitter", "{change_ref}"]}
        ),
    }
    with pytest.raises(emitter.EmitterError, match="host-a"):
        emitter.run_side_load(
            FakeClient(),
            registration,
            "o/n#9",
            current_machine="host-b",
        )


def test_unassociated_emitter_cannot_spoof_evaluator_ref():
    client = FakeClient()
    spec = _spec(task_output="json")
    emitter._author_tasks(
        client,
        spec,
        '{"title":"x","repo":"o/n","evaluator_ref":"other-loop"}',
    )
    assert client.created[0]["evaluator_ref"] is None


def test_registered_side_load_accepts_null_env():
    client = FakeClient()
    registration = {
        "id": "emitter-reg",
        "kind": "emitter",
        "env": "default",
        "spec": _spec(
            env=None,
            side_load={"command": ["review-emitter", "{change_ref}"]},
        ),
    }

    def runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='{"title":"x","repo":"o/n"}',
            stderr="",
        )

    assert emitter.run_side_load(
        client,
        registration,
        "o/n#9",
        current_env="default",
        runner=runner,
    )["created"]
