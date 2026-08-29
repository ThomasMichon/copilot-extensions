"""Bounded assembly for the agent-worktrees session conduct hook."""

from __future__ import annotations

import json
from importlib import metadata
import os
from pathlib import Path
import re
import sys

from . import output

MAX_OUTPUT_CHARS = 4_000
AGGREGATE_MAX_CONTEXT_BYTES = 1_200
KNOWN_FRAGMENTS = ("account-conduct.md", "worktree-conduct.md")
UNKNOWN_OMITTED = "[Additional unrecognized conduct fragments omitted.]"
RELATED_OMITTED = "[Related-repository guidance omitted to fit the conduct budget.]"
HISTORY_TRUNCATED = "[Older worktree history omitted.]"
_SEMANTIC_HISTORY_PREFIXES = ("Active effort:", "Worktree succession:")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_AGGREGATE_GUIDANCE = (
    "Agent-worktrees owns this session's worktree binding and Picker disposition. "
    "Treat an emitted binding, active-effort assignment, and succession head as "
    "authoritative; if another live head is named, coordinate instead of starting "
    "parallel work. `agent-worktrees status` is the disposition source: mark "
    "remaining work `--follow-up`, completed work `--resolved`, update title and "
    "summary when focus changes, and run `finalize` last without resuming afterward. "
    "Before GitHub mutations resolve the target account and use `repos gh`; before "
    "touching another repository use `related resolve` and obey its class, locus, "
    "and delegate. Load `agent-worktrees:worktree` and "
    "`agent-worktrees:agent-worktrees-repos` for details."
)


def _installed_package_version() -> str:
    """Resolve the installed distribution version without risking hook startup."""
    try:
        value = metadata.version("agent-worktrees").strip()
        if _VERSION_RE.fullmatch(value):
            return value
    except Exception:
        pass
    return "unknown"


def _owner_marker() -> str:
    return f"[owner: agent-worktrees@{_installed_package_version()}]"


def _payload(parts: list[str]) -> str:
    clean = [part.strip() for part in parts if part.strip()]
    if not clean:
        return "{}"
    clean.insert(0, _owner_marker())
    return json.dumps(
        {"additionalContext": "\n\n".join(clean)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compact_block(text: str, prefix: str | None = None, limit: int = 160) -> str:
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    if prefix is not None:
        blocks = [block for block in blocks if block.startswith(prefix)]
    if not blocks:
        return ""
    compact = " ".join(blocks[-1].split())
    if len(compact.encode("utf-8")) <= limit:
        return compact
    encoded = compact.encode("utf-8")[: limit - 3]
    while True:
        try:
            return encoded.decode("utf-8") + "..."
        except UnicodeDecodeError:
            encoded = encoded[:-1]


def assemble_aggregate_payload(
    definition: str,
    history: str,
    *,
    max_bytes: int = AGGREGATE_MAX_CONTEXT_BYTES,
) -> str:
    """Return the compact, read-only aggregate-mode conduct kernel."""
    owner = _owner_marker()
    candidates = {
        "definition": _compact_block(definition),
        "effort": _compact_block(history, "Active effort:"),
        "succession": _compact_block(history, "Worktree succession:"),
    }
    selected: set[str] = set()
    for name in ("succession", "effort", "definition"):
        if not candidates[name]:
            continue
        candidate = "\n\n".join(
            [
                owner,
                *(
                    [candidates["definition"]]
                    if "definition" in selected or name == "definition"
                    else []
                ),
                _AGGREGATE_GUIDANCE,
                *(
                    [candidates["effort"]]
                    if "effort" in selected or name == "effort"
                    else []
                ),
                *(
                    [candidates["succession"]]
                    if "succession" in selected or name == "succession"
                    else []
                ),
            ]
        )
        if len(candidate.encode("utf-8")) <= max_bytes:
            selected.add(name)
    context = "\n\n".join(
        [
            owner,
            *([candidates["definition"]] if "definition" in selected else []),
            _AGGREGATE_GUIDANCE,
            *([candidates["effort"]] if "effort" in selected else []),
            *([candidates["succession"]] if "succession" in selected else []),
        ]
    )
    return json.dumps(
        {"additionalContext": context},
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

    blocks = history.split("\n\n")
    semantic = [
        block.strip() for block in blocks
        if block.strip().startswith(_SEMANTIC_HISTORY_PREFIXES)
    ]
    digest_text = "\n\n".join(
        block for block in blocks
        if not block.strip().startswith(_SEMANTIC_HISTORY_PREFIXES)
    ).strip()
    digest_lines = digest_text.splitlines()
    newest_lines = [
        line for line in digest_lines
        if line.startswith("- ")
    ]

    fixed = HISTORY_TRUNCATED
    if semantic:
        fixed = "\n\n".join([fixed, *semantic])
    if not _fits([*prefix, fixed], max_chars):
        # Effort/succession instructions are semantic and must never be sliced.
        semantic_text = "\n\n".join(semantic)
        if semantic_text and _fits([*prefix, semantic_text], max_chars):
            return [*prefix, semantic_text]
        return prefix

    selected: list[str] = []
    for line in reversed(newest_lines):
        candidate_lines = [line, *selected]
        candidate = (
            f"{HISTORY_TRUNCATED}\n" + "\n".join(candidate_lines)
        )
        if semantic:
            candidate += "\n\n" + "\n\n".join(semantic)
        if not _fits([*prefix, candidate], max_chars):
            break
        selected = candidate_lines

    bounded = HISTORY_TRUNCATED
    if selected:
        bounded += "\n" + "\n".join(selected)
    if semantic:
        bounded += "\n\n" + "\n\n".join(semantic)
    return [*prefix, bounded]


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
        if "--aggregate" in sys.argv[1:]:
            payload = assemble_aggregate_payload(
                os.environ.get("AW_CONDUCT_DEFINITION", ""),
                os.environ.get("AW_CONDUCT_HISTORY", ""),
            )
        else:
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
