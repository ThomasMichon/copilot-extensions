"""Bounded assembly for the agent-worktrees session conduct hook."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from . import output

MAX_OUTPUT_CHARS = 4_000
KNOWN_FRAGMENTS = ("account-conduct.md", "worktree-conduct.md")
UNKNOWN_OMITTED = "[Additional unrecognized conduct fragments omitted.]"
RELATED_OMITTED = "[Related-repository guidance omitted to fit the conduct budget.]"
HISTORY_TRUNCATED = "[Older worktree history omitted.]"


def _payload(parts: list[str]) -> str:
    clean = [part.strip() for part in parts if part.strip()]
    if not clean:
        return "{}"
    return json.dumps(
        {"additionalContext": "\n\n".join(clean)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def runtime_units(text: str) -> int:
    """Return JavaScript/Node string length (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _fits(parts: list[str], max_chars: int) -> bool:
    return runtime_units(_payload(parts)) <= max_chars


def _bounded_history(
    prefix: list[str],
    history: str,
    *,
    max_chars: int,
) -> list[str]:
    history = history.strip()
    if not history:
        return prefix
    if _fits([*prefix, history], max_chars):
        return [*prefix, history]
    if not _fits([*prefix, HISTORY_TRUNCATED], max_chars):
        return prefix

    low, high = 0, len(history)
    while low < high:
        keep = (low + high + 1) // 2
        candidate = [*prefix, f"{HISTORY_TRUNCATED}\n{history[-keep:]}"]
        if _fits(candidate, max_chars):
            low = keep
        else:
            high = keep - 1
    if low:
        return [*prefix, f"{HISTORY_TRUNCATED}\n{history[-low:]}"]
    return [*prefix, HISTORY_TRUNCATED]


def _read_fragments(conduct_dir: Path) -> tuple[list[str], list[str], bool]:
    known: list[str] = []
    extras: list[str] = []
    omitted_extra = False
    if not conduct_dir.is_dir():
        return known, extras, omitted_extra

    for name in KNOWN_FRAGMENTS:
        path = conduct_dir / name
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            text = ""
        if text:
            known.append(text)

    known_names = set(KNOWN_FRAGMENTS)
    for path in sorted(conduct_dir.glob("*.md")):
        if path.name in known_names:
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            omitted_extra = True
            continue
        if text:
            extras.append(text)
    return known, extras, omitted_extra


def assemble_payload(
    conduct_dir: Path,
    definition: str,
    related: str,
    history: str,
    *,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> str:
    """Return one valid JSON object no longer than *max_chars* characters.

    State-root and known safety fragments are mandatory. Related guidance is
    next in priority, followed by bounded history. Unrecognized ``*.md`` files
    are included only when the complete payload fits; otherwise one marker
    reports their omission.
    """
    known, extras, unreadable_extra = _read_fragments(conduct_dir)
    mandatory = [definition.strip(), *known]
    mandatory = [part for part in mandatory if part]
    if not _fits(mandatory, max_chars):
        raise ValueError("mandatory conduct exceeds the output budget")

    related = related.strip()
    history = history.strip()
    full = [*mandatory]
    if related:
        full.append(related)
    full.extend(extras)
    if history:
        full.append(history)
    if not unreadable_extra and _fits(full, max_chars):
        return _payload(full)

    prefix = [*mandatory]
    if related:
        prefix.append(related)
    if extras or unreadable_extra:
        prefix.append(UNKNOWN_OMITTED)

    with_history = _bounded_history(prefix, history, max_chars=max_chars)
    if _fits(with_history, max_chars):
        return _payload(with_history)

    prefix = [*mandatory]
    if extras or unreadable_extra:
        candidate = [*prefix, UNKNOWN_OMITTED]
        if _fits(candidate, max_chars):
            prefix = candidate
    if related:
        candidate = [*prefix, RELATED_OMITTED]
        if _fits(candidate, max_chars):
            prefix = candidate
    return _payload(_bounded_history(prefix, history, max_chars=max_chars))


def main() -> int:
    output.ensure_utf8_stdio()
    conduct_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path()
    try:
        payload = assemble_payload(
            conduct_dir,
            os.environ.get("AW_CONDUCT_DEFINITION", ""),
            os.environ.get("AW_CONDUCT_RELATED", ""),
            os.environ.get("AW_CONDUCT_HISTORY", ""),
        )
    except Exception:
        payload = "{}"
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
