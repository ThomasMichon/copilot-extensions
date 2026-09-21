"""Pure Python reimplementation of context-handoff's short cutover-seed
contract (``plugins/context-handoff/extensions/context-handoff/cutover-seed.mjs``).

The coordinator's handoff-fallback launch (:mod:`agent_dispatch.coordinator`)
hands a real Copilot process's **first interactive turn** to
``agent-bridge resume <worktree> --force`` (then ``send``), exactly the way a
live handoff hands one to
``agent-worktrees handoff-cutover``. That seed's shape -- short, ASCII, a task
lead, a `/consume-handoff` pointer, and an opaque recovery locator -- is a
stable, already-tested cross-language contract; this module deliberately
duplicates only that small pure-string format (not the full JS module, which
also owns storage/trigger/manual-fallback concerns this coordinator path does
not need) so the coordinator never takes a Node.js runtime dependency merely
to build one line of text.

Keep this format synchronized with ``cutover-seed.mjs``'s ``buildCutoverSeed``/
``leadFrom``/``recoveryLocatorFor`` if that contract ever changes.
"""

from __future__ import annotations

import re

#: Mirrors ``cutover-seed.mjs``'s ``MAX_CUTOVER_SEED_LENGTH``.
MAX_CUTOVER_SEED_LENGTH = 200

_NON_ASCII_OR_CONTROL = re.compile(r"[^\x20-\x7E]")
_WHITESPACE_RUN = re.compile(r"\s+")
_LEADING_TASK_LABEL = re.compile(r"^(?:continue|task)\s*:\s*", re.IGNORECASE)
_SAFE_LOCATOR_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")


def _seed_field(value: str | None) -> str:
    raw = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _NON_ASCII_OR_CONTROL.sub("", raw)
    text = text.replace("|", " ")
    return _WHITESPACE_RUN.sub(" ", text).strip()


def lead_from(title: str | None) -> str:
    """Build the ``Task: <lead>`` clause -- mirrors ``cutover-seed.mjs``'s ``leadFrom``."""
    normalized = _LEADING_TASK_LABEL.sub("", _seed_field(title))[:72].strip()
    return f"Task: {normalized or 'Continue the current work'}"


def recovery_locator_for(kind: str, task_id: str) -> str:
    """Build the ``<kind>:<id>`` recovery locator -- mirrors
    ``cutover-seed.mjs``'s ``recoveryLocatorFor``. ``kind`` is always
    ``"task"`` for an agent-dispatch-backed handoff."""
    if kind not in ("task", "file"):
        raise ValueError(f"unsupported handoff recovery kind: {kind!r}")
    token = str(task_id or "").strip()
    if not token or not _SAFE_LOCATOR_TOKEN.match(token):
        raise ValueError("handoff recovery id contains unsafe characters")
    return f"{kind}:{token}"


def build_cutover_seed(kind: str, task_id: str, lead: str) -> str:
    """Build the short successor seed -- mirrors ``cutover-seed.mjs``'s
    ``buildCutoverSeed``: a task/title lead, one recommendation to use
    ``/consume-handoff``, and one short opaque recovery locator. Falls back to
    the generic lead if the full seed would exceed
    :data:`MAX_CUTOVER_SEED_LENGTH`.
    """
    locator = recovery_locator_for(kind, task_id)
    recommendation = "Resume: /consume-handoff to take over"
    task_lead = _seed_field(lead) or lead_from(None)
    seed = f"{task_lead} | {recommendation} | Recovery: context-handoff {locator}"
    if len(seed) > MAX_CUTOVER_SEED_LENGTH:
        task_lead = lead_from(None)
        seed = f"{task_lead} | {recommendation} | Recovery: context-handoff {locator}"
    if len(seed) > MAX_CUTOVER_SEED_LENGTH:
        raise ValueError(
            f"handoff recovery locator exceeds {MAX_CUTOVER_SEED_LENGTH} characters"
        )
    return seed


def build_fallback_seed(task_id: str, title: str | None) -> str:
    """Convenience wrapper: the fallback-launch seed for an agent-dispatch
    handoff task (always ``kind="task"``)."""
    return build_cutover_seed("task", task_id, lead_from(title))
