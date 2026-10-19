"""Versioned, count-only root-call features, not reconstructed billing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_logger.sessions import SessionRef
from agent_logger.usage_ledger import MAX_RECORDS, UsageEvidenceError
from agent_logger.usage_transcript import MAX_EVENTS

FEATURE_SCHEMA = "agent-logger.transcript-features"
FEATURE_VERSION = 1
FEATURE_METHOD = "root-turn-visible-replay-v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _text(value: object) -> int:
    return len(value) if isinstance(value, str) else 0


def _message_text(data: dict) -> int:
    """Count visible text whether exposed as ``text`` or ``content`` (a plain
    string or a block array of ``{"text": ...}`` entries)."""
    text = data.get("text")
    if isinstance(text, str):
        return len(text)
    content = data.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(item["text"]) for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return 0


def _identifier(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise UsageEvidenceError("invalid feature attribution identifier")
    return value


def _tokens(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > 10**15:
        raise UsageEvidenceError("invalid measured feature token quantity")
    return value


def _output(data: dict) -> int:
    result = _message_text(data)
    reasoning = data.get("reasoningText")
    if isinstance(reasoning, str):
        result += len(reasoning)
    else:
        blocks = data.get("reasoningBlocks")
        blocks = blocks.get("blocks", []) if isinstance(blocks, dict) else []
        if not isinstance(blocks, list):
            raise UsageEvidenceError("invalid exposed reasoning blocks")
        for block in blocks:
            if not isinstance(block, dict):
                raise UsageEvidenceError("invalid exposed reasoning block")
            if isinstance(block.get("text"), str):
                result += len(block["text"])
            elif isinstance(block.get("thinking"), str):
                result += len(block["thinking"])
            elif isinstance(block.get("summary"), list):
                result += sum(_text(item.get("text")) for item in block["summary"] if isinstance(item, dict))
    requests = data.get("toolRequests", [])
    if not isinstance(requests, list):
        raise UsageEvidenceError("invalid emitted tool requests")
    for request in requests:
        if not isinstance(request, dict):
            raise UsageEvidenceError("invalid emitted tool request")
        arguments = request.get("arguments")
        if isinstance(arguments, str):
            result += len(arguments)
        elif arguments is not None:
            result += len(json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return result


@dataclass
class CallFeatures:
    turn: int
    api_call_id: str | None
    model: str | None
    input_chars: int
    output_chars: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    emissions: int = 0


@dataclass
class _Turn:
    index: int
    input_chars: int
    model: str | None
    calls: dict[str, CallFeatures] = field(default_factory=dict)
    unlinked: list[tuple[int, str | None, str | None]] = field(default_factory=list)

    def call(self, api: str, model: str | None, active_model: str | None) -> CallFeatures:
        if api not in self.calls:
            # Attribute a new call to the model active when it was first
            # observed, not the turn-start model, so a within-turn model
            # change doesn't relabel it at finish() time.
            self.calls[api] = CallFeatures(
                self.index, api, model or active_model, self.input_chars,
            )
        call = self.calls[api]
        if model and call.model and model != call.model:
            raise UsageEvidenceError("conflicting root-call model evidence")
        call.model = model or call.model
        return call

    def finish(self) -> list[CallFeatures]:
        # Synthetic unlinked calls are kept out of `self.calls` (keyed by real
        # `api_call_id`) so a real ID can never collide with internal bookkeeping.
        extra: list[CallFeatures] = []
        if self.unlinked:
            if len(self.calls) == 1:
                call = next(iter(self.calls.values()))
                for count, model, active_model in self.unlinked:
                    effective = model or active_model
                    if effective and call.model and effective != call.model:
                        raise UsageEvidenceError("ambiguous root emission model")
                    call.model = call.model or effective
                    call.output_chars += count
                    call.emissions += 1
            elif len(self.calls) == 0:
                # No named call exists to attribute these emissions to and no
                # distinct request boundary is observable either: aggregate
                # every anonymous emission into one call rather than replaying
                # the same prompt's input count once per ID-less emission.
                merged = CallFeatures(self.index, None, None, self.input_chars)
                for count, model, active_model in self.unlinked:
                    effective = model or active_model
                    if effective and merged.model and effective != merged.model:
                        raise UsageEvidenceError("ambiguous root emission model")
                    merged.model = merged.model or effective
                    merged.output_chars += count
                    merged.emissions += 1
                extra.append(merged)
            else:
                for count, model, active_model in self.unlinked:
                    extra.append(CallFeatures(
                        self.index, None, model or active_model, self.input_chars,
                        output_chars=count, emissions=1,
                    ))
        for call in self.calls.values():
            call.model = call.model or self.model
        for call in extra:
            call.model = call.model or self.model
        return list(self.calls.values()) + extra


def read_transcript_features(ref: SessionRef) -> dict:
    """Reduce a stable live/archive transcript to counts before scalar joins."""
    from agent_logger.usage_reader import read_session_events

    return features_from_events(read_session_events(ref), ref.id)


def extract_features(ref: SessionRef, *, usage_store: Path | None = None) -> dict:
    """Read a stable transcript and pair its explicitly selected measurements."""
    from agent_logger.usage_feature_evidence import enrich_features

    return enrich_features(read_transcript_features(ref), ref, usage_store=usage_store)


def features_from_events(events: list[dict], session_id: str) -> dict:
    """Use root turn segments plus API IDs; a usage record can precede emission."""
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise UsageEvidenceError("feature transcript event limit exceeded")
    session_id = _identifier(session_id)
    if not session_id:
        raise UsageEvidenceError("feature session identity is unavailable")
    original_id: str | None = None
    seen: dict[str, str] = {}
    calls: list[CallFeatures] = []
    turn: _Turn | None = None
    context = turns = resets = compactions = unlinked_tools = child_events = 0
    model: str | None = None
    root_tools: set[str] = set()
    warnings: set[str] = set()
    source_digest = hashlib.sha256()
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise UsageEvidenceError("invalid transcript feature event")
        fingerprint = _digest(event)
        event_id = _identifier(event.get("id"))
        if event_id:
            if event_id in seen:
                if seen[event_id] != fingerprint:
                    raise UsageEvidenceError("conflicting replayed event identity")
                continue
            seen[event_id] = fingerprint
        source_digest.update(fingerprint.encode())
        data = event.get("data", {})
        if not isinstance(data, dict):
            raise UsageEvidenceError("invalid transcript feature data")
        nested_usage = data.get("usage")
        nested_agent = (
            _identifier(nested_usage.get("agentId")) if isinstance(nested_usage, dict) else None
        )
        if (
            _identifier(event.get("agentId")) or _identifier(data.get("agentId"))
            or data.get("parentToolCallId") or nested_agent
        ):
            child_events += 1
            continue
        kind = event["type"]
        if kind == "session.start":
            candidate = _identifier(data.get("sessionId"))
            if candidate and original_id and candidate.lower() != original_id.lower():
                raise UsageEvidenceError("conflicting original session identity")
            original_id = candidate or original_id
            model = _identifier(data.get("model")) or model
        elif kind == "session.model_change":
            model = _identifier(data.get("newModel") or data.get("modelId") or data.get("model"))
        elif kind == "session.compaction_complete":
            summary = data.get("summaryContent")
            if data.get("success") is not False:
                context = _text(summary)
                compactions += 1
            if data.get("success") is False or not isinstance(summary, str):
                warnings.add("unobserved-compaction-context")
        elif kind == "session.context_cleared":
            initial = data.get("initialMessage")
            context = _text(initial)
            resets += 1
            root_tools.clear()
        elif kind in ("user.message", "system.message"):
            context += _message_text(data)
            if data.get("attachments"):
                warnings.add("nontext-input-not-reconstructed")
        elif kind == "assistant.turn_start" and not data.get("parentToolCallId"):
            if turn:
                calls.extend(turn.finish())
            turns += 1
            turn = _Turn(turns, context, model)
        elif kind == "session.usage_record":
            usage = data.get("usage")
            if not isinstance(usage, dict):
                raise UsageEvidenceError("malformed feature usage record")
            if usage.get("parentToolCallId"):
                continue
            accounting = usage.get("accounting")
            if isinstance(accounting, dict):
                candidate = _identifier(accounting.get("sourceSessionId"))
                if candidate and original_id and candidate.lower() != original_id.lower():
                    raise UsageEvidenceError("conflicting measured session identity")
                original_id = candidate or original_id
            api = _identifier(usage.get("apiCallId"))
            if turn and api:
                call = turn.call(api, _identifier(usage.get("model")), model)
                for source, target in (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens")):
                    value = _tokens(usage.get(source))
                    old = getattr(call, target)
                    if old is not None and value is not None and old != value:
                        raise UsageEvidenceError("conflicting root-call measured tokens")
                    if value is not None:
                        setattr(call, target, value)
            else:
                warnings.add("unmatched-measured-call")
        elif kind == "assistant.message" and not data.get("parentToolCallId"):
            count = _output(data)
            context += count
            if turn:
                api = _identifier(data.get("apiCallId"))
                observed_model = _identifier(data.get("model"))
                if api:
                    call = turn.call(api, observed_model, model)
                    call.output_chars += count
                    call.emissions += 1
                else:
                    turn.unlinked.append((count, observed_model, model))
            else:
                warnings.add("emission-outside-root-turn")
            for request in data.get("toolRequests", []):
                tool_id = _identifier(request.get("toolCallId") or request.get("id"))
                if tool_id:
                    root_tools.add(tool_id)
        elif kind == "tool.execution_complete":
            tool_id = _identifier(data.get("toolCallId"))
            if tool_id and tool_id in root_tools:
                result = data.get("result")
                if isinstance(result, str):
                    context += len(result)
                elif isinstance(result, dict):
                    context += _message_text(result)
            else:
                unlinked_tools += 1
        if len(calls) + (len(turn.calls) + len(turn.unlinked) if turn else 0) > MAX_RECORDS:
            raise UsageEvidenceError("root-call feature limit exceeded")
    if turn:
        calls.extend(turn.finish())
    for call in calls:
        if call.model is None:
            warnings.add("unknown-model")
        if call.input_chars == 0:
            warnings.add("unobserved-input-context")
    return {
        "schema": FEATURE_SCHEMA, "version": FEATURE_VERSION, "method": FEATURE_METHOD,
        "session_id": (original_id or session_id).lower(),
        "source_digest": source_digest.hexdigest(), "calls": [asdict(call) for call in calls],
        "coverage": {
            "root_turns": turns, "calls": len(calls), "compactions": compactions,
            "context_resets": resets, "unlinked_tool_results": unlinked_tools,
            "child_events_excluded": child_events,
        },
        "warnings": sorted(warnings),
        "limitations": "visible replay only; hidden input, cache behavior and unrecorded output are unknown",
    }
