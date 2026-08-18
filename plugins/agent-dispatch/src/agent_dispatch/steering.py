"""Steering -- the card + steer seam that lets a dispatched agent block on
**operator input** and be woken with the answer.

A goal-loop worker sometimes needs a decision only a human can make (screen a
review draft before it posts, choose between resolutions, confirm a risky step).
Rather than sit on a live session, it **posts a card** describing what it needs
and **suspends**; the operator answers later through any surface (the DISPATCH
picker pivot, an ``ask_user`` skill, a raw CLI call), and the coordinator **wakes
the worker** with the answer. This is the human-in-the-loop analogue of
*hibernate-the-wait* (which blocks on a machine condition).

This module is the pure, dependency-free core:

* :func:`parse_request_input` turns the compact ``--request-input`` spec a card
  carries into a structured field list a renderer can lay out.
* :func:`build_card` assembles the stored card object from its parts.
* :func:`validate_steer_fields` checks an operator's submitted answer against the
  card's declared fields (best-effort; unknown fields pass through).

The queue stores the card/steer objects opaquely, so the coordinator stays a
**general** steering substrate -- nothing here is review-specific.
"""

from __future__ import annotations

import re

#: Supported field types in a ``request-input`` spec.
FIELD_TEXT = "text"
FIELD_TEXTAREA = "textarea"
FIELD_CHOICE = "choice"
FIELD_TYPES = frozenset({FIELD_TEXT, FIELD_TEXTAREA, FIELD_CHOICE})

#: Bounds so a card can never balloon into a transcript (mirrors the progress
#: beat's discipline).
CARD_TITLE_MAX = 200
CARD_STATUS_MAX = 400
CARD_BODY_MAX = 20000
FIELD_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


class SteeringError(ValueError):
    """A malformed request-input spec or an invalid steer submission."""


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value if len(value) <= limit else value[:limit]


def parse_request_input(spec: str | None) -> list[dict]:
    """Parse a compact ``--request-input`` spec into a structured field list.

    Grammar (comma-separated fields; each ``name:type`` or ``name`` [=> text]):

    * ``feedback`` / ``feedback:text`` -> a one-line text field
    * ``notes:textarea`` -> a multi-line text field
    * ``decision:choice[revise,post-approved,hold-all]`` -> a single-select

    Returns ``[{"name", "type", "options": [...]}]`` (``options`` only for a
    choice). A ``None``/empty spec yields ``[]`` (a card with no form -- a pure
    status/notification card). Raises :class:`SteeringError` on a malformed spec
    so a producer catches the mistake early.

    Commas inside ``choice[...]`` are **not** field separators; the bracket
    content is parsed first, then the remainder split on top-level commas.
    """
    if not spec or not spec.strip():
        return []

    fields: list[dict] = []
    seen: set[str] = set()
    i = 0
    text = spec.strip()
    n = len(text)
    while i < n:
        # Read a field token up to the next top-level comma, honoring a [...] run.
        start = i
        depth = 0
        while i < n:
            ch = text[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                break
            i += 1
        token = text[start:i].strip()
        i += 1  # skip the comma
        if not token:
            continue
        name, _sep, type_part = token.partition(":")
        name = name.strip()
        type_part = type_part.strip()
        if not FIELD_NAME_RE.match(name):
            raise SteeringError(
                f"invalid field name {name!r} (letters, digits, _-, leading letter)"
            )
        if name in seen:
            raise SteeringError(f"duplicate field {name!r}")
        seen.add(name)

        if not type_part:
            fields.append({"name": name, "type": FIELD_TEXT})
            continue

        m = re.match(r"^choice\[(.*)\]$", type_part)
        if m:
            options = [o.strip() for o in m.group(1).split(",") if o.strip()]
            if not options:
                raise SteeringError(f"choice field {name!r} has no options")
            fields.append({"name": name, "type": FIELD_CHOICE, "options": options})
            continue

        if type_part not in FIELD_TYPES or type_part == FIELD_CHOICE:
            raise SteeringError(
                f"field {name!r}: unknown type {type_part!r} "
                f"(text | textarea | choice[a,b,...])"
            )
        fields.append({"name": name, "type": type_part})

    return fields


def build_card(
    *,
    title: str | None = None,
    status: str | None = None,
    link: str | None = None,
    body: str | None = None,
    request_input: list[dict] | None = None,
    ts: float | None = None,
) -> dict:
    """Assemble the stored card object from its parts (bounds-checked).

    A card is the glanceable brief the operator sees for a blocked task: a
    ``title`` + one-line ``status`` overview + an optional ``link`` (to the rich
    artifact) + a scrollable ``body`` + the ``request_input`` form to fill. All
    parts are optional; a card with a non-empty ``request_input`` marks the task
    as *awaiting operator steering*.
    """
    card: dict = {}
    t = _clip(title, CARD_TITLE_MAX)
    if t:
        card["title"] = t
    s = _clip(status, CARD_STATUS_MAX)
    if s:
        card["status"] = s
    link = (link or "").strip()
    if link:
        card["link"] = link
    b = _clip(body, CARD_BODY_MAX)
    if b:
        card["body"] = b
    if request_input:
        card["request_input"] = request_input
    if ts is not None:
        card["ts"] = ts
    return card


def validate_steer_fields(fields: dict, request_input: list[dict] | None) -> None:
    """Best-effort check of an operator's submitted answer against a card's form.

    * Every declared **choice** field's value (when present) must be one of its
      options.
    * Extra/unknown fields pass through (forward-compatible; a surface may send
      more than the card asked for).
    * A missing field is allowed here (a surface may enforce "required" itself);
      the point is to catch a *wrong choice*, not to gate submission.

    Raises :class:`SteeringError` on a value outside a declared choice set.
    """
    if not request_input:
        return
    by_name = {f["name"]: f for f in request_input if isinstance(f, dict) and "name" in f}
    for key, value in fields.items():
        spec = by_name.get(key)
        if not spec or spec.get("type") != FIELD_CHOICE:
            continue
        options = spec.get("options") or []
        if value is not None and str(value) not in options:
            raise SteeringError(
                f"field {key!r}={value!r} is not one of {options}"
            )
