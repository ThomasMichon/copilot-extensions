"""Literal Summary/Claims/Bridge rendering from independently qualified owner facts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from .companion_bridge_current_requests import CurrentRequestState
from .companion_timeline_contract import Availability, Provider
from .companion_timeline_model import TimelineWindow

if TYPE_CHECKING:
    from .mux_companion import _CompanionData, MuxCompanionApp


def render_current_input(app: MuxCompanionApp, state: CurrentRequestState | None) -> None:
    if app.query("#timeline-current-input"):
        label = app.query_one("#timeline-current-input", Static)
        label.display = app._bridge_attachment.choice != "membership"
        label.update(Text(
            state.summary() if state is not None else
            "Current input unknown; no qualified selected-session observation.",
        ))


def bridge_status(window: TimelineWindow, message: str) -> Text:
    lines = ["Bridge: " + message]
    for state in window.sources.values():
        if (
            state.token.identity.provider is Provider.BRIDGE
            and state.availability is Availability.OBSERVED
            and state.snapshot is not None and state.snapshot.facts_json is not None
        ):
            facts = json.loads(state.snapshot.facts_json)
            if "membership-replay-incomplete" in facts.get("limitations", []):
                lines.append("Membership replay incomplete; current members unknown.")
            lines.extend(
                f"{member['bridge_session_id']}: {member['state']} "
                f"({member['registry']}, link {member['link_qualification']})"
                for member in facts.get("members", [])
            )
    return Text("\n".join(lines))


def _fact(data: _CompanionData, name: str) -> tuple[dict | None, str]:
    """Keep each producer observation and its uncertainty together."""
    if data.bundle_error:
        return None, f"Unavailable: {data.bundle_error}"
    fact = data.bundle.get("facts", {}).get(name)
    if not isinstance(fact, dict) or not isinstance(fact.get("value"), dict):
        return None, "Unavailable: the engine supplied no observation."
    state = "Confirmed" if fact.get("confirmed") is True else "Last known; unconfirmed"
    observed = fact.get("observed_at")
    if isinstance(observed, (int, float)) and not isinstance(observed, bool):
        try:
            stamp = datetime.fromtimestamp(observed, timezone.utc).isoformat(timespec="seconds")
            state += f" at {stamp}"
        except (ValueError, OverflowError, OSError):
            state += "; observation time unavailable"
    else:
        state += "; observation time unavailable"
    return fact["value"], state


def _summary_text(data: _CompanionData) -> Text:
    text = Text()
    disposition, evidence = _fact(data, "disposition")
    text.append("Purpose\n", style="bold #d78700")
    if disposition is None:
        text.append(evidence + "\n")
    else:
        text.append(str(disposition.get("title") or "No purpose reported.") + "\n\n")
        text.append("Summary\n", style="bold")
        text.append(str(disposition.get("summary") or "No summary reported.") + "\n")
        text.append(evidence + "\n", style="dim")
        flags = []
        if disposition.get("follow_up"):
            flags.append("Follow-up required")
        if disposition.get("paused"):
            flags.append("Intentionally paused")
        if flags:
            text.append(" | ".join(flags) + "\n", style="bold #d7af00")
    claims, claims_evidence = _fact(data, "claims")
    text.append("\nClaims\n", style="bold")
    resources = claims.get("resources") if claims is not None else None
    if isinstance(resources, list) and all(isinstance(row, dict) for row in resources):
        text.append(f"{len(resources)} recorded resource(s); see Claims for states and references.\n")
    else:
        text.append("Resource ledger unavailable.\n")
    text.append(claims_evidence, style="dim")
    return text


def _claims_view(data: _CompanionData) -> tuple[Text, list[dict]]:
    claims, evidence = _fact(data, "claims")
    text = Text(evidence + "\n")
    if claims is None:
        return text, []
    text.append("Owned by: ", style="bold")
    text.append(str(claims.get("owner_ref") or "(no owner recorded)") + "\n")
    resources = claims.get("resources")
    if not isinstance(resources, list) or any(not isinstance(row, dict) for row in resources):
        text.append("Resource ledger unavailable: invalid claims observation.", style="bold yellow")
        return text, []
    text.append(
        f"{len(resources)} recorded resource(s). States are reported by the owning engine."
        if resources else "No recorded resources in this observation."
    )
    return text, resources
