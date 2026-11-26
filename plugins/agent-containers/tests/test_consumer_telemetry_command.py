"""Optional `containers.lifecycle.v1` records for finite CLI runs."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from process_telemetry.finite_owner import capability
from process_telemetry.stage_projection import for_schema
from ssh_manager import TargetBusyError

from agent_containers import config, consumer_telemetry as ct, copilot_detach


def _load_sibling(module: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(module, Path(__file__).with_name(filename))
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


# The detached and attached launch seams are shared with their own tests
# rather than copied.
_detach_tests: Any = _load_sibling("_containers_detach_tests", "test_copilot_detach.py")
_venue_tests: Any = _load_sibling("_containers_venue_tests", "test_copilot_venue.py")
seams = _detach_tests.seams
_stub_plugin_preparation = _venue_tests._stub_plugin_preparation

_RECEIVER = """\
import json, sys
from pathlib import Path
with Path("readback.jsonl").open("a", encoding="utf-8") as output:
    for line in sys.stdin:
        output.write(json.dumps(json.loads(line)) + "\\n")
"""

_EMBODIED = json.dumps({
    "ok": True, "created": True, "resumed": False, "seed_submitted": False,
    "bridge_seed_deferred": True, "bridge_seed_operation": "r1", "session": "wt-anchor-repo",
})


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
            "agent-containers", "containers.lifecycle.v1",
        )
    return [record["event"] for record in records]


def _shape(event: dict[str, Any]) -> tuple[Any, ...]:
    return (event["stage"], event["outcome"], event["reason"], event["attempt"])


def _shapes(code: int) -> list[tuple[Any, ...]]:
    return [_shape(e)[:3] for e in ct.finish(code)]


def _embody_run(seams: Any, monkeypatch: pytest.MonkeyPatch, *, code: int, stdout: str,
                stderr: str = "") -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        command = argv[-1]
        seams.run.append(command)
        if "agent-worktrees embody" in command:
            return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def _detach(**kwargs: Any) -> int:
    return int(copilot_detach.cmd_detach(
        _detach_tests._args(**kwargs),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: port == 61234,
    ))


def test_a_successful_detached_launch_reaches_a_real_receiver_without_identity(
    seams: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _admit(tmp_path, monkeypatch)

    assert ct.instrument(_detach)() == 0
    events = _events(root)
    assert [_shape(e)[:3] for e in events] == [
        ("container_lookup", "success", "none"),
        ("seed_validation", "success", "none"),
        ("claim", "success", "none"),
        ("bridge_payload", "success", "none"),
        ("credential_relay", "success", "none"),
        ("plugin_preparation", "success", "none"),
        ("exec", "success", "none"),
        ("remote_embody", "success", "none"),
        ("bridge_registration", "success", "none"),
        ("launch", "success", "none"),
    ]
    assert all(type(e["duration_ms"]) is int for e in events)
    text = json.dumps(events)
    for secret in ("repo-1", "anchor-repo", "sid-42", "/workspaces", "vscode", str(tmp_path)):
        assert secret not in text


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

    def broken_offer(events: Any) -> None:
        raise RuntimeError("receiver exploded")

    monkeypatch.setattr(ct, "_offer", broken_offer)

    def fails() -> int:
        ct.note("exec", "failure", "transport_failed")
        return 7

    assert ct.instrument(fails)() == 7


def test_console_entry_is_instrumented(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_containers import __main__ as cli

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


def test_packaged_descriptor_names_only_the_containers_schema() -> None:
    path = Path(ct.__file__).parent / "metadata" / "consumer-telemetry-contract.json"
    assert json.loads(path.read_text(encoding="utf-8")) == capability([ct.SOURCE_SCHEMA])


@pytest.mark.parametrize("message, expected", [
    ("Container lookup failed: Docker CLI not found", "engine_unavailable"),
    ("Container lookup failed: daemon not reachable", "engine_unavailable"),
    ("Container lookup failed: unexpected listing", "listing_failed"),
    ("'secret-box' is not a discovered fleet member", "not_found"),
    ("'secret-box' is not ready (state=exited)", "not_running"),
    ("something unexpected", "other"),
])
def test_lookup_reasons_are_closed(message: str, expected: str) -> None:
    assert ct.lookup_reason(RuntimeError(message)) == expected


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


def test_remaining_classifiers_cover_interrupts_and_unknowns() -> None:
    assert ct.lookup_reason(KeyboardInterrupt()) == "interrupted"
    assert ct.preparation_reason(KeyboardInterrupt()) == "interrupted"
    assert ct.preparation_reason(ValueError("x")) == "other"
    assert ct.seed_reason(ValueError("seed is 9 characters (max 1)")) == "seed_too_long"
    assert ct.seed_reason(ValueError("--seed and --seed-file are mutually exclusive")) == (
        "seed_invalid"
    )
    assert ct.embody_reason("", "bash: agent-worktrees: command not found") == (
        "installer_unavailable"
    )
    assert ct.embody_reason("", "bash: python3: command not found") == (
        "bootstrap_python_unavailable"
    )
    assert ct.embody_reason("boom", "") == "other"
    assert ct.registration_reason(
        "the session is running but never registered with the host bridge",
    ) == "session_unrepresented"
    assert ct.registration_reason("the resumed native session never registered") == (
        "session_unregistered"
    )
    assert ct.registration_reason(None) == "other"


def test_note_bounds_attempt_and_limit_and_drops_unknown_stages() -> None:
    ct.note("exec", "timeout", "transport_failed",
            started=ct.clock(), attempt=10**9, limit_ms=30000)
    ct.note("container_start", "failure", "start_failed")
    ct.note("not_a_stage", "failure", "other")
    (event,) = ct.finish(1)
    assert event["attempt"] == 2**16 and event["limit_ms"] == 30000
    assert "duration_ms" in event


def test_every_record_passes_the_shared_projection(
    seams: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, project = for_schema(ct.SOURCE_SCHEMA)
    _embody_run(seams, monkeypatch, code=127, stdout="",
                stderr="bash: agent-worktrees: command not found in secret-box")
    rc = _detach()
    assert rc == 1
    events = ct.finish(rc)
    assert events
    for event in events:
        assert project(event)["stage"] == event["stage"]


def test_a_lookup_failure_keeps_its_cause_without_masking_the_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import agent_containers.resolver as resolver_mod

    def refused(name: str, config: Any = None) -> Any:
        raise RuntimeError("Container lookup failed: Docker CLI not found on secret-host")

    monkeypatch.setattr(resolver_mod, "resolve_live_exec_target", refused)
    assert _detach() == 1
    assert "Docker CLI not found" in capsys.readouterr().err
    assert _shapes(1) == [
        ("container_lookup", "failure", "engine_unavailable"),
        ("launch", "failure", "engine_unavailable"),
    ]


def test_a_too_long_seed_is_noted(seams: Any) -> None:
    assert _detach(seed="x" * 1_000_000) == 1
    assert _shapes(1) == [
        ("container_lookup", "success", "none"),
        ("seed_validation", "failure", "seed_too_long"),
        ("launch", "failure", "seed_too_long"),
    ]


class _Busy(TargetBusyError):
    def __init__(self) -> None:
        RuntimeError.__init__(self, "busy")

    def user_message(self) -> str:
        return "[BUSY] synthetic"


def test_claim_outcomes_are_noted(seams: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(self: Any, *, force: bool = False) -> None:
        raise _Busy()

    with monkeypatch.context() as patch:
        patch.setattr(_detach_tests._FakeLock, "acquire", busy)
        assert _detach() == 75
    assert ("claim", "failure", "claim_busy") in _shapes(75)
    ct.reset()
    assert _detach(force=True) == 0
    assert ("claim", "success", "force_claimed") in _shapes(0)


def test_a_transport_exit_is_an_exec_failure_not_an_embody_result(
    seams: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _embody_run(seams, monkeypatch, code=255, stdout="",
                stderr="ssh: connect to host secret-host port 22: Connection refused")
    rc = _detach()
    assert rc == 1
    shapes = _shapes(rc)
    assert ("exec", "failure", "transport_failed") in shapes
    assert not any(stage == "remote_embody" for stage, _, _ in shapes)
    assert shapes[-1] == ("launch", "failure", "transport_failed")


def test_an_exec_timeout_is_noted_with_its_limit(
    seams: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(argv: list[str], **kwargs: Any) -> Any:
        if "agent-worktrees embody" in argv[-1]:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 0)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", slow)
    rc = _detach()
    assert rc != 0
    events = [e for e in ct.finish(rc) if e["stage"] == "exec"]
    assert [(e["outcome"], e["reason"]) for e in events] == [("timeout", "transport_failed")]
    assert type(events[0]["limit_ms"]) is int


def test_an_embody_failure_names_the_missing_installer(
    seams: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _embody_run(seams, monkeypatch, code=127, stdout="",
                stderr="bash: agent-worktrees: command not found")
    rc = _detach()
    assert rc == 1
    shapes = _shapes(rc)
    assert ("exec", "success", "none") in shapes
    assert ("remote_embody", "failure", "installer_unavailable") in shapes
    assert shapes[-1] == ("launch", "failure", "installer_unavailable")


def test_registration_cleanup_never_closes_as_success(
    seams: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from venue_copilot import detached as venue_detached

    _embody_run(seams, monkeypatch, code=0, stdout=_EMBODIED)
    monkeypatch.setattr(venue_detached, "await_claim", lambda scope, rid, timeout: None)
    rc = _detach()
    assert rc == 1
    shapes = _shapes(rc)
    assert [s for s in shapes if s[0] == "bridge_registration"] == [
        ("bridge_registration", "failure", "session_unrepresented"),
    ]
    assert shapes[-1] == ("launch", "failure", "session_unrepresented")


def test_a_relay_outage_is_noted(seams: Any) -> None:
    rc = int(copilot_detach.cmd_detach(
        _detach_tests._args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: False,
    ))
    assert rc == 1
    assert ("credential_relay", "failure", "relay_not_listening") in _shapes(rc)


def test_an_attached_transport_exit_is_a_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rc, _ = _venue_tests._call(
        _venue_tests._args(no_relay=True), target=_venue_tests._target(),
        monkeypatch=monkeypatch,
        subprocess_run=lambda *a, **k: types.SimpleNamespace(returncode=255),
    )
    assert rc == 255
    shapes = _shapes(rc)
    assert shapes[0] == ("container_lookup", "success", "none")
    assert ("claim", "success", "none") in shapes
    assert ("container_connect", "failure", "connect_failed") in shapes


def test_an_attached_session_exit_is_not_a_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, _ = _venue_tests._call(
        _venue_tests._args(no_relay=True), target=_venue_tests._target(),
        monkeypatch=monkeypatch,
        subprocess_run=lambda *a, **k: types.SimpleNamespace(returncode=3),
    )
    assert ("container_connect", "success", "none") in _shapes(rc)
