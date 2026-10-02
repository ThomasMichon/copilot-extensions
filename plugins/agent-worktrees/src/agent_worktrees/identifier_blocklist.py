"""Pluggable identifier-blocklist sweep.

Discovers and aggregates ``block-for-<tier>.yaml`` denylists from every
locally registered repo (``~/.agent-worktrees/repos.yaml``), scoped to a
*target* repo's own audience-exposure tier (``RepoEntry.visibility``).

## The convention

Any registered repo may carry a ``.identifier-blocklist/`` directory at its
anchor root, checked into the repo itself (not machine-local -- so it
travels with a fork/clone, like the rest of that repo's own capability).
Inside it, one file per exposure tier it wants to protect against:

- ``block-for-internal.yaml`` -- terms that must never appear in a repo
  whose own ``visibility`` is ``internal`` or more exposed (``public`` too).
- ``block-for-public.yaml`` -- terms that must never appear in a repo whose
  own ``visibility`` is ``public``.

There is deliberately no ``block-for-private.yaml``: nothing is more exposed
than ``private``, so such a file could never apply to any *other* repo, and
a repo never needs to protect itself from its own content.

## File schema

```yaml
entries:
  - token: legacy-system
    reason: Internal org/repo name -- use a generic product placeholder

  - token: '\bSPO\b'
    kind: regex
    reason: Standalone internal abbreviation -- use a generic service placeholder

  - token: CAR
    whole_word: true
    case_sensitive: true
    reason: Case-sensitive acronym for a private repo -- refer to it generically
```

Each entry under the top-level ``entries:`` list is a mapping:

- ``token`` (required) -- the literal substring, or (when ``kind: regex``) a
  Python regular-expression fragment.
- ``kind`` -- ``literal`` (default) or ``regex``. A ``literal`` token matches
  as a plain, case-insensitive substring unless ``whole_word``/
  ``case_sensitive`` below promote it to a regex internally.
- ``whole_word`` -- wrap the token in ``\b...\b`` boundaries (auto-escaping a
  literal token first). Use this instead of hand-writing a regex for the
  common "don't flag this as a substring of another word" case.
- ``case_sensitive`` -- match this token's exact case only (scoped with an
  inline ``(?-i:...)`` group so the rest of the pattern, and every other
  entry, stays case-insensitive).
- ``reason`` (optional) -- explains why the token is forbidden and names the
  generic replacement to use instead.

Internally, each parsed entry still resolves to the same ``token``/
``regex:<pattern>`` representation
``tools/check-no-internal-identifiers.py``'s CI-secret source has always
used, via :func:`render_ci_format` -- so a consumer reading the aggregated
sweep output needs no format awareness of this module's own YAML source.

## Why this lives in agent-worktrees, not a single consuming repo

Any repo that registers an audience-exposure ``visibility`` and wants its
pre-push/CI guard sourced from a live, cross-repo sweep rather than a
hand-maintained copy can call :func:`sweep` (or the ``identifiers sweep``
CLI) -- this is the centralized, pluggable half of the mechanism; a
consuming repo's own guard script (or this plugin's own pre-push hook) is
the enforcement half.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import repos as repos_mod

BLOCKLIST_DIR_NAME = ".identifier-blocklist"
# Blocklist tiers, keyed by the exposure level at which their terms become
# forbidden, mapped to the by-convention filename each one lives at.
TIER_FILES: dict[str, str] = {
    "internal": "block-for-internal.yaml",
    "public": "block-for-public.yaml",
}


@dataclass(frozen=True)
class BlocklistEntry:
    """One discovered forbidden-identifier entry."""

    token: str  # literal substring, or a "regex:<pattern>" token
    reason: str | None
    source_repo: str
    source_tier: str


def resolve_visibility_rank(entry: "repos_mod.RepoEntry | None") -> int:
    """Fail-safe exposure rank for *entry* -- unset/unknown => most exposed.

    An operator who hasn't yet classified a repo's ``visibility`` gets
    *more* enforcement swept in, not less, until they do.
    """
    if entry is None:
        return repos_mod.VISIBILITY_RANK["public"]
    vis = entry.visibility or "public"
    return repos_mod.VISIBILITY_RANK.get(vis, repos_mod.VISIBILITY_RANK["public"])


def applicable_tiers(target_rank: int) -> list[str]:
    """Which blocklist tiers apply to a target at *target_rank* exposure.

    A tier named ``T`` applies whenever the target's exposure is at or above
    ``T``'s own rank -- e.g. ``internal`` applies to both internal and
    public targets, ``public`` only to public targets.
    """
    return [
        tier for tier in TIER_FILES
        if target_rank >= repos_mod.VISIBILITY_RANK[tier]
    ]


def _compile_internal_token(raw: dict) -> str:
    """Resolve one YAML entry mapping to the internal token representation.

    Returns a plain literal substring, or a ``regex:<pattern>`` token -- the
    same shape :func:`render_ci_format` and every downstream consumer
    already understands. Returns ``""`` for a mapping with no usable token.
    """
    token = str(raw.get("token", "") or "").strip()
    if not token:
        return ""
    kind = str(raw.get("kind", "literal") or "literal").strip().lower()
    if kind not in ("literal", "regex"):
        kind = "literal"
    whole_word = bool(raw.get("whole_word", False))
    case_sensitive = bool(raw.get("case_sensitive", False))

    if kind == "literal" and not whole_word and not case_sensitive:
        return token

    # Anything needing regex semantics (explicit `regex` kind, `whole_word`,
    # or a case-sensitive literal) gets promoted to a regex token.
    pattern = token if kind == "regex" else _re.escape(token)
    if whole_word:
        pattern = rf"\b{pattern}\b"
    if case_sensitive:
        pattern = f"(?-i:{pattern})"
    return f"regex:{pattern}"


def parse_blocklist_file(path: Path, source_repo: str, tier: str) -> list[BlocklistEntry]:
    """Parse one ``block-for-<tier>.yaml`` file into entries."""
    entries: list[BlocklistEntry] = []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return entries

    if isinstance(data, dict):
        raw_entries = data.get("entries", [])
    elif isinstance(data, list):
        raw_entries = data
    else:
        raw_entries = []
    if not isinstance(raw_entries, list):
        return entries

    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        token = _compile_internal_token(raw)
        if not token:
            continue
        reason = raw.get("reason")
        reason = str(reason).strip() or None if reason else None
        entries.append(
            BlocklistEntry(token=token, reason=reason, source_repo=source_repo, source_tier=tier)
        )
    return entries


def sweep(target: str | None) -> list[BlocklistEntry]:
    """Aggregate every registered repo's applicable blocklist tiers for *target*.

    *target* names a registered repo; ``None`` resolves to maximum exposure
    (every tier applies) since an unresolvable target is the fail-safe case.
    Returns a deduplicated (by lowercased token + reason), deterministically
    sorted list.
    """
    target_entry = repos_mod.find_repo(target) if target else None
    target_rank = resolve_visibility_rank(target_entry)
    tiers = applicable_tiers(target_rank)
    if not tiers:
        return []

    seen: dict[tuple[str, str | None], BlocklistEntry] = {}
    for repo_entry in repos_mod.list_repos():
        local = repo_entry.local_path()
        if not local:
            continue
        root = Path(local)
        if not root.is_dir():
            continue
        blocklist_dir = root / BLOCKLIST_DIR_NAME
        if not blocklist_dir.is_dir():
            continue
        for tier in tiers:
            file_path = blocklist_dir / TIER_FILES[tier]
            if not file_path.is_file():
                continue
            for entry in parse_blocklist_file(file_path, repo_entry.name, tier):
                key = (entry.token.lower(), entry.reason)
                seen.setdefault(key, entry)

    return sorted(seen.values(), key=lambda e: (e.token.lower(), e.source_repo))


def render_ci_format(entries: list[BlocklistEntry]) -> str:
    """Render entries as ``token|reason`` lines (CI-secret-ready format)."""
    lines = []
    for e in entries:
        lines.append(f"{e.token}|{e.reason}" if e.reason else e.token)
    return "\n".join(lines)
