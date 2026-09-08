#!/usr/bin/env python3
"""Prepare and score the process-manager-agnostic context-handoff eval."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


CANARY = "HANDOFF_FIDELITY_7f1a9c2e"
SCHEMA = "copilot-extensions.context-handoff-eval-metrics"


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def field(path: Path, name: str) -> int:
    value = _json(path).get(name, "")
    if isinstance(value, str):
        print(value, end="")
    return 0


def verify(root: Path) -> int:
    seed = (root / "seed.txt").read_text(encoding="utf-8")
    recovery = seed.rsplit(" | ", 1)[-1]
    payload = (root / "payload.md").read_text(encoding="utf-8")
    save = _json(root / "save.json")
    state_dir = Path((root / "state-dir").read_text(encoding="utf-8"))
    handoff = state_dir / "handoff" / "handoff-eval-predecessor.json"
    session_state = state_dir / "session-state" / "handoff-request.json"
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
        session_state.is_file(),
    ]
    return 0 if all(checks) else 1


def metrics(root: Path, results: Path) -> int:
    seed = (root / "seed.txt").read_text(encoding="utf-8")
    expected_prompt_path = results / "eval" / "prompt.txt"
    expected_prompt = (
        expected_prompt_path.read_text(encoding="utf-8-sig")
        if expected_prompt_path.is_file() else ""
    )
    payload = (root / "payload.md").read_text(encoding="utf-8")
    state_dir = Path((root / "state-dir").read_text(encoding="utf-8"))
    session_state = _json(state_dir / "session-state" / "handoff-request.json")
    turns: list[dict] = []
    turns_path = results / "eval" / "turns.jsonl"
    if turns_path.is_file():
        for line in turns_path.read_text(
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
    tool_calls = [
        call
        for turn in turns
        for call in (turn.get("tool_calls") or [])
        if str(call.get("status") or "") == "completed"
    ]
    trigger_calls = [
        call for call in tool_calls
        if str(call.get("title") or "") == "trigger_handoff"
    ]
    consume_calls = [
        call for call in tool_calls
        if str(call.get("title") or "") == "consume_handoff"
    ]
    structured_text = "\n".join(
        text for turn in turns for text in _all_strings(turn)
    )
    payload_sha = hashlib.sha256(payload.encode()).hexdigest()
    stored_sha = hashlib.sha256(
        str(session_state.get("promptText") or "").encode()
    ).hexdigest()
    final_seed_present = seed in structured_text
    record = {
        "schema": SCHEMA,
        "version": 1,
        "initialSeed": {
          "characters": len(seed),
          "estimatedTokens": math.ceil(len(seed) / 4),
          "parts": seed.count(" | ") + 1,
        },
        "submittedPrompt": {
          "submittedPrompts": len(prompts),
          "promptMatchesRunnerComposite": len(prompts) == 1 and prompts[0] == expected_prompt,
          "promptContainsExactHandoffSeed": len(prompts) == 1 and seed in prompts[0],
        },
        "exchange": {
          "turnCount": len(turns),
          "triggerToolCalls": len(trigger_calls),
          "consumeToolCalls": len(consume_calls),
        },
        "fidelity": {
          "expectedSha256": payload_sha,
          "sessionStateSha256": stored_sha,
          "payloadFaithful": str(session_state.get("promptText") or "") == payload,
          "canaryVisible": CANARY in structured_text,
        },
        "lifecycle": {
          "sessionStateWritten": bool(session_state),
          "finalSeedPresent": final_seed_present,
          "manualFallbackShown": "No control system acknowledged the request" in structured_text,
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
        and record["exchange"]["triggerToolCalls"] == 1
        and record["fidelity"]["payloadFaithful"]
        and record["fidelity"]["canaryVisible"]
        and record["lifecycle"]["sessionStateWritten"]
        and record["lifecycle"]["finalSeedPresent"]
        and record["lifecycle"]["manualFallbackShown"]
    )
    return 0 if passed else 1


def self_test(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    state = root / "state"
    handoff_dir = state / "handoff"
    session_dir = state / "session-state"
    handoff_dir.mkdir(parents=True)
    session_dir.mkdir(parents=True)
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
        json.dumps({"promptText": payload}), encoding="utf-8"
    )
    (session_dir / "handoff-request.json").write_text(
        json.dumps({
            "handoffId": "handoff-eval-predecessor",
            "promptText": payload,
            "seed": seed,
        }),
        encoding="utf-8",
    )
    eval_dir = root / "out" / "eval"
    eval_dir.mkdir(parents=True)
    composite_prompt = "Literal mode fixture.\n\n--- TASK ---\n\n" + seed
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
                "title": "trigger_handoff",
                "kind": "other",
                "status": "completed",
                "content": [
                    "Handoff request signaled.\n\n"
                    "No control system acknowledged the request during the grace window.\n\n"
                    "Final short handoff prompt/seed:\n\n"
                    f"```text\n{seed}\n```"
                ],
            }],
        },
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
