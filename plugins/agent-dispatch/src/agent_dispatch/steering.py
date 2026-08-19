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

import json
import re

#: Supported field types in a ``request-input`` spec.
FIELD_TEXT = "text"
FIELD_TEXTAREA = "textarea"
FIELD_CHOICE = "choice"
FIELD_MULTICHOICE = "multichoice"
FIELD_TYPES = frozenset({FIELD_TEXT, FIELD_TEXTAREA, FIELD_CHOICE, FIELD_MULTICHOICE})
#: The choice-family types (they carry ``options`` and may allow an ``other``).
FIELD_CHOICE_TYPES = frozenset({FIELD_CHOICE, FIELD_MULTICHOICE})

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
    * ``tags:multichoice[perf,api,ux]`` -> a multi-select (answer is a JSON array)
    * a trailing ``*`` option in a choice/multichoice bracket declares an
      **"Other…"** affordance: ``severity:choice[low,med,high,*]`` -> single-select
      of low/med/high **or** a free-text "other" answer. ``*`` is stripped from
      ``options`` and recorded as ``allow_other: true``.

    Returns ``[{"name", "type", "options"?, "allow_other"?}]`` (``options`` +
    ``allow_other`` only for a choice/multichoice). A ``None``/empty spec yields
    ``[]`` (a card with no form -- a pure status/notification card). Raises
    :class:`SteeringError` on a malformed spec so a producer catches the mistake
    early.

    Commas inside ``choice[...]`` / ``multichoice[...]`` are **not** field
    separators; the bracket content is parsed first, then the remainder split on
    top-level commas.
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

        m = re.match(r"^(choice|multichoice)\[(.*)\]$", type_part)
        if m:
            ftype = m.group(1)
            raw = [o.strip() for o in m.group(2).split(",") if o.strip()]
            # A trailing/anywhere ``*`` sentinel opts the question into an
            # "Other…" free-text answer; it is not a real option.
            allow_other = "*" in raw
            options = [o for o in raw if o != "*"]
            if not options:
                raise SteeringError(f"{ftype} field {name!r} has no options")
            field = {"name": name, "type": ftype, "options": options}
            if allow_other:
                field["allow_other"] = True
            fields.append(field)
            continue

        if type_part not in FIELD_TYPES or type_part in FIELD_CHOICE_TYPES:
            raise SteeringError(
                f"field {name!r}: unknown type {type_part!r} "
                f"(text | textarea | choice[a,b,...] | multichoice[a,b,...]; "
                f"add * for an Other option)"
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


def _decode_multi(value: object) -> list[str]:
    """Decode a multichoice answer into a list of member strings.

    The form encodes a multi-select answer as a JSON array string (so a member
    that contains a comma -- e.g. an ``Other…`` free-text answer -- survives).
    Falls back to a comma-split for a bare string, and tolerates an already-
    decoded list. Never raises.
    """
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if value is None:
        return []
    s = str(value).strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            decoded = json.loads(s)
            if isinstance(decoded, list):
                return [str(v) for v in decoded]
        except (ValueError, TypeError):
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def validate_steer_fields(fields: dict, request_input: list[dict] | None) -> None:
    """Best-effort check of an operator's submitted answer against a card's form.

    * A declared **choice**/**multichoice** field's value (when present) must be
      drawn from its options -- unless the field is ``allow_other`` (a declared
      "Other…" affordance), in which case any value passes (the operator typed a
      free-text answer). A ``multichoice`` value is a JSON array of members, each
      validated the same way.
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
        if not spec or spec.get("type") not in FIELD_CHOICE_TYPES:
            continue
        if spec.get("allow_other"):
            continue  # a declared Other -> any free-text value is valid
        options = spec.get("options") or []
        if spec.get("type") == FIELD_MULTICHOICE:
            for member in _decode_multi(value):
                if member not in options:
                    raise SteeringError(
                        f"field {key!r} member {member!r} is not one of {options}"
                    )
        elif value is not None and str(value) not in options:
            raise SteeringError(
                f"field {key!r}={value!r} is not one of {options}"
            )
