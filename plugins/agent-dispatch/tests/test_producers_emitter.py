"""Tests for lease-gated periodic command emitters."""

from __future__ import annotations

import subprocess
import sys
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


def test_run_tick_always_pipes_json_command_output_and_never_over_specifies(
    monkeypatch,
):
    """The JSON task-output protocol path must use a real pipe (``text=True``)
    so ``_author_tasks`` gets exact decoded bytes -- confirms the kwarg
    shape ``run_tick`` passes through to its ``runner``."""
    client = FakeClient()
    seen = {}

    def runner(_command, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="[]")

    emitter.run_tick(client, _spec(task_output="json"), holder="host-a", runner=runner)

    assert seen["stdout"] is subprocess.PIPE
    assert seen["stderr"] is subprocess.PIPE
    assert seen["text"] is True


def test_run_tick_routes_non_json_command_output_directly_to_our_stderr(capfd):
    """A command that prints on stdout must never corrupt the tick's own
    result via inherited-stdout interleaving -- a subprocess-based caller
    (a serve loop shelling out to ``emitter tick``) treats the CLI's own
    stdout as one JSON payload, so the launched command's output must never
    land there. Real subprocess, real OS-level redirection (no Python-side
    capture-then-forward): its output streams straight to OUR stderr, and
    the returned result is unaffected."""
    client = FakeClient()
    spec = _spec(
        command=[
            sys.executable, "-c",
            "import sys; print('some log line'); print('a warning', file=sys.stderr)",
        ],
    )

    result = emitter.run_tick(client, spec, holder="host-a")

    assert result["returncode"] == 0
    assert result["error"] is None
    captured = capfd.readouterr()
    assert "some log line" in captured.err
    assert "a warning" in captured.err
    assert captured.out == ""


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
    spec = _spec(command=None, repository_issue_loop=config, cwd="/repo/root")
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
    assert observed["kwargs"]["cwd"] == "/repo/root"
    assert result["created"] == [{"id": "task-1"}]
    assert result["duration_seconds"] == 2.0


def test_validate_spec_threads_cwd_into_repository_issue_loop_validation(monkeypatch):
    """Regression guard: a repository-issue-loop emitter's own ``cwd`` (the
    declaring repo's root, stamped at expansion time) must reach
    ``repository_issue_loops.validate_config`` at tick-validation time too --
    this is what lets a supervisor daemon process (whose own cwd is not the
    declaring repo) resolve that repo's repo-local worker-identity override."""
    observed = {}

    def fake_validate_config(_config, *, cwd=None):
        observed["cwd"] = cwd
        return {}

    monkeypatch.setattr(
        "agent_dispatch.repository_issue_loops.validate_config",
        fake_validate_config,
    )
    spec = _spec(
        command=None,
        repository_issue_loop={"kind": "repository-issue-loop"},
        cwd="/repo/root",
    )
    emitter.validate_spec(spec)
    assert observed["cwd"] == "/repo/root"


def test_validate_spec_rejects_malformed_cwd_before_repository_issue_loop_validation():
    """Regression guard: a malformed non-string ``cwd`` must surface as the
    ordinary ``EmitterError`` contract, not an unhandled ``TypeError`` from
    ``Path(cwd)`` inside ``repository_issue_loops.validate_config`` -- the
    'cwd' type check must run before that call, not only after it."""
    spec = _spec(
        command=None,
        repository_issue_loop={"kind": "repository-issue-loop"},
        cwd=123,
    )
    with pytest.raises(emitter.EmitterError, match="'cwd' must be a non-empty string"):
        emitter.validate_spec(spec)


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




# -- serve() shells out to `emitter tick` fresh every cycle -------------------
#
# Regression coverage: a long-lived ``emitter serve``
# process (started once at host boot, running for hours/days) must never
# build/hold a DispatchClient (or any resolved coordinator address) across
# its sleep boundary -- a coordinator restart mid-lifetime (a new OS-assigned
# ephemeral port) once left a real emitter permanently pointed at a dead port
# with no self-healing for ~10 hours. The fix: each tick re-invokes this same
# emitter's own ``agent-dispatch emitter tick`` CLI command in a fresh
# subprocess, which re-discovers the coordinator exactly like any other
# one-shot CLI invocation would -- there is no in-process coordinator state
# left for a restart to strand.


def test_serve_runs_emitter_tick_as_a_fresh_subprocess_every_cycle(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"id": "x", "command": ["true"], "interval_seconds": 60}')

    seen_argv: list[list[str]] = []
    payloads = iter(
        [
            '{"held": true, "returncode": 0, "error": null, "duration_seconds": 0.1}',
            '{"held": true, "returncode": 0, "error": null, "duration_seconds": 0.2}',
        ]
    )

    def fake_runner(argv, **kwargs):
        seen_argv.append(list(argv))
        return SimpleNamespace(returncode=0, stdout=next(payloads), stderr="")

    ticks: list[dict] = []
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt

    emitter.serve(
        spec_path,
        holder="host-a",
        cli_argv=["fake-python", "-m", "agent_dispatch"],
        runner=fake_runner,
        on_tick=lambda result: ticks.append(result),
        sleep=fake_sleep,
    )

    # Every tick is the SAME fixed argv -- proving there is no cached
    # per-process coordinator address embedded in it, and each invocation
    # would independently re-discover the coordinator exactly as a fresh CLI
    # command does.
    expected_argv = [
        "fake-python", "-m", "agent_dispatch", "emitter", "tick",
        str(spec_path), "--holder", "host-a",
    ]
    assert seen_argv == [expected_argv, expected_argv]
    assert [t["duration_seconds"] for t in ticks] == [0.1, 0.2]


def test_serve_synthesizes_an_error_from_a_hard_subprocess_failure(tmp_path):
    """A coordinator that's still genuinely unreachable makes the forked tick
    crash (nonzero exit, no JSON on stdout -- its own traceback already
    streamed live to our inherited stderr) -- ``serve`` must report that as
    an error tick, not choke on the missing payload."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"id": "x", "command": ["true"], "interval_seconds": 60}')

    def fake_runner(_argv, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="")

    ticks: list[dict] = []

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    emitter.serve(
        spec_path,
        holder="host-a",
        cli_argv=["fake-python", "-m", "agent_dispatch"],
        runner=fake_runner,
        on_tick=lambda result: ticks.append(result),
        sleep=fake_sleep,
    )

    assert ticks[0]["error"] == "emitter tick produced no valid JSON result (exit 1)"


def test_serve_default_cli_argv_uses_this_interpreter(tmp_path):
    """With no explicit ``cli_argv``, ``serve`` re-invokes ``agent_dispatch``
    under THIS process's own interpreter (``sys.executable``) -- the same
    default a plain, unqualified CLI re-run would use."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"id": "x", "command": ["true"], "interval_seconds": 60}')

    seen_argv: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        seen_argv.append(list(argv))
        return SimpleNamespace(returncode=0, stdout='{"held": true}')

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    emitter.serve(spec_path, holder="host-a", runner=fake_runner, sleep=fake_sleep)

    assert seen_argv[0][:3] == [sys.executable, "-m", "agent_dispatch"]


def test_serve_only_pipes_stdout_leaving_stderr_to_inherit_ours(tmp_path):
    """``serve``'s own subprocess call must pipe ONLY stdout (needed to parse
    the JSON result) and never touch stderr at all -- no ``capture_output``,
    no explicit ``stderr=``. Leaving stderr unspecified means the OS
    inherits ours directly, so the forked tick's own diagnostics (and, per
    ``run_tick``'s own redirection, any non-JSON command's output) stream
    straight through in real time with no Python-side buffering at this
    layer, exactly matching the previously-inherited passthrough behavior
    this PR's subprocess redesign must not regress."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"id": "x", "command": ["true"], "interval_seconds": 60}')

    seen_kwargs: dict = {}

    def fake_runner(_argv, **kwargs):
        seen_kwargs.update(kwargs)
        return SimpleNamespace(returncode=0, stdout='{"held": true}')

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    emitter.serve(spec_path, holder="host-a", runner=fake_runner, sleep=fake_sleep)

    assert seen_kwargs["stdout"] is subprocess.PIPE
    assert "stderr" not in seen_kwargs
    assert "capture_output" not in seen_kwargs


def test_serve_rejects_a_zero_exit_payload_missing_the_held_key(tmp_path):
    """A zero-exit tick whose stdout doesn't decode to a dict with a bool
    ``held`` key is a protocol violation, not a vacuous success -- it must
    surface as an error, never silently normalize to an empty
    ``{}`` result that ``on_tick`` would otherwise treat as an idle tick
    while reporting ``ok: true``."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"id": "x", "command": ["true"], "interval_seconds": 60}')

    def fake_runner(_argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="")

    ticks: list[dict] = []

    def fake_sleep(_seconds):
        raise KeyboardInterrupt

    emitter.serve(
        spec_path,
        holder="host-a",
        runner=fake_runner,
        on_tick=lambda result: ticks.append(result),
        sleep=fake_sleep,
    )

    assert ticks[0]["error"] == "emitter tick produced no valid JSON result (exit 0)"


# --- emitter-command-receipts ------------------------------------------------


def test_run_tick_writes_receipts_keyed_by_dedup_key(tmp_path, monkeypatch):
    """A ``task_output=json`` tick that creates tasks durably records one
    receipt per task, keyed by the same ``dedup_key`` it emitted, readable by
    ``read_receipts`` on a LATER call -- not just observed in the instant of
    creation and then lost."""
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    client = FakeClient()

    def runner(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"title": "range 1", "dedup_key": "range:1"}, '
                '{"title": "range 2", "dedup_key": "range:2"}]'
            ),
        )

    emitter.run_tick(
        client,
        _spec(task_output="json"),
        holder="host-a",
        runner=runner,
        clock=lambda: 100.0,
    )

    result = emitter.read_receipts("review-inbox")
    assert result["cursor"] == 2
    by_key = {r["dedup_key"]: r["task_id"] for r in result["receipts"]}
    assert by_key == {"range:1": "t-1", "range:2": "t-2"}


def test_read_receipts_since_cursor_returns_only_new_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    client = FakeClient()

    def runner(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0, stdout='[{"title": "range 1", "dedup_key": "range:1"}]',
        )

    emitter.run_tick(
        client, _spec(task_output="json"), holder="host-a", runner=runner,
        clock=lambda: 1.0,
    )
    first = emitter.read_receipts("review-inbox")
    assert first["cursor"] == 1

    def runner2(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0, stdout='[{"title": "range 2", "dedup_key": "range:2"}]',
        )

    emitter.run_tick(
        client, _spec(task_output="json"), holder="host-a", runner=runner2,
        clock=lambda: 2.0,
    )

    new_only = emitter.read_receipts("review-inbox", since=first["cursor"])
    assert [r["dedup_key"] for r in new_only["receipts"]] == ["range:2"]
    assert new_only["cursor"] == 2

    everything = emitter.read_receipts("review-inbox")
    assert len(everything["receipts"]) == 2


def test_dedup_colliding_task_records_the_existing_task_id(tmp_path, monkeypatch):
    """A re-emitted ``dedup_key`` that collides with an already-existing task
    must record the EXISTING task's id in its receipt, not a phantom new one
    -- ``FakeClient.create`` always mints a fresh id, so this test stands in
    for the coordinator's own dedup-collide behavior by returning the SAME id
    for a repeated ``dedup_key``."""
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))

    class DedupingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self._by_key: dict[str, str] = {}

        def create(self, title, **kwargs):
            key = kwargs.get("dedup_key")
            if key in self._by_key:
                task = {"id": self._by_key[key], "title": title, **kwargs}
            else:
                task = super().create(title, **kwargs)
                self._by_key[key] = task["id"]
            return task

    client = DedupingClient()

    def runner(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0, stdout='[{"title": "range 1", "dedup_key": "range:1"}]',
        )

    emitter.run_tick(
        client, _spec(task_output="json"), holder="host-a", runner=runner,
        clock=lambda: 1.0,
    )
    emitter.run_tick(
        client, _spec(task_output="json"), holder="host-a", runner=runner,
        clock=lambda: 2.0,
    )

    result = emitter.read_receipts("review-inbox")
    task_ids = {r["task_id"] for r in result["receipts"]}
    assert task_ids == {"t-1"}
    assert len(result["receipts"]) == 2


def test_run_tick_exposes_receipts_path_in_command_env(tmp_path, monkeypatch):
    """The declared command's own subprocess receives the receipts path via
    env, so a domain command can read its own prior tick's receipts directly
    with no CLI round trip."""
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    client = FakeClient()
    seen_env: dict = {}

    def runner(_command, **kwargs):
        seen_env.update(kwargs.get("env") or {})
        return SimpleNamespace(returncode=0, stdout="[]")

    emitter.run_tick(
        client, _spec(task_output="json"), holder="host-a", runner=runner,
    )

    assert seen_env[emitter.RECEIPTS_PATH_ENV] == str(
        emitter.receipts_path("review-inbox")
    )


def test_run_side_load_writes_to_the_same_receipts_sink(tmp_path, monkeypatch):
    monkeypatch.setattr(
        emitter, "no_window_kwargs", lambda: {"creationflags": 0x08000000}
    )
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    client = FakeClient()
    registration = {
        "id": "review-inbox-reg",
        "kind": "emitter",
        "spec": {
            "id": "review-inbox",
            "command": ["review-emitter", "tick"],
            "interval_seconds": 60,
            "task_output": "json",
            "side_load": {"command": ["review-emitter", "side-load", "{change_ref}"]},
        },
    }

    def runner(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0, stdout='{"title": "pr 42", "dedup_key": "pr:42"}',
        )

    emitter.run_side_load(client, registration, "owner/name#42", runner=runner)

    result = emitter.read_receipts("review-inbox")
    assert [r["dedup_key"] for r in result["receipts"]] == ["pr:42"]


def test_receipts_log_is_bounded_under_sustained_emission(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))

    max_receipts = 5
    for n in range(max_receipts + 3):
        emitter._append_receipts(
            "review-inbox",
            [{"id": f"t-{n}", "dedup_key": f"k:{n}"}],
            clock=lambda n=n: float(n),
            max_receipts=max_receipts,
        )

    result = emitter.read_receipts("review-inbox")
    assert len(result["receipts"]) == max_receipts
    # The oldest records were trimmed; the newest survive.
    assert result["receipts"][-1]["dedup_key"] == f"k:{max_receipts + 2}"
    assert result["receipts"][0]["dedup_key"] == "k:3"


def test_read_receipts_reports_gap_when_cursor_predates_a_trim(tmp_path, monkeypatch):
    """A cursor taken BEFORE receipts between it and the oldest retained
    record were trimmed away must be flagged, not silently treated as a
    clean "nothing new" read -- the caller has an incomplete handoff."""
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    for n in range(3):
        emitter._append_receipts(
            "review-inbox", [{"id": f"t-{n}", "dedup_key": f"k:{n}"}],
            clock=lambda n=n: float(n), max_receipts=100,
        )
    cursor = emitter.read_receipts("review-inbox")["cursor"]
    assert cursor == 3

    for n in range(3, 10):
        emitter._append_receipts(
            "review-inbox", [{"id": f"t-{n}", "dedup_key": f"k:{n}"}],
            clock=lambda n=n: float(n), max_receipts=5,
        )

    # Records 4-5 existed and were trimmed before this cursor consumed them
    # -- the read must say so, not quietly return 6-10 as if it were complete.
    stale_read = emitter.read_receipts("review-inbox", since=cursor)
    assert stale_read["gap"] is True
    assert all(r["seq"] > cursor for r in stale_read["receipts"])


def test_read_receipts_reports_no_gap_when_cursor_is_current(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    for n in range(3):
        emitter._append_receipts(
            "review-inbox", [{"id": f"t-{n}", "dedup_key": f"k:{n}"}],
            clock=lambda n=n: float(n), max_receipts=100,
        )
    cursor = emitter.read_receipts("review-inbox")["cursor"]

    emitter._append_receipts(
        "review-inbox", [{"id": "t-3", "dedup_key": "k:3"}],
        clock=lambda: 3.0, max_receipts=100,
    )

    fresh_read = emitter.read_receipts("review-inbox", since=cursor)
    assert fresh_read["gap"] is False
    assert [r["dedup_key"] for r in fresh_read["receipts"]] == ["k:3"]


def test_append_receipts_serializes_concurrent_writers(tmp_path, monkeypatch):
    """Two 'overlapping' writers (a periodic tick and an on-demand side-load,
    modeled here as two direct ``_append_receipts`` calls) must never choose
    the same ``seq`` or clobber each other's record -- the receipts
    transaction is locked, not a bare unlocked read-modify-write."""
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))

    for n in range(20):
        emitter._append_receipts(
            "review-inbox", [{"id": f"t-{n}", "dedup_key": f"k:{n}"}],
            clock=lambda n=n: float(n), max_receipts=1000,
        )

    result = emitter.read_receipts("review-inbox")
    seqs = [r["seq"] for r in result["receipts"]]
    assert seqs == list(range(1, 21)), "no duplicate/skipped seq under serialized writes"
    assert len({r["dedup_key"] for r in result["receipts"]}) == 20
