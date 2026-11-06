"""Optional passive expected-consumer comparison and literal Recovery display."""

from __future__ import annotations

import re
from datetime import datetime

from rich.text import Text

from . import engine_client as ec
from .companion_timeline_contract import literal_display

CONTRACT = "agent-worktrees.handoff-head-observation"
COMMAND = "handoff-head-observation"
_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,160}\Z")
_REASON = re.compile(r"[a-z0-9-]{1,160}\Z")


def _segment(value) -> bool:
    return isinstance(value, str) and value not in {".", ".."} and bool(_SEGMENT.fullmatch(value))


def _integer(value) -> bool:
    return type(value) is int and value >= 0


def _timestamp(value, *, aware=False) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return not aware or parsed.tzinfo is not None
    except ValueError:
        return False


def declaration_error(snapshot: dict) -> str | None:
    """Accept only the selected runtime's exact known optional invocation."""
    if "handoff_head_observation" not in snapshot:
        return "unsupported-provider"
    value = snapshot["handoff_head_observation"]
    if (
        not isinstance(value, dict)
        or value.get("contract") != CONTRACT
        or type(value.get("version")) is not int
        or value.get("command") != COMMAND
    ):
        return "malformed-declaration"
    return None if value["version"] == 1 else "unsupported-contract-version"


def observation_error(value, project: str, worktree_id: str) -> str | None:
    """Reject contradictory or wrong-subject results, not just malformed JSON."""
    if not isinstance(value, dict) or value.get("contract") != CONTRACT:
        return "malformed-comparison"
    if type(value.get("contract_version")) is not int:
        return "malformed-comparison"
    if value["contract_version"] != 1:
        return "unsupported-contract-version"
    if (
        value.get("project") != project
        or value.get("worktree_id") != worktree_id
        or value.get("status") not in ("match", "mismatch", "unknown", "unavailable")
        or not isinstance(value.get("reason"), str)
        or not _REASON.fullmatch(value["reason"])
        or any(key not in value for key in ("expected", "durable_head", "picker"))
    ):
        return "malformed-comparison"
    subject = value.get("subject")
    if subject is not None and (
        not isinstance(subject, dict)
        or not _segment(subject.get("creation_nonce"))
        or not all(_integer(subject.get(key)) for key in ("lifecycle_revision", "head_revision"))
    ):
        return "malformed-subject"
    for key in ("durable_head", "picker"):
        fact = value[key]
        if not isinstance(fact, dict) or fact.get("status") not in (
            "observed",
            "unavailable",
            "stale",
        ):
            return "malformed-head-fact"
        if fact["status"] == "observed":
            if (
                "session_id" not in fact
                or fact["session_id"] is not None
                and not _segment(fact["session_id"])
                or subject is None
            ):
                return "malformed-head-fact"
            if key == "durable_head" and (
                not _integer(fact.get("revision")) or fact["revision"] != subject["head_revision"]
            ):
                return "malformed-head-fact"
        elif "session_id" in fact:
            return "malformed-head-fact"
    expected = value["expected"]
    if expected is not None:
        if (
            not isinstance(expected, dict)
            or subject is None
            or not all(_segment(expected.get(key)) for key in ("token", "predecessor", "consumer"))
            or expected.get("source") not in ("file", "task")
            or not _timestamp(expected.get("consumed_at"), aware=True)
        ):
            return "malformed-expected-consumer"
        identity = expected.get("identity")
        if (
            not isinstance(identity, dict)
            or type(identity.get("version")) is not int
            or identity["version"] != 1
            or identity.get("project") != project
            or identity.get("worktreeId") != worktree_id
            or identity.get("creationNonce") != subject["creation_nonce"]
            or identity.get("sessionId") != expected["consumer"]
        ):
            return "malformed-consumer-identity"
        activation = identity.get("activation")
        if (
            not isinstance(activation, dict)
            or type(activation.get("version")) is not int
            or activation["version"] != 1
            or not _integer(activation.get("ordinal"))
            or activation["ordinal"] == 0
            or not _timestamp(activation.get("startedAt"))
            or not _timestamp(activation.get("startRecordedAt"))
            or not isinstance(activation.get("sourceIdentity"), str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", activation["sourceIdentity"]) is None
        ):
            return "malformed-consumer-activation"
    if value["status"] in ("match", "mismatch"):
        if expected is None or any(
            value[key]["status"] != "observed" for key in ("durable_head", "picker")
        ):
            return "incomplete-comparison"
        matches = all(
            value[key]["session_id"] == expected["consumer"] for key in ("durable_head", "picker")
        )
        if matches != (value["status"] == "match"):
            return "contradictory-comparison"
    return None


def read_observation(
    project: str | None,
    worktree_id: str,
    snapshot: dict,
    *,
    runner=None,
) -> tuple[dict | None, str | None]:
    """Read once after declaration; never probe legacy syntax or retry effects."""
    error = declaration_error(snapshot)
    if error:
        return None, error
    if not _segment(project) or not _segment(worktree_id):
        return None, "selected-subject-unavailable"
    try:
        value = ec.run_json(
            project,
            [COMMAND, "--worktree", worktree_id, "--contract-version", "1", "--json"],
            timeout=5,
            runner=runner,
        )
    except ec.EngineCleanupError:
        raise
    except ec.EngineError as exc:
        return None, f"comparison-read-failed: {exc}"
    error = observation_error(value, project, worktree_id)
    return (None, error) if error else (value, None)


def observation_text(value: dict | None, error: str | None) -> Text:
    """Keep the independent facts and uncertainty separate from the head dot."""
    if value is None:
        diagnostic = literal_display(error or "not loaded")
        if len(diagnostic) > 1024:
            diagnostic = diagnostic[:1024] + " (diagnostic omitted)"
        return Text(f"Handoff comparison: unavailable ({diagnostic})", style="#b2b2b2")
    status = value["status"]
    text = Text(
        f"Handoff comparison: {status}",
        style={"match": "green", "mismatch": "bold yellow"}.get(status, "#b2b2b2"),
    )
    text.append(f" - {value['reason'].replace('-', ' ')}")
    expected = value["expected"]
    text.append(f"\nExpected consumer: {expected['consumer'] if expected else 'unknown'}")
    for key, label in (("durable_head", "Durable head"), ("picker", "Picker target (sampled)")):
        fact = value[key]
        target = (
            (fact["session_id"] or "(none)") if fact["status"] == "observed" else fact["status"]
        )
        text.append(f"\n{label}: {target}")
    if expected:
        text.append(f"\nPrevious: {expected['predecessor']}; {expected['source']} consumption")
    return text


def observation_details(value: dict | None) -> Text:
    """Present retained source identity only on explicit evidence expansion."""
    if value is None:
        return Text("No qualified comparison response.")
    text = Text("Sampled observation only; not a launch or retirement guarantee.")
    subject = value.get("subject")
    if subject:
        text.append(
            f"\nWorktree incarnation: {subject['creation_nonce']}"
            f"\nLifecycle revision: {subject['lifecycle_revision']}; head revision: {subject['head_revision']}"
        )
    expected = value["expected"]
    if expected:
        activation = expected["identity"]["activation"]
        text.append(
            f"\nBaton: {expected['token']}\nConsumed: {expected['consumed_at']}"
            f"\nConsumer activation: {activation['ordinal']} ({activation['sourceIdentity']})"
            f"\nStarted: {activation['startedAt']}; recorded: {activation['startRecordedAt']}"
        )
    return text
