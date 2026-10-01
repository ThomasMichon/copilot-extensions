"""Re-express the persisted Copilot model/effort/context preference as
explicit CLI flags at launch time.

Copilot CLI has been observed to ignore its own persisted
``~/.copilot/settings.json`` values (``model`` / ``effortLevel`` /
``contextTier``) at startup, honoring only an explicit CLI flag or a
mid-session ``/model`` change -- a gap recorded against more than one of
this facility's launch paths that start a Copilot process on an operator's
behalf. ``agent-machines`` remains the single source of truth for the
preference on a managed machine (it is typically the sole writer of that
settings file, see its ``copilot.settings`` resource); this module only
re-expresses whatever is already persisted as the matching CLI flag, so
every worktree create/resume launch carries the intended model even when
the persisted setting alone would be ignored.

Deliberately reads the plain settings file rather than depending on
``agent-machines`` itself: that keeps this launch-time translation correct
even when ``agent-machines`` isn't installed, and avoids a reverse runtime
dependency between the two plugins.

Not applied to ACP sessions -- see the ``_build_launch_cmd`` call site.
"""

from __future__ import annotations

import json
from pathlib import Path

# Maps each persisted settings.json key to the CLI flag that carries the
# same value explicitly. Order matters: this is also the order flags are
# appended in.
_LAUNCH_PREF_FLAGS: dict[str, str] = {
    "model": "--model",
    "effortLevel": "--reasoning-effort",
    "contextTier": "--context",
}


def _strip_line_comments(raw: str) -> str:
    """Strip ``//`` JSON line comments while preserving ``//`` inside strings.

    Copilot CLI's own settings.json may contain ``//`` comments (JSON-with-
    comments), which a plain ``json.loads`` rejects outright. Mirrors the
    equivalent resolver in ``libs/venue-copilot``.
    """
    lines: list[str] = []
    for line in raw.splitlines():
        in_string = False
        escaped = False
        cut_at: int | None = None
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if in_string and char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if not in_string and char == "/" and index + 1 < len(line):
                if line[index + 1] == "/":
                    cut_at = index
                    break
        if cut_at is not None:
            line = line[:cut_at].rstrip()
        lines.append(line)
    return "\n".join(lines)


def _user_copilot_settings() -> dict:
    """Best-effort read of ``~/.copilot/settings.json``.

    Never raises: a missing, unreadable, or malformed file just means "no
    persisted preference", which the caller treats as "nothing to inject"
    rather than a launch failure. Tolerates ``//`` line comments, which a
    plain ``json.loads`` would otherwise reject wholesale.
    """
    path = Path.home() / ".copilot" / "settings.json"
    try:
        data = json.loads(_strip_line_comments(path.read_text(encoding="utf-8")))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _flag_already_present(passthrough: list[str], flag: str) -> bool:
    """True if ``passthrough`` already carries ``flag`` (bare or ``=``-form)."""
    for arg in passthrough:
        if arg == flag or arg.startswith(flag + "="):
            return True
    return False


def resolve_launch_pref_flags(passthrough: list[str]) -> list[str]:
    """Return the CLI flags needed to carry the persisted model/effort/
    context preference through to this launch.

    ``passthrough`` is every argument assembled into the launch command so
    far (a configured ``launch`` template plus operator ``copilot_args`` plus
    any profile ``copilot_args``) -- an explicit ``--model``/
    ``--reasoning-effort``/``--context`` already present anywhere in it
    always wins over the ambient settings.json default, so this never
    duplicates or overrides one of those, however it was supplied.
    """
    settings = _user_copilot_settings()
    flags: list[str] = []
    for key, flag in _LAUNCH_PREF_FLAGS.items():
        value = settings.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        if _flag_already_present(passthrough, flag):
            continue
        flags.extend([flag, value])
    return flags
