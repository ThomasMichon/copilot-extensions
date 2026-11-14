"""Tests for reference-file staging into a detached venue session."""

from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path
from typing import Any, BinaryIO, cast
from types import SimpleNamespace

import pytest
from venue_copilot import refs as venue_refs


def test_prepared_delivery_retains_unknown_after_timeout_even_if_retry_refuses() -> None:
    import json
    import subprocess
    from types import SimpleNamespace
    calls: list[tuple[list[str], str]] = []
    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((argv, kwargs["input"]))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return SimpleNamespace(returncode=69, stdout=json.dumps({
            "owned_delivery_version": 1, "admission_status": "not_admitted",
            "reason": "plugins_not_ready",
        }))
    result = venue_refs.deliver_note(
        "native", "owned", run=run, operation="operation",
        prepared_context={"version": 1, "cwd": "/workspace", "plugins": []},
    )
    assert result.status == "unknown" and len(calls) == 2
    assert result.reason is None
    assert calls[0] == calls[1]
    assert "--expected-session-id" in calls[0][0]


def test_prepared_delivery_requires_structured_admission_or_confirmed_refusal() -> None:
    import json
    from types import SimpleNamespace
    context = {"version": 1, "cwd": "/workspace", "plugins": []}
    refused = venue_refs.deliver_note(
        "native", "owned", operation="operation", prepared_context=context,
        run=lambda *args, **kwargs: SimpleNamespace(returncode=69, stdout=json.dumps({
            "owned_delivery_version": 1, "admission_status": "not_admitted",
        })),
    )
    assert refused.status == "refused" and refused.reason is None
    bare_success = venue_refs.deliver_note(
        "native", "owned", operation="operation", prepared_context=context,
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""),
    )
    assert bare_success.status == "unknown"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("plugins_not_ready", "plugins_not_ready"),
        ("startup_protocol", "startup_protocol"),
        ("unrecognized diagnostic text", None),
        ({"detail": "untrusted diagnostic"}, None),
    ],
)
def test_prepared_delivery_preserves_only_fixed_refusal_reasons(
    reason: str | dict[str, str], expected: str | None,
) -> None:
    import json
    from types import SimpleNamespace

    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=69, stdout=json.dumps({
            "owned_delivery_version": 1, "admission_status": "not_admitted",
            "reason": reason, "error": "raw diagnostic must not propagate",
        }))

    result = venue_refs.deliver_note(
        "native", "owned", run=run, operation="operation",
        prepared_context={"version": 1, "cwd": "/workspace", "plugins": []},
    )
    assert result.status == "refused" and len(calls) == 1
    assert result.reason == expected and result.recovery()["reason"] == expected
    assert "raw diagnostic" not in repr(result.recovery())


def _readiness_refusal(detail: str) -> dict[str, Any]:
    return {
        "owned_delivery_version": 1, "admission_status": "not_admitted",
        "reason": "plugins_not_ready",
        "error": str({"code": "plugins_not_ready", "reason": detail}),
    }


def test_prepared_delivery_reobserves_readiness_while_plugins_are_still_loading() -> None:
    import json

    calls: list[tuple[list[str], str]] = []
    sleeps: list[float] = []
    replies = [
        _readiness_refusal("readiness-timeout"),
        _readiness_refusal("failed:selected-loadout-pending:alpha@market"),
        {"accepted": True, "message_id": 9, "session_id": "native",
         "receipt": {"stage": "admitted", "session_id": "native"}},
    ]

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((argv, kwargs["input"]))
        reply = replies.pop(0)
        return SimpleNamespace(returncode=0 if reply.get("accepted") else 69, stdout=json.dumps(reply))

    result = venue_refs.deliver_note(
        "native", "owned", run=run, operation="operation", sleep=sleeps.append,
        prepared_context={"version": 1, "cwd": "/workspace", "plugins": []},
    )
    assert result.status == "admitted" and result.message_id == 9
    assert sleeps == list(venue_refs.READINESS_RETRY_DELAYS)
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]


def test_prepared_delivery_bounds_loading_retries_and_keeps_final_refusals() -> None:
    import json

    def deliver(detail: str) -> tuple[Any, list[float], int]:
        sleeps: list[float] = []
        calls: list[int] = []

        def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
            calls.append(1)
            return SimpleNamespace(returncode=69, stdout=json.dumps(_readiness_refusal(detail)))

        result = venue_refs.deliver_note(
            "native", "owned", run=run, operation="operation", sleep=sleeps.append,
            prepared_context={"version": 1, "cwd": "/workspace", "plugins": []},
        )
        return result, sleeps, len(calls)

    result, sleeps, count = deliver("readiness-timeout")
    assert result.status == "refused" and result.reason == "plugins_not_ready"
    assert sleeps == list(venue_refs.READINESS_RETRY_DELAYS)
    assert count == len(venue_refs.READINESS_RETRY_DELAYS) + 1
    for final in ("failed:selected-plugin-disabled:alpha@market", "native-session-changed",
                  "startup-operation-input-changed"):
        result, sleeps, count = deliver(final)
        assert result.status == "refused" and sleeps == [] and count == 1


def test_confirmed_admission_recovery_never_resends_after_native_session_exits() -> None:
    context = {"version": 1, "cwd": "/workspace", "plugins": []}
    pending = {
        **venue_refs.owned_result(
            False, session_id="native", operation="operation", note="owned", context=context,
        ).recovery(),
        "status": "admitted", "message_id": 17,
        "envelope": {"prompt": "owned", "plugin_context": context},
    }
    result = venue_refs.reconcile_owned_delivery(
        pending, run=lambda *a, **kw: pytest.fail("confirmed admission cannot be resent"),
    )
    assert result.status == "admitted" and result.message_id == 17
    with pytest.raises(ValueError, match="inputs changed"):
        venue_refs.reconcile_owned_delivery({
            **pending, "envelope": {"prompt": "different", "plugin_context": context},
        })
    with pytest.raises(ValueError, match="valid message ID"):
        venue_refs.reconcile_owned_delivery({**pending, "message_id": None})


def _members(payload: bytes) -> dict[str, bytes]:
    tf = tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload)), mode="r:gz")
    return {m.name: cast(BinaryIO, tf.extractfile(m)).read() for m in tf.getmembers() if m.isfile()}


def test_files_travel_over_stdin_into_a_batch_dir_outside_the_repo(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    har = tmp_path / "a" / "trace.har"
    har.write_bytes(b"HAR")
    command, payload, files = venue_refs.build_refs_upload([str(har)], "scope-1")
    assert command.startswith('mkdir -p "$HOME/.agent-bridge/refs/scope-1"')
    assert "base64 -d | tar -xzf -" in command and command.endswith("pwd")
    assert len(command) < 200  # the payload is never on the command line
    assert _members(payload) == {"trace.har": b"HAR"}
    assert files == [("trace.har", 3)]


def test_same_named_files_do_not_collide(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (tmp_path / "x" / "notes.md").write_text("one")
    (tmp_path / "y" / "notes.md").write_text("two")
    _, payload, files = venue_refs.build_refs_upload(
        [str(tmp_path / "x" / "notes.md"), str(tmp_path / "y" / "notes.md")], "b",
    )
    assert [n for n, _ in files] == ["notes.md", "notes-2.md"]
    assert _members(payload) == {"notes.md": b"one", "notes-2.md": b"two"}


def test_a_folder_is_sent_whole(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "one.log").write_text("1")
    (logs / "two.log").write_text("22")
    _, payload, files = venue_refs.build_refs_upload([str(logs)], "b")
    assert files == [("logs", 3)]
    assert set(_members(payload)) == {"logs/one.log", "logs/two.log"}


def test_missing_and_oversized_inputs_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(venue_refs.RefFileError, match="not found"):
        venue_refs.build_refs_upload([str(tmp_path / "nope.har")], "b")
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 2048)
    monkeypatch.setattr(venue_refs, "MAX_REF_BYTES", 1024)
    with pytest.raises(venue_refs.RefFileError, match="limit"):
        venue_refs.build_refs_upload([str(big)], "b")


def test_note_names_exact_venue_paths_and_asks_not_to_commit() -> None:
    note = venue_refs.refs_note(
        "/home/codespace/.agent-bridge/refs/b", [("trace.har", 3 * 1024 * 1024)],
    )
    assert "- /home/codespace/.agent-bridge/refs/b/trace.har (3.0 MB)" in note
    assert "never" in note and "commit" in note


def test_deliver_note_sends_over_stdin() -> None:
    calls: list[tuple[list[str], Any]] = []

    def run(argv: list[str], **kw: Any) -> object:
        calls.append((argv, kw.get("input")))
        return type("R", (), {"returncode": 0})()

    assert venue_refs.deliver_note("sid-1", "see /x/y.har", run=run, operation="launch-1")
    argv, stdin = calls[0]
    assert argv[1:7] == ["send", "sid-1", "--prompt-file", "-", "--no-wait", "--steer"]
    assert stdin == "see /x/y.har"

    def key(i: int) -> str:
        return calls[i][0][calls[i][0].index("--idempotency-key") + 1]

    # A retry of the same delivery reuses its key: the bridge answers an
    # ambiguous earlier attempt instead of enqueueing the note twice.
    assert venue_refs.deliver_note("sid-1", "see /x/y.har", run=run, operation="launch-1")
    assert key(1) == key(0)
    # A later, separate delivery of the same text is really sent: a new key.
    assert venue_refs.deliver_note("sid-1", "see /x/y.har", run=run, operation="launch-2")
    assert key(2) != key(0)
    assert venue_refs.deliver_note("sid-1", "see /x/y.har", run=run)  # no operation: its own
    assert venue_refs.deliver_note("sid-1", "see /x/y.har", run=run)
    assert len({key(0), key(2), key(3), key(4)}) == 4
    assert venue_refs.deliver_note("sid-1", "another note", run=run, operation="launch-1")
    assert key(5) != key(0)
    # A retry naming the id the placeholder was renamed to reaches the same
    # session: the same key, so an accepted-but-unanswered first send isn't run twice.
    assert venue_refs.deliver_note("resumed-sid", "see /x/y.har", run=run, operation="launch-1")
    assert key(6) == key(0)


def test_deliver_note_is_steered_and_bounded() -> None:
    seen: dict[str, Any] = {}

    def run(argv: list[str], **kw: Any) -> object:
        seen.update(kw)
        return type("R", (), {"returncode": 0})()

    assert venue_refs.deliver_note("sid-1", "note", run=run)
    assert seen.get("timeout") == 60


def test_deliver_note_can_require_a_daemon_protocol() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kw: Any) -> object:
        calls.append(argv)
        return type("R", (), {"returncode": 3})()  # the daemon is too old

    assert venue_refs.deliver_note("sid-1", "note", run=run, min_daemon_protocol=20) is False
    assert calls[0][-2:] == ["--min-daemon-protocol", "20"]


def test_a_wedged_bridge_reports_failed_delivery() -> None:
    import subprocess

    def run(argv: list[str], **kw: Any) -> object:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=60)

    assert venue_refs.deliver_note("sid-1", "note", run=run) is False
