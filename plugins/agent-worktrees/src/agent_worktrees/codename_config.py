"""Config for the per-repo codename generator (effort:
``pr-attribution-codenames``, issue #2838).

Kept as its own small module rather than growing ``config.py`` directly --
that module is already near its module-size ceiling (see
``tools/module-size-baseline.json``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CodenameConfig:
    """Per-repo codename-generation settings.

    ``wordlist_path`` is empty by default -- the built-in,
    organization-neutral generator (:mod:`agent_worktrees.codename`) is
    used. Setting it points at a JSON or YAML file (see
    :func:`agent_worktrees.codename.load_wordlist`) declaring the
    adopter's own vocabulary, e.g. a private control repo's own themed
    word list, without that vocabulary ever living in this public plugin.
    Purely declarative data -- no code runs, no subprocess, no timeout.
    """

    wordlist_path: str = ""
    # Whether ``codename.wordlist_path`` was an explicit key in the raw
    # ``codename:`` block, versus omitted entirely -- distinct from
    # ``wordlist_path``'s own truthiness. Two failure modes make the
    # truthiness check alone unsafe for classification purposes (effort
    # codename-attribution-by-default): (1) a present-but-malformed
    # non-string value normalizes to the same empty string as a genuinely
    # absent key (see below), and (2) a present-but-unloadable/missing
    # wordlist *file* still resolves to the built-in `Wordlist` object
    # downstream (`load_wordlist_or_default`'s own fail-soft), which is
    # indistinguishable from "no custom path configured" if you only look
    # at the resolved `Wordlist`. This flag lets a caller ask "did this
    # repo configure a custom wordlist at all?" using only the raw key's
    # *presence*, independent of whether its value is valid or its file
    # loads.
    wordlist_path_configured: bool = False


def parse_codename(raw: Any) -> CodenameConfig:
    """Parse the optional ``codename:`` block of a repo config.

    Unknown or missing values fall back to :class:`CodenameConfig` defaults
    (built-in generator). A non-string ``wordlist_path`` falls back to the
    empty default too -- this parser also serves the plain in-repo and
    machine config paths, which don't go through ``config_dropins``'s
    schema validation first, so ``str(None)`` == ``"None"`` (a truthy,
    non-empty "path") must never reach here unguarded.
    """
    if not isinstance(raw, dict):
        return CodenameConfig()
    wordlist_path = raw.get("wordlist_path", "")
    wordlist_path_configured = "wordlist_path" in raw
    if not isinstance(wordlist_path, str):
        wordlist_path = ""
    return CodenameConfig(
        wordlist_path=wordlist_path.strip(),
        wordlist_path_configured=wordlist_path_configured,
    )
