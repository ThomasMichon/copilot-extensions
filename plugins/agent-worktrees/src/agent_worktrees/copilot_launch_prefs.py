"""Re-express the persisted Copilot model/effort/context preference as
explicit CLI flags at launch time.

Copilot CLI has been observed to ignore its own persisted
``~/.copilot/settings.json`` values (``model`` / ``effortLevel`` /
``contextTier``) at startup, honoring only an explicit CLI flag or a
mid-session ``/model`` change -- the gap the facility's 2026-08-31
model-policy session recorded against the Intelligence Dampener's dispatch
launch path. ``agent-machines`` remains the single source of truth for the
preference (it is the sole writer of that settings file, see its
``copilot.settings`` resource); this module only re-expresses whatever it
already wrote as the matching CLI flag, so every worktree create/resume
launch carries the intended model even when the persisted setting alone
would be ignored.

Deliberately reads the plain settings file rather than depending on
``agent-machines`` itself: that keeps this launch-time translation correct
even when ``agent-machines`` isn't installed, and avoids a reverse runtime
dependency between the two plugins.
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


def _user_copilot_settings() -> dict:
    """Best-effort read of ``~/.copilot/settings.json``.

    Never raises: a missing, unreadable, or malformed file just means "no
    persisted preference", which the caller treats as "nothing to inject"
    rather than a launch failure.
    """
    path = Path.home() / ".copilot" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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

    ``passthrough`` is every Copilot CLI argument the caller (operator
    ``copilot_args`` plus any profile ``copilot_args``) already supplied --
    an explicit ``--model``/``--reasoning-effort``/``--context`` the caller
    passed always wins over the ambient settings.json default, so this
    never duplicates or overrides one of those.
    """
    settings = _user_copilot_settings()
    flags: list[str] = []
    for key, flag in _LAUNCH_PREF_FLAGS.items():
        value = settings.get(key)
        if not isinstance(value, str) or not value:
            continue
        if _flag_already_present(passthrough, flag):
            continue
        flags.extend([flag, value])
    return flags
