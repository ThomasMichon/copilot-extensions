"""Optional `codespaces.lifecycle.v1` records for finite CLI runs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_codespaces import config, consumer_telemetry as ct, copilot_detach, copilot_venue
from process_telemetry.finite_owner import capability

# The detached-launch seams (fixture and fake SSH session) are shared with the
# launch tests rather than copied.
def _load_launch_tests(name: str = "test_copilot_detach.py") -> Any:
    spec = importlib.util.spec_from_file_location(
        "_codespaces_ + Path(name).stem", Path(__file__).with_name(name),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_launch_tests: Any = _load_launch_tests()
seams = _launch_tests.seams
_portable_tests: Any = _load_launch_tests("test_portable_mux_launch.py")
flow = _portable_tests.flow

_RECEIVER = """\
import json, sys
from pathlib import Path
with Path("readback.jsonl").open("a", encoding="utf-8") as output:
    for line in sys.stdin:
        output.write(json.dumps(json.loads(line)) + "\\n")
"""


@pytest.fixture(autouse=True)
def _clean_recorder() -> Any:
    ct.reset()
    yield
    ct.reset()


def _admit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "harness root"
    root.mkdir()
    (root / "receiver.py").write_text(_RECEIVER, encoding="utf-8")
    (root / ".agent-telemetry").mkdir()
    (root / ".agent-telemetry" / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [sys.executable, "-I", "receiver.py"]},
    }), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "consumer-telemetry.json").write_text(json.dumps({
        "schema_version": 1, "root": str(root),
    }), encoding="utf-8")
    monkeypatch.setattr(config, "RUNTIME_DIR", runtime)
    return root


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "readback.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        assert (record["source"], record["source_schema"]) == (
            "agent-codespaces", "codespaces.lifecycle.v1",
        )
    return [record["event"] for record in records]


def _shape(event: dict[str, Any]) -> tuple[Any, ...]:
    return (event["stage"], event["outcome"], event["reason"], event["attempt"])


def test_a_failed_detached_launch_reaches_a_real_receiver_without_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _admit(tmp_path, monkeypatch)

    def launch() -> int:
        ct.start_launch()
        ct.begin("codespace_lookup")
        ct.begin("ssh")
        ct.retry("transport_retry")
        ct.begin("remote_embody")
        ct.fail(copilot_detach.embody_reason(
            "", "bash: agent-worktrees: command not found in secret-codespace",
        ))
        return 1

    assert ct.instrument(launch)() == 1
    events = _events(root)
    assert [_shape(e) for e in events] == [
        ("codespace_lookup", "success", "none", 1),
        ("ssh", "retry", "transport_retry", 1),
        ("ssh", "success", "none", 2),
        ("remote_embody", "failure", "installer_unavailable", 1),
        ("launch", "failure", "installer_unavailable", 1),
    ]
    assert all(type(e["duration_ms"]) is int for e in events)
    text = json.dumps(events)
    assert "secret" not in text and str(tmp_path) not in text


def test_legacy_runtime_offers_nothing_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)

    def launch() -> int:
        ct.note("seed_validation", "failure", "seed_too_long")
        return 2

    assert ct.instrument(launch)() == 2
    assert list(tmp_path.iterdir()) == []


def test_a_run_without_stages_never_reads_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct, "_offer", lambda events: pytest.fail("offered an empty run"))
    assert ct.instrument(lambda: 0)() == 0


def test_receiver_failure_and_interrupts_keep_the_product_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _admit(tmp_path, monkeypatch)
    (root / ".agent-telemetry" / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [str(tmp_path / "missing-receiver")]},
    }), encoding="utf-8")

    def interrupted() -> int:
        ct.begin("bridge_registration")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ct.instrument(interrupted)()

    def exits() -> int:
        ct.note("claim", "failure", "claim_busy")
        raise SystemExit(4)

    with pytest.raises(SystemExit) as raised:
        ct.instrument(exits)()
    assert raised.value.code == 4


@pytest.mark.parametrize("message, expected", [
    ("Credential relay is not ready on the host", "relay_not_listening"),
    ("fatal: Authentication failed for 'https://example.invalid/secret'", "clone_auth_failed"),
    ("fatal: could not read Username for 'https://example.invalid'", "clone_auth_failed"),
    ("plugin marketplace add failed: exit 1", "marketplace_add_failed"),
    ("Required target plugin preparation failed: secret-plugin", "plugin_install_failed"),
    ("something unexpected", "other"),
])
def test_plugin_preparation_reasons_are_closed(message: str, expected: str) -> None:
    assert ct.preparation_reason(RuntimeError(message)) == expected


def test_preparation_and_embody_classifiers_cover_interrupts_and_python() -> None:
    assert ct.preparation_reason(KeyboardInterrupt()) == "interrupted"
    assert ct.preparation_reason(ValueError("x")) == "other"
    assert copilot_detach.embody_reason("", "bash: python3: command not found") == (
        "bootstrap_python_unavailable"
    )
    assert copilot_detach.embody_reason("boom", "") == "other"


def test_relay_reason_maps_preflight_codes() -> None:
    assert ct.relay_reason("github-credential-unavailable") == "credential_fill_failed"
    assert ct.relay_reason(None) == "credential_fill_failed"


def test_note_bounds_attempt_and_limit() -> None:
    ct.note("account_resolution", "timeout", "snapshot_timeout",
            started=ct.clock(), attempt=10**9, limit_ms=30000)
    ct.note("not_a_stage", "failure", "other")
    (event,) = ct.finish(1)
    assert event["attempt"] == 2**16 and event["limit_ms"] == 30000
    assert "duration_ms" in event


def _claim(monkeypatch: pytest.MonkeyPatch, effect: Any, *, force: bool = False) -> Any:
    from agent_codespaces import lease

    def claim_for_connect(*args: Any, **kwargs: Any) -> None:
        if effect is not None:
            raise effect

    monkeypatch.setattr(lease, "claim_for_connect", claim_for_connect)
    args = argparse.Namespace(name="secret-codespace", force_claim=force, effort=None)
    result = copilot_venue.claim_or_exit_code(args, launch_policy=False)
    return result, [_shape(e)[:3] for e in ct.finish(0 if result is None else 1)]


def test_claim_outcomes_are_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_codespaces.lease import ClaimConflict, CoordinationRejected

    busy, events = _claim(monkeypatch, ClaimConflict("cs", "holder", "host", 1))
    assert busy is not None and events == [("claim", "failure", "claim_busy")]
    ct.reset()
    _, events = _claim(monkeypatch, CoordinationRejected("no state"))
    assert events == [("claim", "failure", "lease_conflict")]
    ct.reset()
    assert _claim(monkeypatch, None, force=True) == (None, [("claim", "success", "force_claimed")])
    ct.reset()
    assert _claim(monkeypatch, None) == (None, [("claim", "success", "none")])


def test_account_snapshot_timeout_is_noted_with_its_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_codespaces import gh_account, worktrees

    def slow(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("agent-worktrees", kwargs.get("timeout", 0))

    monkeypatch.setattr(worktrees, "explicit_context", lambda: True)
    monkeypatch.setattr(worktrees, "run", slow)
    with pytest.raises(gh_account.AccountInventoryError):
        gh_account._lookup("repos", "list", strict=True)
    (event,) = ct.finish(1)
    assert (event["stage"], event["outcome"], event["reason"]) == (
        "account_resolution", "timeout", "snapshot_timeout",
    )
    assert event["limit_ms"] == gh_account._SNAPSHOT_TIMEOUT_SECONDS * 1000


def test_console_entry_is_instrumented(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_codespaces import __main__ as cli

    seen: list[Any] = []

    def inner(argv: Any = None) -> int:
        ct.note("seed_validation", "failure", "seed_invalid")
        seen.append(argv)
        return 0

    offered: list[Any] = []
    monkeypatch.setattr(cli, "_main", inner)
    monkeypatch.setattr(ct, "_offer", offered.append)
    assert cli.main(["status"]) == 0
    assert seen == [["status"]] and offered[0][0]["reason"] == "seed_invalid"


def test_packaged_descriptor_names_only_the_codespaces_schema() -> None:
    path = Path(ct.__file__).parent / "metadata" / "consumer-telemetry-contract.json"
    assert json.loads(path.read_text(encoding="utf-8")) == capability([ct.SOURCE_SCHEMA])


def _launch_shapes() -> list[tuple[Any, ...]]:
    return [
        (r["stage"], r["outcome"], r["reason"], r["attempt"], r.get("duration_ms"))
        for r in ct._records if r["stage"] in ("ssh", "remote_embody")
    ]


def test_remote_embody_is_timed_around_the_remote_exec_only(
    seams: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(ct, "_now", lambda: now[0])
    inner = _launch_tests._ssh(seams, stdout=_launch_tests._CREATED)

    def session(ns: Any, **kwargs: Any) -> int:
        now[0] += 5.0  # connect and venue preparation precede the remote command
        ns.remote_exec_started()
        now[0] += 2.0  # the remote embody command itself
        sink = kwargs["result_sink"]

        def timed_sink(result: Any) -> int:
            out = int(sink(result))
            now[0] += 3.0  # teardown after the remote command returned
            return out

        return int(inner(ns, **{**kwargs, "result_sink": timed_sink}))

    copilot_detach.cmd_detach(_launch_tests._args(), ssh_session=session)
    assert [s for s in _launch_shapes() if s[0] == "remote_embody"] == [
        ("remote_embody", "success", "none", 1, 2000),
    ]


def test_a_final_transport_failure_is_not_overwritten_by_an_embody_result(seams: Any) -> None:
    ct.start_launch()
    rc = copilot_detach.cmd_detach(_launch_tests._args(), ssh_session=_launch_tests._ssh(
        seams, stderr="Connection reset by peer", code=255,
    ))
    assert rc == 1
    assert [s[:4] for s in _launch_shapes()] == [
        ("ssh", "retry", "transport_retry", 1),
        ("ssh", "failure", "transport_failed", 2),
    ]
    assert ct._last_reason == "transport_failed"


def test_a_zero_exit_malformed_snapshot_is_not_a_resolved_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_codespaces import gh_account, worktrees

    def malformed(*args: Any, **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(list(args), 0, stdout="[]", stderr="")

    monkeypatch.setattr(worktrees, "explicit_context", lambda: True)
    monkeypatch.setattr(worktrees, "run", malformed)
    with pytest.raises(gh_account.AccountInventoryError):
        gh_account._account_inventory()
    assert [_shape(e)[:3] for e in ct.finish(1)] == [
        ("account_resolution", "failure", "account_unresolved"),
    ]


def test_shared_preparation_notes_attached_and_detached_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from agent_codespaces import plugin_preparation

    async def refused(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("Required target plugin preparation failed: secret-plugin")

    monkeypatch.setattr(plugin_preparation, "_prepare_plugins", refused)
    with pytest.raises(RuntimeError):
        asyncio.run(plugin_preparation.prepare_plugins(
            object(), "secret-codespace", None, object(), "/workspaces/secret",
        ))
    assert [_shape(e)[:3] for e in ct.finish(1)] == [
        ("plugin_preparation", "failure", "plugin_install_failed"),
    ]


def test_a_preparation_failure_inside_the_launch_keeps_its_cause(seams: Any) -> None:
    def session(ns: Any, **kwargs: Any) -> int:
        ct.note("plugin_preparation", "failure", "plugin_install_failed")
        raise RuntimeError("Required target plugin preparation failed: secret-plugin")

    ct.start_launch()
    rc = copilot_detach.cmd_detach(_launch_tests._args(), ssh_session=session)
    assert rc == 1
    shapes = [_shape(e)[:3] for e in ct.finish(rc)]
    assert ("ssh", "failure", "plugin_install_failed") in shapes
    assert shapes[-1] == ("launch", "failure", "plugin_install_failed")


def test_an_enclosing_stage_closed_by_exit_keeps_the_inner_cause() -> None:
    ct.start_launch()
    ct.begin("ssh")
    ct.note("plugin_preparation", "failure", "clone_auth_failed")
    assert [_shape(e)[:3] for e in ct.finish(1)] == [
        ("plugin_preparation", "failure", "clone_auth_failed"),
        ("ssh", "failure", "clone_auth_failed"),
        ("launch", "failure", "clone_auth_failed"),
    ]


def test_a_full_recorder_keeps_the_terminal_result_and_launch_summary() -> None:
    ct.start_launch()
    ct.begin("ssh")
    for _ in range(ct.MAX_RECORDS + 5):
        ct.retry("transport_retry")
    ct.fail("transport_failed", outcome="timeout")
    events = ct.finish(1)
    assert len(events) == ct.MAX_RECORDS
    assert [(e["stage"], e["outcome"], e["reason"]) for e in events[-2:]] == [
        ("ssh", "timeout", "transport_failed"), ("launch", "failure", "transport_failed"),
    ]


def test_a_recovered_retry_does_not_become_a_later_failure_cause() -> None:
    ct.start_launch()
    ct.begin("ssh")
    ct.note("codespace_connect", "retry", "transport_retry")
    ct.note("codespace_connect", "success", "none", attempt=2)
    events = ct.finish(1)
    assert [(e["stage"], e["outcome"], e["reason"]) for e in events if e["stage"] != "codespace_connect"] == [
        ("ssh", "failure", "other"), ("launch", "failure", "other"),
    ]


def test_a_recovered_relay_does_not_explain_a_later_transport_failure() -> None:
    ct.start_launch()
    ct.begin("ssh")
    ct.note("credential_relay", "failure", "relay_not_listening")
    ct.note("credential_relay", "success", "none")
    ct.fail_with_cause("transport_failed")
    events = ct.finish(1)
    assert [(e["stage"], e["outcome"], e["reason"]) for e in events if e["stage"] != "credential_relay"] == [
        ("ssh", "failure", "transport_failed"), ("launch", "failure", "transport_failed"),
    ]


def test_a_refused_claim_closes_the_ssh_session_as_that_failure(seams: Any) -> None:
    def refused(ns: Any, **kwargs: Any) -> int:
        ct.note("claim", "failure", "claim_busy")
        return copilot_detach._BUSY_EXIT

    ct.start_launch()
    rc = copilot_detach.cmd_detach(_launch_tests._args(), ssh_session=refused)
    assert rc == copilot_detach._BUSY_EXIT
    shapes = [_shape(e)[:3] for e in ct.finish(rc)]
    assert ("ssh", "failure", "claim_busy") in shapes
    assert ("ssh", "success", "none") not in shapes
    assert shapes[-1] == ("launch", "failure", "claim_busy")


@pytest.mark.parametrize(("stage", "reason"), [
    ("credential_relay", "relay_not_listening"), ("ssh", "transport_failed"),
])
def test_a_session_without_a_result_keeps_its_recorded_cause(
    seams: Any, stage: str, reason: str,
) -> None:
    def unreachable(ns: Any, **kwargs: Any) -> int:
        ct.note(stage, "failure", reason)
        return 1

    ct.start_launch()
    rc = copilot_detach.cmd_detach(_launch_tests._args(), ssh_session=unreachable)
    assert rc == 1
    shapes = [_shape(e)[:3] for e in ct.finish(rc)]
    assert ("ssh", "failure", "connect_failed") not in shapes
    assert shapes[-1] == ("launch", "failure", reason)


def _portable_stage(stage: str) -> list[tuple[Any, Any]]:
    return [(r["outcome"], r["reason"]) for r in ct._records if r["stage"] == stage]


def test_a_portable_launch_whose_session_never_registers_is_observed(flow: Any) -> None:
    flow.registration_missing = True
    assert flow.invoke() == copilot_detach._BUSY_EXIT
    assert _portable_stage("seed_validation") == [("success", "none")]
    assert _portable_stage("bridge_registration") == [("failure", "session_unrepresented")]


def test_a_portable_launch_with_an_oversized_seed_is_observed(
    flow: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def oversized(args: Any) -> str:
        raise ValueError("seed is too long (max 1 characters)")

    monkeypatch.setattr("agent_codespaces.portable_mux_launch.read_seed", oversized)
    assert flow.invoke() == 1
    assert _portable_stage("seed_validation") == [("failure", "seed_too_long")]
    assert ct._last_reason == "seed_too_long"