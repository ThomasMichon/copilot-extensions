"""Passive receipt/head comparison over private current owner files."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import handoff_head_observation as provider
from agent_worktrees import handoff_head_io as io
from agent_worktrees import sessions

TIME = "2026-01-01T00:00:00+00:00"
LATER = "2026-01-01T00:01:00+00:00"
CONSUMED = "2026-01-01T00:02:00+00:00"


def _identity(sid: str) -> dict[str, Any]:
    return {
        "version": 1,
        "project": "test",
        "worktreeId": "wt",
        "creationNonce": "nonce",
        "sessionId": sid,
        "activation": {
            "version": 1,
            "ordinal": 1,
            "startedAt": TIME,
            "startRecordedAt": TIME,
            "sourceIdentity": f"source:{sid}",
        },
    }


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    project, session_root = tmp_path / "project", tmp_path / ".copilot" / "session-state"
    tracking_path = project / "worktrees" / "wt.yaml"
    directory = project / "worktrees" / "wt" / "handoff"
    directory.mkdir(parents=True)
    record: dict[str, Any] = {
        "repo": "test",
        "worktree_id": "wt",
        "creation_nonce": "nonce",
        "branch": "worktree/wt",
        "worktree_path": str(tmp_path / "checkout"),
        "machine": "test",
        "platform": "linux",
        "started_at": TIME,
        "last_resumed_at": TIME,
        "resume_count": 0,
        "title": None,
        "status": "active",
        "completed_at": None,
        "head_session": "new",
        "head_revision": 1,
        "lifecycle_revision": 1,
        "sessions": [
            {
                "session_id": sid,
                "started_at": TIME,
                "activations": [
                    {
                        "ordinal": 1,
                        "started_at": TIME,
                        "start_recorded_at": TIME,
                        "source_identity": f"source:{sid}",
                    }
                ],
            }
            for sid in ("old", "new", "other")
        ],
    }
    tracking_path.write_text(yaml.safe_dump(record), encoding="utf-8")
    for sid in ("old", "new", "other"):
        state = session_root / sid
        state.mkdir(parents=True)
        (state / "events.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: session_root)
    return {
        "project": project,
        "session_root": session_root,
        "record": record,
        "tracking_path": tracking_path,
        "directory": directory,
    }


def _write_record(owner: dict[str, Any]) -> None:
    owner["tracking_path"].write_text(yaml.safe_dump(owner["record"]), encoding="utf-8")


def _receipt(
    owner: dict[str, Any],
    *,
    task: bool = False,
    token: str | None = None,
    consumer: str = "new",
    consumed: bool = True,
    created: str = TIME,
    consumed_at: str = CONSUMED,
) -> tuple[Path, dict[str, Any]]:
    token = token or ("task-one" if task else "handoff-old")
    metadata = {
        "kind": "context-handoff",
        "version": 2,
        "id": "handoff-old",
        "storage": "agent-dispatch" if task else "file",
        "taskId": None,
        "worktree": "wt",
        "sessionId": "old",
        "createdAt": created,
        "authorIdentity": _identity("old"),
    }
    if task:
        raw: dict[str, Any] = {
            "kind": "context-handoff-delivery",
            "version": 1,
            "metadata": metadata,
            "handoffToken": token,
            "predecessorSession": "old",
            "consumerSession": consumer,
            "steps": {"consumeAttempted": True, "taskConsumed": consumed, "promptInjected": False},
            "stepTimes": {"taskConsumed": consumed_at} if consumed else {},
            "consumerIdentity": _identity(consumer) if consumed else None,
            "payload": "PRIVATE PROMPT MUST NOT BE RETURNED",
        }
        name = f"delivery-{token}.json"
    else:
        metadata["id"] = token
        raw = {
            **metadata,
            "consumed": consumed,
            "consumedAt": consumed_at if consumed else None,
            "consumedBySession": consumer if consumed else None,
            "consumerIdentity": _identity(consumer) if consumed else None,
            "promptText": "PRIVATE PROMPT MUST NOT BE RETURNED",
        }
        name = f"{token}.json"
    path = owner["directory"] / name
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, raw


def _observe(owner: dict[str, Any]) -> dict[str, Any]:
    return provider.observe("test", "wt", owner["project"], owner["session_root"])


@pytest.mark.parametrize("task", [False, True])
@pytest.mark.parametrize("head", ["new", "other", None])
def test_independent_consumer_not_head(
    owner: dict[str, Any],
    task: bool,
    head: str | None,
) -> None:
    _receipt(owner, task=task)
    owner["record"]["head_transitions"] = [
        {"revision": 1, "session_id": head, "reason": "explicit", "at": TIME},
    ]
    _write_record(owner)
    result = _observe(owner)
    assert result["status"] == ("match" if head == "new" else "mismatch")
    assert result["expected"]["consumer"] == "new"
    assert result["expected"]["source"] == ("task" if task else "file")
    assert result["durable_head"]["session_id"] == head
    assert "PRIVATE PROMPT" not in json.dumps(result)


def test_picker_fallback_is_not_hidden(owner: dict[str, Any]) -> None:
    _receipt(owner)
    (owner["session_root"] / "new" / "events.jsonl").unlink()
    (owner["session_root"] / "other" / "workspace.yaml").write_text(
        "updated_at: '2026-01-02T00:00:00+00:00'\n",
        encoding="utf-8",
    )
    result = _observe(owner)
    assert result["status"] == "mismatch"
    assert result["reason"] == "picker-target-mismatch"
    assert result["durable_head"]["session_id"] == "new"
    assert result["picker"]["session_id"] == "other"


@pytest.mark.parametrize("task", [False, True])
def test_newer_incomplete_does_not_fall_back(owner: dict[str, Any], task: bool) -> None:
    _receipt(owner)
    _receipt(
        owner,
        task=task,
        token="task-two" if task else "handoff-two",
        consumed=False,
        created=LATER,
    )
    result = _observe(owner)
    assert result["status"] == "unknown"
    assert result["reason"] == "newer-incomplete-delivery"
    assert result["expected"] is None


@pytest.mark.parametrize("field", ["authorIdentity", "consumerIdentity"])
@pytest.mark.parametrize("change", ["legacy", "nonce", "activation", "boolean"])
def test_identity_cannot_be_upgraded_from_current_binding(
    owner: dict[str, Any],
    field: str,
    change: str,
) -> None:
    path, receipt = _receipt(owner)
    if change == "legacy":
        receipt.pop(field)
    elif change == "nonce":
        receipt[field]["creationNonce"] = "previous-incarnation"
    elif change == "boolean":
        receipt[field]["activation"]["ordinal"] = True
    else:
        receipt[field]["activation"]["sourceIdentity"] = "previous:activation"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _observe(owner)
    assert result["status"] == "unknown"
    assert result["expected"] is None


def test_superseded_consumer_activation(owner: dict[str, Any]) -> None:
    _receipt(owner)
    entry = owner["record"]["sessions"][1]
    entry["activations"][0]["ended_at"] = LATER
    entry["activations"].append(
        {
            "ordinal": 2,
            "started_at": LATER,
            "start_recorded_at": LATER,
            "source_identity": "source:new-resume",
        }
    )
    _write_record(owner)
    assert _observe(owner)["reason"] == "consumer-activation-unqualified-or-superseded"


@pytest.mark.parametrize("task", [False, True])
def test_token_and_predecessor_correlation(owner: dict[str, Any], task: bool) -> None:
    _receipt(owner, task=task)
    owner["record"]["handoffs"] = [
        {
            "ordinal": 1,
            "token": "task-one" if task else "handoff-old",
            "predecessor": "other",
            "state": "linked",
            "opened_at": TIME,
        }
    ]
    _write_record(owner)
    assert _observe(owner)["reason"] == "conflicting-predecessor"


def test_conflicting_predecessor_mirror(owner: dict[str, Any]) -> None:
    _receipt(owner)
    request = {
        "kind": "context-handoff-session-request",
        "version": 1,
        "sessionId": "old",
        "worktree": "wt",
        "handoffId": "handoff-old",
        "consumed": True,
        "consumedBySession": "other",
        "authorIdentity": _identity("old"),
        "consumerIdentity": _identity("other"),
    }
    (owner["session_root"] / "old" / "handoff-request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    assert _observe(owner)["reason"] == "conflicting-consumption-records"


def test_tied_consumptions_are_ambiguous(owner: dict[str, Any]) -> None:
    _receipt(owner)
    _receipt(owner, task=True)
    assert _observe(owner)["reason"] == "ambiguous-receipt-order"


def test_most_recent_actual_consume_wins(owner: dict[str, Any]) -> None:
    _receipt(owner, consumer="other", consumed_at=LATER)
    _receipt(owner, task=True)
    assert _observe(owner)["expected"]["consumer"] == "new"


def test_preparation_is_not_consumption(owner: dict[str, Any]) -> None:
    _receipt(owner, task=True, consumed=False)
    result = _observe(owner)
    assert result["status"] == "unknown"
    assert result["reason"] == "no-completed-delivery"


@pytest.mark.parametrize("failure", ["oversized", "invalid", "directory-budget", "symlink"])
def test_bad_candidate_cannot_be_silently_skipped(owner: dict[str, Any], failure: str) -> None:
    _receipt(owner)
    path = owner["directory"] / "delivery-other.json"
    if failure == "oversized":
        path.write_bytes(b" " * (io.FILE_BYTES + 1))
    elif failure == "invalid":
        path.write_bytes(b'{"kind":')
    elif failure == "directory-budget":
        for index in range(io.ENTRY_LIMIT):
            (owner["directory"] / f"irrelevant-{index}").touch()
    else:
        path.symlink_to(owner["tracking_path"])
    result = _observe(owner)
    assert result["status"] == "unavailable"
    assert result["expected"] is None


def test_no_state_creation_or_root_enumeration(
    owner: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = owner["directory"]
    directory.rmdir()
    directory.parent.rmdir()
    scandir = io.os.scandir

    def exact_scandir(path: Path) -> Any:
        assert path == directory
        return scandir(path)

    monkeypatch.setattr(io.os, "scandir", exact_scandir)
    monkeypatch.setattr(Path, "mkdir", lambda *_a, **_kw: pytest.fail("state creation"))
    monkeypatch.setattr(Path, "glob", lambda *_a, **_kw: pytest.fail("global enumeration"))
    result = _observe(owner)
    assert result["status"] == "unknown"
    assert not directory.parent.exists()


@pytest.mark.parametrize("replace", ["record", "receipt"])
def test_changed_during_read_is_not_a_pass(
    owner: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    replace: str,
) -> None:
    path, raw = _receipt(owner)
    original = provider.read_expected

    def changing(*args: Any) -> Any:
        result = original(*args)
        if replace == "record":
            owner["record"]["creation_nonce"] = "replacement"
            _write_record(owner)
        else:
            raw["consumedBySession"] = "other"
            path.write_text(json.dumps(raw), encoding="utf-8")
        return result

    monkeypatch.setattr(provider, "read_expected", changing)
    result = _observe(owner)
    assert result["status"] == "unknown"
    assert result["reason"] == "source-changed"
    assert result["expected"] is None
    assert result["durable_head"]["status"] == "stale"


def test_parser_and_old_listing_are_additive(
    owner: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_worktrees import output, session_tracking_cli, tracking

    parser = argparse.ArgumentParser(allow_abbrev=False)
    provider.add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["handoff-head-observation", "--worktree", "wt", "--json"])
    assert args.func is provider.cmd_observation
    with pytest.raises(SystemExit):
        parser.parse_args(["handoff-head-observation", "--worktree", "wt", "--unknown"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["handoff-head-observation", "--worktree", "wt", "--contract-version", "2"]
        )
    monkeypatch.setattr(provider.cfg, "tracking_dir", lambda: owner["tracking_path"].parent)
    monkeypatch.setattr(tracking, "list_records", lambda _p: [])
    captured: list[dict] = []
    monkeypatch.setattr(output, "_json_output", captured.append)
    monkeypatch.setattr(provider, "observe", lambda *_a: pytest.fail("listing read receipts"))
    assert session_tracking_cli.cmd_list_sessions(argparse.Namespace()) == 0
    assert captured[0]["sessions"] == []
    assert captured[0]["handoff_head_observation"] == provider.declaration()


def test_existing_picker_and_snapshot_picker_agree(owner: dict[str, Any]) -> None:
    data = copy.deepcopy(owner["record"])
    record = provider.tracking.parse_record_data(data, path=owner["tracking_path"])
    assert sessions.resolve_resume_target(record) == provider._picker(
        io.Snapshot(),
        record,
        owner["session_root"],
    )


@pytest.mark.parametrize("task", [False, True])
def test_actual_handoff_producer_and_consume_records(
    owner: dict[str, Any],
    task: bool,
) -> None:
    """Exercise the actual JavaScript writers, including failed head promotion."""
    for sid in ("old", "new"):
        identity = _identity(sid)
        activation = identity["activation"]
        activation["startRecordedAt"] = TIME.removesuffix("+00:00")
        owner["record"]["sessions"][0 if sid == "old" else 1]["activations"][0][
            "start_recorded_at"
        ] = activation["startRecordedAt"]
        binding = {
            "sessionId": sid,
            "worktreeId": "wt",
            "project": "test",
            "creationNonce": "nonce",
            "sessionActivation": activation,
        }
        (owner["session_root"] / sid / "worktree-binding.json").write_text(
            json.dumps(binding),
            encoding="utf-8",
        )
    _write_record(owner)
    core = Path(__file__).resolve().parents[3] / (
        "plugins/context-handoff/extensions/context-handoff/handoff-core.mjs"
    )
    script = """
import { pathToFileURL } from "node:url";
import { join } from "node:path";
const core = await import(pathToFileURL(process.argv[1]));
const stateDir = process.argv[2], task = process.argv[3] === "task";
const cwd = join(stateDir, "wt");
const meta = core.makeHandoffMetadata({
  sid: "old", cwd, storage: task ? "agent-dispatch" : "file",
}, key => key === "worktree-dir" ? cwd : stateDir);
const id = task ? "task-one" : meta.id;
core.writeSessionStateHandoff({
  sid: "old", promptText: "private brief", seed: "seed",
  stored: { id, metadata: meta, storage: meta.storage },
});
const promoteHead = () => { throw new Error("promotion unavailable"); };
let result;
if (task) {
  const payload = core.encodeHandoffPayload("private brief", meta);
  result = core.consumeDispatchHandoffTask(cwd, id, "new", true, {
    readPayload: () => payload, consumeTask: () => payload,
    stateDirResolver: () => stateDir, promoteHead,
  });
} else {
  const path = join(stateDir, "handoff", `${meta.id}.json`);
  core.writeJsonAtomic(path, { ...meta, consumed: false, promptText: "private brief" });
  result = core.consumeFileHandoff(cwd, "new", id, path, { promoteHead });
}
if (!result.ok) throw new Error("private consume failed");
"""
    home = owner["session_root"].parents[1]
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            str(core),
            str(owner["directory"].parent),
            "task" if task else "file",
        ],
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    observation = _observe(owner)
    assert observation["status"] == "match", observation
    assert observation["expected"]["consumer"] == "new"


def test_real_cli_dispatch_is_passive(
    owner: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import __main__ as cli

    _receipt(owner)
    monkeypatch.setattr(cli, "_anchor_for_project", lambda _p: None)
    monkeypatch.setattr(provider.cfg, "project_dir", lambda *_a: owner["project"])
    monkeypatch.setattr(
        cli,
        "_ensure_status_monitor",
        lambda *_a, **_kw: pytest.fail("daemon wake"),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_a, **_kw: pytest.fail("process launch"),
    )
    captured: list[dict] = []
    monkeypatch.setattr(provider.output, "_json_output", captured.append)
    assert (
        cli.main(["--project", "test", "handoff-head-observation", "--worktree", "wt", "--json"])
        == 0
    )
    assert captured[0]["status"] == "match"


@pytest.mark.parametrize("broken", ["session", "head", "activation"])
def test_malformed_current_ledgers_do_not_normalize_into_a_pass(
    owner: dict[str, Any],
    broken: str,
) -> None:
    _receipt(owner)
    if broken == "session":
        owner["record"]["sessions"].append({"started_at": LATER})
    elif broken == "head":
        owner["record"]["head_transitions"] = [{"revision": True, "session_id": "new"}]
    else:
        owner["record"]["sessions"][1]["activations"][0]["ordinal"] = True
    _write_record(owner)
    assert _observe(owner)["status"] in {"unknown", "unavailable"}


@pytest.mark.parametrize("failure", ["interrupted", "elapsed", "total-bytes"])
def test_exhausted_or_interrupted_read_is_unavailable(
    owner: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    _receipt(owner)
    if failure == "interrupted":

        def interrupted(*_args: Any) -> bytes:
            raise InterruptedError("private content must not leak")

        monkeypatch.setattr(io.os, "read", interrupted)
    elif failure == "elapsed":
        monkeypatch.setattr(io, "WORK_SECONDS", 0)
    else:
        monkeypatch.setattr(io, "TOTAL_BYTES", 1)
    result = _observe(owner)
    assert result["status"] == "unavailable"
    assert result["expected"] is None
    assert "private content" not in json.dumps(result)


def test_conversation_append_does_not_change_picker_presence(
    owner: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _receipt(owner)
    original = provider.read_expected

    def appending(*args: Any) -> Any:
        result = original(*args)
        with (owner["session_root"] / "new" / "events.jsonl").open("a") as stream:
            stream.write('{"type":"new-event"}\n')
        return result

    monkeypatch.setattr(provider, "read_expected", appending)
    assert _observe(owner)["status"] == "match"
