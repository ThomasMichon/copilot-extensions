#!/usr/bin/env python3
"""Prepare and score the identity-free context-handoff efficiency eval."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path


CANARY = "HANDOFF_FIDELITY_7f1a9c2e"
SCHEMA = "copilot-extensions.context-handoff-eval-metrics"


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _find(value: object, name: str) -> str:
    if isinstance(value, dict):
        direct = value.get(name)
        if isinstance(direct, str) and direct:
            return direct
        aliases = {
            "worktree_id": ("id",),
            "worktree_path": ("work_dir", "path"),
        }
        for alias in aliases.get(name, ()):
            direct = value.get(alias)
            if isinstance(direct, str) and direct:
                return direct
        for child in value.values():
            found = _find(child, name)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find(child, name)
            if found:
                return found
    return ""


def field(path: Path, name: str) -> int:
    print(_find(_json(path), name), end="")
    return 0


def verify(root: Path) -> int:
    seed = (root / "seed.txt").read_text(encoding="utf-8")
    recovery = seed.rsplit(" | ", 1)[-1]
    payload = (root / "payload.md").read_text(encoding="utf-8")
    save = _json(root / "save.json")
    state_dir = Path((root / "state-dir").read_text(encoding="utf-8"))
    handoff = state_dir / "handoff" / "handoff-eval-predecessor.json"
    checks = [
        len(seed) <= 200,
        "\n" not in seed,
        seed.count(" | ") == 2,
        "/consume-handoff" in seed,
        re.fullmatch(
            r"Recovery: context-handoff file:[A-Za-z0-9._-]+",
            recovery,
        )
        is not None,
        "handoff-eval-predecessor" in seed,
        CANARY in payload,
        save.get("id") == "handoff-eval-predecessor",
        handoff.is_file(),
    ]
    return 0 if all(checks) else 1


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _delta_ms(start: object, end: object) -> int | None:
    left, right = _parse_time(start), _parse_time(end)
    if left is None or right is None:
        return None
    return max(0, round((right - left).total_seconds() * 1000))


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            text
            for child in value.values()
            for text in _all_strings(child)
        ]
    if isinstance(value, list):
        return [
            text
            for child in value
            for text in _all_strings(child)
        ]
    return []


def metrics(root: Path, results: Path) -> int:
    seed = (root / "seed.txt").read_text(encoding="utf-8")
    submitted_prompt_path = results / "eval" / "prompt.txt"
    expected_submitted_prompt = (
        submitted_prompt_path.read_text(encoding="utf-8-sig")
        if submitted_prompt_path.is_file() else ""
    )
    payload = (root / "payload.md").read_text(encoding="utf-8")
    state_dir = Path((root / "state-dir").read_text(encoding="utf-8"))
    checkpoints = sorted(
        (state_dir / "handoff").glob("cutover-handoff-eval-predecessor.json")
    )
    checkpoint = _json(checkpoints[-1]) if checkpoints else {}
    turns: list[dict] = []
    turn_indexes = sorted((results / "eval").glob("run-*/turns.jsonl"))
    if not turn_indexes and (results / "eval" / "turns.jsonl").is_file():
        turn_indexes = [results / "eval" / "turns.jsonl"]
    for path in turn_indexes:
        for line in path.read_text(
            encoding="utf-8-sig", errors="replace"
        ).splitlines():
            try:
                detail = json.loads(line)
            except json.JSONDecodeError:
                continue
            turn = detail.get("turn") if isinstance(detail, dict) else None
            if isinstance(turn, dict):
                turns.append(turn)
    turns.sort(key=lambda turn: int(turn.get("turn_index", 0)))
    prompts = [
        str(turn.get("prompt") or "")
        for turn in turns
        if str(turn.get("prompt") or "").strip()
    ]
    submitted_prompts = len(prompts)
    consume_counts = []
    acknowledged_turns = []
    consume_calls_all: list[dict] = []
    for turn in turns:
        tool_calls = turn.get("tool_calls") or []
        consume_calls = [
            call
            for call in tool_calls
            if str(call.get("title") or "") == "consume_handoff"
        ]
        consume_calls_all.extend(consume_calls)
        consume_counts.append(len(consume_calls))
        acknowledged_turns.append(any(
            str(call.get("status") or "") == "completed"
            and "## Handoff Consumed" in "\n".join(_all_strings(call))
            for call in consume_calls
        ))
    consume_tool_calls = sum(consume_counts)
    turns_to_ack = next(
        (
            index for index, acknowledged in enumerate(
                acknowledged_turns, start=1
            ) if acknowledged
        ),
        None,
    )
    structured_text = "\n".join(
        text for turn in turns for text in _all_strings(turn)
    )
    consume_result_texts = [
        "".join(_all_strings(call.get("content") or []))
        for call in consume_calls_all
    ]
    structured_payload = next(
        (
            text[text.index(payload):text.index(payload) + len(payload)]
            for text in consume_result_texts
            if payload in text
        ),
        "",
    )
    steps = checkpoint.get("steps") or {}
    times = checkpoint.get("stepTimes") or {}
    details = checkpoint.get("details") or {}
    bind_detail = details.get("successorBound") or {}
    stored_payload = str(checkpoint.get("payload") or "")
    payload_sha = hashlib.sha256(payload.encode()).hexdigest()
    stored_sha = hashlib.sha256(stored_payload.encode()).hexdigest()
    record = {
        "schema": SCHEMA,
        "version": 1,
        "initialSeed": {
            "characters": len(seed),
            "estimatedTokens": math.ceil(len(seed) / 4),
            "parts": seed.count(" | ") + 1,
        },
        "submittedPrompt": {
            "characters": (
                len(prompts[0]) if len(prompts) == 1 else None
            ),
            "submittedPrompts": submitted_prompts,
            "submittedPromptSha256": (
                hashlib.sha256(prompts[0].encode()).hexdigest()
                if len(prompts) == 1 else None
            ),
            "expectedCompositeSha256": (
                hashlib.sha256(
                    expected_submitted_prompt.encode()
                ).hexdigest() if expected_submitted_prompt else None
            ),
            "promptMatchesRunnerComposite": (
                len(prompts) == 1
                and prompts[0] == expected_submitted_prompt
            ),
            "promptContainsExactHandoffSeed": (
                len(prompts) == 1 and seed in prompts[0]
            ),
        },
        "exchange": {
            "turnCount": len(turns),
            "turnsToConsumeAndAck": turns_to_ack,
            "consumeToolCalls": consume_tool_calls,
            "toolCallMetric": "agent-bridge structured turn.tool_calls exact title",
            "timeToTakeoverMs": _delta_ms(
                checkpoint.get("createdAt"), times.get("headVerified")
            ),
            "timeToRetireMs": _delta_ms(
                checkpoint.get("createdAt"), times.get("predecessorRetired")
            ),
            "timeToRetireDecisionMs": _delta_ms(
                checkpoint.get("createdAt"), times.get("predecessorPreserved")
            ),
        },
        "fidelity": {
            "expectedSha256": payload_sha,
            "storedSha256": stored_sha,
            "payloadFaithful": payload == stored_payload,
            "canaryVisible": CANARY in structured_text,
            "structuredConsumePayloadSha256": (
                hashlib.sha256(structured_payload.encode()).hexdigest()
                if structured_payload else None
            ),
            "structuredConsumeContainsFullPayload": (
                structured_payload == payload
            ),
        },
        "lifecycle": {
            "candidateAcknowledged": bool(
                bind_detail.get("candidate_acknowledged")
            ),
            "headVerified": bool(steps.get("headVerified")),
            "predecessorRetired": bool(steps.get("predecessorRetired")),
            "predecessorPreserved": bool(steps.get("predecessorPreserved")),
            "takeoverAfterConsume": bool(
                _parse_time(times.get("taskConsumed"))
                and _parse_time(times.get("headVerified"))
                and _parse_time(times["taskConsumed"])
                <= _parse_time(times["headVerified"])
            ),
        },
    }
    output = results / "context-handoff-eval-metrics.json"
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    passed = (
        record["initialSeed"]["characters"] <= 200
        and record["initialSeed"]["parts"] == 3
        and record["submittedPrompt"]["submittedPrompts"] == 1
        and record["submittedPrompt"]["promptMatchesRunnerComposite"]
        and record["submittedPrompt"]["promptContainsExactHandoffSeed"]
        and record["exchange"]["turnCount"] == 1
        and record["exchange"]["turnsToConsumeAndAck"] == 1
        and record["exchange"]["consumeToolCalls"] == 1
        and record["fidelity"]["payloadFaithful"]
        and record["fidelity"]["canaryVisible"]
        and record["fidelity"]["structuredConsumeContainsFullPayload"]
        and record["lifecycle"]["candidateAcknowledged"]
        and record["lifecycle"]["headVerified"]
        and record["lifecycle"]["predecessorPreserved"]
        and record["lifecycle"]["takeoverAfterConsume"]
    )
    return 0 if passed else 1


def self_test(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    state = root / "state"
    handoff_dir = state / "handoff"
    handoff_dir.mkdir(parents=True)
    payload = f"brief\nCanary: {CANARY}\n"
    seed = (
        "Task: Measure | Resume: /consume-handoff to take over | "
        "Recovery: context-handoff file:handoff-eval-predecessor"
    )
    (root / "seed.txt").write_text(seed, encoding="utf-8")
    (root / "payload.md").write_text(payload, encoding="utf-8")
    (root / "state-dir").write_text(str(state), encoding="utf-8")
    (root / "save.json").write_text(
        json.dumps({"id": "handoff-eval-predecessor"}), encoding="utf-8"
    )
    (handoff_dir / "handoff-eval-predecessor.json").write_text(
        "{}", encoding="utf-8"
    )
    now = "2026-01-01T00:00:00.000Z"
    later = "2026-01-01T00:00:00.250Z"
    (handoff_dir / "cutover-handoff-eval-predecessor.json").write_text(
        json.dumps({
            "createdAt": now,
            "payload": payload,
            "steps": {
                "headVerified": True,
                "predecessorPreserved": True,
            },
            "stepTimes": {
                "taskConsumed": now,
                "headVerified": later,
                "predecessorPreserved": later,
            },
            "details": {
                "successorBound": {"candidate_acknowledged": True}
            },
        }),
        encoding="utf-8",
    )
    eval_dir = root / "out" / "eval"
    eval_dir.mkdir(parents=True)
    composite_prompt = (
        "Literal mode fixture.\n\n--- TASK ---\n\n" + seed
    )
    (eval_dir / "prompt.txt").write_text(
        composite_prompt, encoding="utf-8",
    )
    turn_detail = {
        "kind": "turn",
        "session_id": "successor-session",
            "turn": {
                "turn_index": 0,
                "prompt": composite_prompt,
                "response_text": f"acknowledged {CANARY}",
                "stop_reason": "end_turn",
                "tool_calls": [{
                    "tool_call_id": "tool-1",
                    "title": "consume_handoff",
                    "kind": "other",
                    "status": "completed",
                    "content": [f"## Handoff Consumed\n\n{payload}"],
                }],
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:00:01Z",
            }
        }
    (eval_dir / "turn-detail.json").write_text(
        json.dumps(turn_detail),
        encoding="utf-8",
    )
    (eval_dir / "turns.jsonl").write_text(
        json.dumps(turn_detail, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return verify(root) or metrics(root, root / "out")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    field_parser = sub.add_parser("field")
    field_parser.add_argument("--path", type=Path, required=True)
    field_parser.add_argument("--name", required=True)
    for name in ("verify", "self-test"):
        item = sub.add_parser(name)
        item.add_argument("--root", type=Path, required=True)
    metric_parser = sub.add_parser("metrics")
    metric_parser.add_argument("--root", type=Path, required=True)
    metric_parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "field":
        return field(args.path, args.name)
    if args.command == "verify":
        return verify(args.root)
    if args.command == "metrics":
        return metrics(args.root, args.results)
    return self_test(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
