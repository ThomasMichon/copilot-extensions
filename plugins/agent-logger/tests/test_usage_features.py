"""Synthetic transcript counts and explicit root-call attribution."""

from __future__ import annotations

import copy
import json
import sqlite3
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from agent_logger.usage_feature_evidence import pair_measured_features
from agent_logger.__main__ import main
from agent_logger.sessions import SessionRef
from agent_logger.usage_features import extract_features, features_from_events
from agent_logger.usage_ledger import UsageEvidenceError


def event(kind: str, **data: object) -> dict:
    return {"type": kind, "data": data}


def fixture() -> list[dict]:
    return [
        event("session.start", sessionId="ORIGINAL"),
        event("user.message", content="hello"),
        event("assistant.turn_start", turnId="same"),
        event("session.usage_record", usage={
            "apiCallId": "c1", "model": "model-a", "inputTokens": 30,
            "outputTokens": 12, "cacheReadTokens": 20, "reasoningTokens": 9,
        }),
        event("assistant.message", apiCallId="c1", content="ab", reasoningText="xyz",
              reasoningBlocks={"blocks": [{"thinking": "alternate must not count"}]},
              reasoningOpaque="not observable", encryptedContent="also excluded",
              toolRequests=[{"toolCallId": "t1", "arguments": {"x": 1}}]),
        event("assistant.message", parentToolCallId="t1", apiCallId="child", content="child not root"),
        event("session.usage_record", usage={"apiCallId": "child", "parentToolCallId": "t1", "inputTokens": 999}),
        event("tool.execution_complete", toolCallId="t1", result={"content": "tool"}),
        event("tool.execution_complete", toolCallId="other", result={"content": "unlinked"}),
        event("assistant.turn_start", turnId="same"),
        event("assistant.message", apiCallId="c2", model="model-a", content="next"),
    ]


@pytest.mark.guard
def test_usage_features_complete_observable_output_and_replayed_input() -> None:
    result = features_from_events(fixture(), "copy")
    first, second = result["calls"]
    assert first["output_chars"] == 2 + 3 + len('{"x":1}')
    assert first["input_chars"] == 5
    assert first["input_tokens"] == 30  # cache reads are not additive
    assert first["output_tokens"] == 12  # reasoning is not a second meter
    assert second["input_chars"] == 5 + first["output_chars"] + 4
    assert result["coverage"]["unlinked_tool_results"] == 1
    assert result["session_id"] == "original"
    assert "child not root" not in json.dumps(result)
    assert "alternate must not count" not in json.dumps(result)


@pytest.mark.guard
def test_usage_features_replay_identity_and_before_emission_pairing() -> None:
    rows = fixture()
    rows[4]["id"] = "emission"
    result = features_from_events(rows + [copy.deepcopy(rows[4])], "original")
    assert result["calls"][0]["emissions"] == 1
    conflict = copy.deepcopy(rows[4])
    conflict["data"]["content"] = "conflict"
    with pytest.raises(UsageEvidenceError, match="conflicting replayed"):
        features_from_events(rows + [conflict], "original")


@pytest.mark.guard
def test_usage_features_segments_resets_models_and_unknowns() -> None:
    rows = fixture() + [
        event("session.compaction_complete", success=True, summaryContent="summary"),
        event("session.model_change", newModel="model-b"),
        event("assistant.turn_start", turnId="same"),
        event("assistant.message", apiCallId="c1", content="third"),
        event("session.context_cleared", initialMessage="reset"),
        event("assistant.turn_start"),
        event("assistant.message", content="last"),
    ]
    result = features_from_events(rows, "original")
    assert result["calls"][2]["input_chars"] == 7
    assert result["calls"][2]["model"] == "model-b"
    assert result["calls"][3]["input_chars"] == 5
    assert result["calls"][3]["api_call_id"] is None
    assert result["coverage"]["compactions"] == result["coverage"]["context_resets"] == 1
    unknown = features_from_events([event("assistant.turn_start"), event("assistant.message", content="x")], "unknown")
    assert unknown["calls"][0]["model"] is None
    assert "unknown-model" in unknown["warnings"]


@pytest.mark.guard
def test_usage_features_reasoning_alternates_and_emitted_arguments_once() -> None:
    rows = [
        event("assistant.turn_start"),
        event("assistant.message", reasoningBlocks={"blocks": [
            {"thinking": "abc"}, {"summary": [{"text": "de"}]}, {"encrypted_content": "excluded"},
        ]}, toolRequests=[{"arguments": "raw"}, {"arguments": {"a": "b"}}]),
    ]
    result = features_from_events(rows, "s")
    assert result["calls"][0]["output_chars"] == 3 + 2 + 3 + len('{"a":"b"}')


@pytest.mark.guard
def test_usage_features_external_evidence_unique_pair_and_conflicts() -> None:
    result = features_from_events(fixture(), "original")
    record = {"source": "session-store", "values": {
        "api_call_id": "c2", "model": "model-a", "input_tokens": 40, "output_tokens": 3,
    }}
    pair_measured_features(result, [record, record])
    assert result["calls"][1]["input_tokens"] == 40
    assert result["evidence_join"]["paired_calls_by_source"] == {"session-store": 1}
    mismatch = copy.deepcopy(record)
    mismatch["values"]["input_tokens"] = 41
    with pytest.raises(UsageEvidenceError, match="conflicting measured"):
        pair_measured_features(result, [mismatch])
    repeated = features_from_events(fixture() + fixture()[2:5], "original")
    pair_measured_features(repeated, [{"source": "session-store", "values": {"api_call_id": "c1", "input_tokens": 30}}])
    assert repeated["evidence_join"]["ambiguous_records"] == 1


@pytest.mark.guard
def test_usage_features_unlinked_emission_cannot_choose_between_two_calls() -> None:
    result = features_from_events([
        event("assistant.turn_start"),
        event("session.usage_record", usage={"apiCallId": "a", "inputTokens": 2, "model": "m"}),
        event("session.usage_record", usage={"apiCallId": "b", "inputTokens": 3, "model": "m"}),
        event("assistant.message", content="ambiguous"),
    ], "s")
    assert len(result["calls"]) == 3
    assert result["calls"][-1]["api_call_id"] is None
    assert result["calls"][-1]["input_tokens"] is None


@pytest.mark.guard
@pytest.mark.parametrize("value", [-1, True, 1.2, 10**16])
def test_usage_features_rejects_invalid_measured_tokens(value: object) -> None:
    with pytest.raises(UsageEvidenceError, match="token"):
        features_from_events([
            event("assistant.turn_start"),
            event("session.usage_record", usage={"apiCallId": "a", "inputTokens": value}),
        ], "s")


@pytest.mark.guard
@pytest.mark.parametrize("kind", ["live", "tar", "zip"])
def test_usage_features_live_archive_preserved_measurements_and_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture, kind: str,
) -> None:
    session = tmp_path / "original"
    session.mkdir()
    rows = fixture()
    for row in rows:
        if row["type"] == "session.usage_record":
            row["data"]["usage"].pop("inputTokens", None)
            row["data"]["usage"].pop("outputTokens", None)
    (session / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    artifact = {
        "schema": "agent-logger.usage-evidence", "version": 1, "session_id": "original",
        "records": [{"source": "session-store", "values": {
            "created_at": "2026-01-01T00:00:00Z", "api_call_id": "c1",
            "model": "model-a", "input_tokens": 30, "output_tokens": 12,
        }}],
    }
    (session / "usage-evidence.json").write_text(json.dumps(artifact))
    expected = extract_features(SessionRef("original", "live", session))
    target = session
    if kind == "tar":
        target = tmp_path / "original.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            for path in session.iterdir():
                archive.add(path, arcname=path.name)
    elif kind == "zip":
        target = tmp_path / "original.zip"
        with zipfile.ZipFile(target, "w") as archive:
            for path in session.iterdir():
                archive.write(path, path.name)
    assert main(["usage", "features", "--session", str(target)]) == 0
    actual = json.loads(capsys.readouterr().out)["sessions"][0]
    assert actual == expected
    assert actual["calls"][0]["input_tokens"] == 30
    assert actual["calls"][1]["input_tokens"] is None


@pytest.mark.guard
def test_usage_features_cli_store_is_one_scoped_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger import usage_cli

    database = tmp_path / "session-store.db"
    paths = []
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE assistant_usage_events "
            "(session_id TEXT, created_at TEXT, model TEXT, api_call_id TEXT, input_tokens INTEGER)"
        )
        for sid in ("s1", "s2", "outside"):
            connection.execute(
                "INSERT INTO assistant_usage_events VALUES(?,?,?,?,?)",
                (sid, "2026-01-01T00:00:00Z", "model-a", "a", 30),
            )
            if sid == "outside":
                continue
            path = tmp_path / sid
            path.mkdir()
            rows = [
                event("session.start", sessionId=sid), event("user.message", content="input"),
                event("assistant.turn_start"), event("assistant.message", apiCallId="a", content="output"),
            ]
            (path / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            paths.append(path)
    original = usage_cli.read_sqlite_usage
    requests = []

    def read(path: Path, ids: set[str]) -> dict:
        requests.append(ids)
        return original(path, ids)

    monkeypatch.setattr(usage_cli, "read_sqlite_usage", read)
    args = ["usage", "features", "--usage-store", str(database)]
    for path in paths:
        args.extend(("--session", str(path)))
    before = database.read_bytes()
    assert main(args) == 0
    actual = json.loads(capsys.readouterr().out)
    assert requests == [{"s1", "s2"}]
    assert len(actual["sessions"]) == 2
    assert all(item["calls"][0]["input_tokens"] == 30 for item in actual["sessions"])
    assert database.read_bytes() == before


@pytest.mark.guard
@pytest.mark.parametrize("placement", ["envelope", "legacy"])
def test_usage_features_review_regression_child_ownership_is_central(placement: str) -> None:
    children = [
        event("session.start", sessionId="child"),
        event("session.model_change", newModel="child-model"),
        event("user.message", content="child prompt must not enter root input"),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="child", content="child output"),
        event("session.usage_record", usage={
            "apiCallId": "child", "model": "child-model", "inputTokens": 999,
        }),
        event("session.compaction_complete", success=True, summaryContent="child summary"),
        event("session.context_cleared", initialMessage="child reset"),
    ]
    for child in children:
        target = child if placement == "envelope" else child["data"]
        target["agentId"] = "child-agent"
    rows = fixture()
    expected = features_from_events(rows, "original")
    actual = features_from_events(rows[:2] + children + rows[2:], "original")
    assert actual["calls"] == expected["calls"]
    assert actual["coverage"]["root_turns"] == expected["coverage"]["root_turns"]
    assert actual["coverage"]["compactions"] == actual["coverage"]["context_resets"] == 0


@pytest.mark.guard
@pytest.mark.parametrize("summary", [None, {"not": "text"}])
def test_usage_features_review_regression_summaryless_compaction_discards_context(
    summary: object,
) -> None:
    result = features_from_events([
        event("session.start", model="model-a"),
        event("user.message", content="discarded history"),
        event("session.compaction_complete", success=True, summaryContent=summary),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="a", content="output"),
    ], "s")
    assert result["calls"][0]["input_chars"] == 0
    assert result["coverage"]["compactions"] == 1
    assert "unobserved-compaction-context" in result["warnings"]


@pytest.mark.guard
@pytest.mark.parametrize("overlap", [False, True])
def test_usage_features_review_regression_preserved_diagnostics_plus_store(
    tmp_path: Path, capsys: pytest.CaptureFixture, overlap: bool,
) -> None:
    session = tmp_path / "s"
    session.mkdir()
    rows = [
        event("session.start", sessionId="s"), event("user.message", content="input"),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="a", model="model-a", content="output"),
    ]
    (session / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    records = [
        {"source": "session-store", "values": {
            "created_at": "2026-01-01T00:00:00Z", "api_call_id": api,
            "model": "model-a", "input_tokens": 30,
        }} for api in ("a", "unmatched")
    ]
    (session / "usage-evidence.json").write_text(json.dumps({
        "schema": "agent-logger.usage-evidence", "version": 1, "session_id": "s", "records": records,
    }))
    database = tmp_path / "store.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE assistant_usage_events "
            "(session_id TEXT, created_at TEXT, model TEXT, api_call_id TEXT, input_tokens INTEGER)"
        )
        if overlap:
            connection.execute(
                "INSERT INTO assistant_usage_events VALUES(?,?,?,?,?)",
                ("s", "2026-01-01T00:00:00Z", "model-a", "a", 30),
            )
    assert main(["usage", "features", "--session", str(session), "--usage-store", str(database)]) == 0
    result = json.loads(capsys.readouterr().out)["sessions"][0]
    assert result["calls"][0]["input_tokens"] == 30
    assert result["evidence_join"]["paired_calls_by_source"] == {"session-store": 1}
    assert result["evidence_join"]["unmatched_records"] == 1


@pytest.mark.guard
def test_usage_features_review_regression_unlinked_key_cannot_collide_with_real_id() -> None:
    """A real `apiCallId` shaped like the old synthetic key must survive intact."""
    result = features_from_events([
        event("assistant.turn_start"),
        event("session.usage_record", usage={"apiCallId": "a", "inputTokens": 2, "model": "m"}),
        # The old synthetic key was f"unlinked:{len(self.calls)}"; with exactly two
        # named calls present, that string is literally "unlinked:2".
        event("session.usage_record", usage={
            "apiCallId": "unlinked:2", "inputTokens": 7, "model": "m",
        }),
        event("assistant.message", apiCallId="a", content="a-out"),
        event("assistant.message", apiCallId="unlinked:2", content="real-id-out"),
        # One ambiguous (no apiCallId) emission triggers the old key-collision code path.
        event("assistant.message", content="ambiguous"),
    ], "s")
    by_id = {call["api_call_id"]: call for call in result["calls"] if call["api_call_id"]}
    assert by_id["unlinked:2"]["input_tokens"] == 7
    assert by_id["unlinked:2"]["output_chars"] == len("real-id-out")
    assert by_id["unlinked:2"]["emissions"] == 1
    unlinked_entries = [call for call in result["calls"] if call["api_call_id"] is None]
    assert len(unlinked_entries) == 1
    assert unlinked_entries[0]["output_chars"] == len("ambiguous")


@pytest.mark.guard
def test_usage_features_review_regression_nested_usage_agent_id_excluded() -> None:
    """``data.usage.agentId`` (not just event/data-level ``agentId``) marks child ownership."""
    rows = fixture()
    expected = features_from_events(rows, "original")
    child_usage_record = event("session.usage_record", usage={
        "apiCallId": "child-only-marker", "model": "child-model", "inputTokens": 999,
        "agentId": "child-agent",
    })
    actual = features_from_events([*rows[:2], child_usage_record, *rows[2:]], "original")
    assert actual["calls"] == expected["calls"]
    excluded = actual["coverage"]["child_events_excluded"]
    baseline = expected["coverage"]["child_events_excluded"]
    assert excluded == baseline + 1
    assert "child-only-marker" not in json.dumps(actual)


@pytest.mark.guard
def test_usage_features_review_regression_agent_owned_evidence_is_skipped_before_pairing() -> None:
    """Child-owned (``agent_id``) preserved/SQLite records must not pair into root accounting."""
    result = features_from_events(fixture(), "original")
    child_record = {"source": "session-store", "values": {
        "api_call_id": "c2", "model": "model-a", "input_tokens": 999, "agent_id": "child-agent",
    }}
    pair_measured_features(result, [child_record])
    assert result["calls"][1]["input_tokens"] is None
    assert result["evidence_join"]["paired_calls_by_source"] == {}
    assert result["evidence_join"]["unmatched_records"] == 0
    assert result["evidence_join"]["ambiguous_records"] == 0


@pytest.mark.guard
def test_usage_features_review_regression_child_owned_record_skips_root_identity_check() -> None:
    """A child-owned record with a distinct source_session_id must be excluded
    before the root-session-identity comparison, not fail it."""
    result = features_from_events(fixture(), "original")
    child_record = {"source": "session-store", "values": {
        "api_call_id": "c2", "model": "model-a", "input_tokens": 999,
        "agent_id": "child-agent", "source_session_id": "distinct-child-session",
    }}
    # Must not raise "measured feature session identity mismatch".
    pair_measured_features(result, [child_record])
    assert result["calls"][1]["input_tokens"] is None


@pytest.mark.guard
def test_usage_features_review_regression_text_and_content_block_shapes_counted() -> None:
    """``data.text`` and a text-block ``content`` array are both counted,
    matching the shapes agent-bridge's peek_snapshot already supports."""
    via_text = features_from_events([
        event("user.message", text="hello-text"),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="a", text="world-text"),
    ], "s")
    assert via_text["calls"][0]["input_chars"] == len("hello-text")
    assert via_text["calls"][0]["output_chars"] == len("world-text")
    via_blocks = features_from_events([
        event("user.message", content=[{"text": "he"}, {"text": "llo"}, {"not_text": "x"}]),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="a", content=[{"text": "wo"}, {"text": "rld"}]),
    ], "s")
    assert via_blocks["calls"][0]["input_chars"] == len("hello")
    assert via_blocks["calls"][0]["output_chars"] == len("world")


@pytest.mark.guard
def test_usage_features_review_regression_linked_tool_result_text_block_counted() -> None:
    """A linked tool result whose ``content`` is a text-block array contributes
    its visible text to the next turn's input count, not zero."""
    result = features_from_events([
        event("assistant.turn_start"),
        event(
            "assistant.message", apiCallId="a", content="out",
            toolRequests=[{"toolCallId": "t1"}],
        ),
        event("tool.execution_complete", toolCallId="t1", result={
            "content": [{"text": "to"}, {"text": "ol-out"}],
        }),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="b", content="next"),
    ], "s")
    assert result["calls"][1]["input_chars"] == len("out") + len("tool-out")
    assert result["calls"][1]["output_chars"] == len("next")


@pytest.mark.guard
def test_usage_features_review_regression_model_change_accepts_model_id() -> None:
    """``session.model_change`` using ``data.modelId`` (not just ``newModel``/``model``)
    must still attribute the model to subsequent calls."""
    result = features_from_events([
        event("session.model_change", modelId="model-from-id"),
        event("assistant.turn_start"),
        event("assistant.message", apiCallId="a", content="out"),
    ], "s")
    assert result["calls"][0]["model"] == "model-from-id"
    assert "unknown-model" not in result["warnings"]


@pytest.mark.guard
def test_usage_features_review_regression_within_turn_model_change_not_relabeled() -> None:
    """A model change mid-turn must attribute calls to the model active when each
    was first observed, not retroactively relabel an earlier call."""
    result = features_from_events([
        event("session.start", model="model-a"),
        event("assistant.turn_start"),
        event("session.usage_record", usage={"apiCallId": "a", "inputTokens": 1}),
        event("assistant.message", apiCallId="a", content="out-a"),
        event("session.model_change", newModel="model-b"),
        event("session.usage_record", usage={"apiCallId": "b", "inputTokens": 2}),
        event("assistant.message", apiCallId="b", content="out-b"),
    ], "s")
    by_id = {call["api_call_id"]: call["model"] for call in result["calls"]}
    assert by_id["a"] == "model-a"
    assert by_id["b"] == "model-b"


@pytest.mark.guard
def test_usage_features_review_regression_anonymous_emissions_not_double_counted() -> None:
    """Two anonymous emissions in a turn with no named calls must not each
    replay the full prompt's input-character count."""
    result = features_from_events([
        event("assistant.turn_start"),
        event("user.message", content="hello"),
        event("assistant.turn_start"),
        event("assistant.message", content="first"),
        event("assistant.message", content="second"),
    ], "s")
    anonymous = [call for call in result["calls"] if call["api_call_id"] is None]
    assert len(anonymous) == 1
    assert anonymous[0]["input_chars"] == len("hello")
    assert anonymous[0]["emissions"] == 2
    assert anonymous[0]["output_chars"] == len("first") + len("second")
