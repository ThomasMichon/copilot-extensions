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

  - token: ABC
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


class BlocklistParseError(Exception):
    """Raised when a ``.identifier-blocklist/`` file exists but fails to
    parse as valid YAML, or can't be read for a reason other than simply
    being absent (permissions, a transient I/O error, etc.).

    This is deliberately NOT swallowed the way a missing/absent file is: a
    typo'd YAML file (or an unreadable one) would otherwise silently drop
    its entire denylist while the sweep still exits successfully, which
    could provision an incomplete CI secret with no visible signal that
    anything went wrong.

    :attr:`partial_entries` carries every entry :func:`sweep` *did*
    successfully parse from every other, unaffected source -- a caller that
    wants "preserve what still works, but still fail loudly" (rather than
    discarding everything) can use it instead of treating this exception as
    all-or-nothing.
    """

    def __init__(self, message: str, *, partial_entries: "list[BlocklistEntry] | None" = None):
        super().__init__(message)
        self.partial_entries: list[BlocklistEntry] = partial_entries or []


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


_KNOWN_ENTRY_FIELDS = frozenset({"token", "kind", "whole_word", "case_sensitive", "reason"})


def _require_bool(raw: dict, key: str, *, context: str) -> bool:
    """Strictly validate a boolean schema field.

    A non-``bool`` value (e.g. the YAML string ``"false"``, or ``1``/``0``)
    must never be silently coerced -- ``bool("false")`` is ``True`` in
    Python, which would silently narrow matching (a mistyped
    ``case_sensitive: "false"`` would be treated as ``case_sensitive:
    true``) while reporting success.
    """
    if key not in raw:
        return False
    value = raw[key]
    if not isinstance(value, bool):
        raise BlocklistParseError(
            f"{context}: '{key}' must be a boolean (true/false), got {value!r}"
        )
    return value


def _compile_internal_token(raw: dict, *, context: str) -> str:
    """Resolve one YAML entry mapping to the internal token representation.

    Returns a plain literal substring, or a ``regex:<pattern>`` token -- the
    same shape :func:`render_ci_format` and every downstream consumer
    already understands. Raises :class:`BlocklistParseError` (naming
    *context*, typically ``<path> (entry #N)``) for a missing/empty
    ``token`` field, an unrecognized ``kind``, an unrecognized field name,
    or a non-boolean ``whole_word``/``case_sensitive`` value -- a typo here
    must never silently fall back to a narrower, wrong interpretation while
    reporting success.
    """
    unknown = set(raw) - _KNOWN_ENTRY_FIELDS
    if unknown:
        plural = "s" if len(unknown) != 1 else ""
        raise BlocklistParseError(
            f"{context}: unknown field{plural} {sorted(unknown)!r} (expected one "
            f"of: {', '.join(sorted(_KNOWN_ENTRY_FIELDS))}) -- check for a typo"
        )
    token = str(raw.get("token", "") or "").strip()
    if not token:
        raise BlocklistParseError(f"{context}: missing required 'token' field")
    if "\n" in token or "\r" in token or ";" in token:
        raise BlocklistParseError(
            f"{context}: 'token' may not contain a newline or semicolon -- "
            "the CI consumer treats both as entry delimiters, so this value "
            "is unrepresentable in that grammar"
        )
    kind = str(raw.get("kind", "literal") or "literal").strip().lower()
    if kind not in ("literal", "regex"):
        raise BlocklistParseError(
            f"{context}: unknown kind '{kind}' (expected 'literal' or 'regex')"
        )
    if kind == "regex":
        # A hand-authored pattern can be wrong in two ways a schema check
        # alone can't catch: it may simply not compile, or it may compile
        # but match the empty string -- which would flag every file in a
        # scan, effectively blocking every push. Catch both here, where the
        # source file and entry index are still available for the message,
        # rather than letting a broken pattern surface downstream as an
        # opaque regex error (or worse, silently over-match).
        try:
            compiled = _re.compile(token)
        except _re.error as exc:
            raise BlocklistParseError(f"{context}: invalid regex '{token}': {exc}") from exc
        if compiled.match(""):
            raise BlocklistParseError(
                f"{context}: regex '{token}' matches the empty string, which "
                "would flag every file -- narrow the pattern"
            )
    whole_word = _require_bool(raw, "whole_word", context=context)
    case_sensitive = _require_bool(raw, "case_sensitive", context=context)

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
    """Parse one ``block-for-<tier>.yaml`` file into entries.

    A missing file (``FileNotFoundError``) is a normal, silent no-op (not
    every repo carries every tier). An entirely empty file (or one holding
    only an empty YAML document / an empty ``entries:`` mapping) is a
    legitimate placeholder with zero entries, also not an error. Anything
    else that doesn't match the documented shape -- a non-mapping/non-list
    top-level document, a misspelled or non-list ``entries`` key, a
    non-mapping list item, or an entry :func:`_compile_internal_token`
    rejects -- raises :class:`BlocklistParseError` instead of silently
    discarding content, same as a YAML syntax error or an OSError reading
    the file. See that class's docstring for why.
    """
    entries: list[BlocklistEntry] = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return entries
    except OSError as exc:
        raise BlocklistParseError(f"{path}: could not read ({exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BlocklistParseError(f"{path}: invalid YAML ({exc})") from exc

    if data is None:
        return entries  # a fully empty file is a legitimate zero-entry placeholder
    if isinstance(data, dict):
        if "entries" not in data:
            if data == {}:
                return entries  # an explicit `{}` placeholder, same as empty
            raise BlocklistParseError(
                f"{path}: missing required top-level 'entries' key "
                "(check for a misspelling)"
            )
        raw_entries = data["entries"]
    elif isinstance(data, list):
        raw_entries = data
    else:
        raise BlocklistParseError(
            f"{path}: expected a YAML mapping with 'entries' or a bare list, "
            f"got {type(data).__name__}"
        )
    if raw_entries is None:
        return entries  # `entries:` with no value is also a zero-entry placeholder
    if not isinstance(raw_entries, list):
        raise BlocklistParseError(
            f"{path}: 'entries' must be a list, got {type(raw_entries).__name__}"
        )

    for idx, raw in enumerate(raw_entries):
        context = f"{path} (entry #{idx + 1})"
        if not isinstance(raw, dict):
            raise BlocklistParseError(f"{context}: expected a mapping, got {type(raw).__name__}")
        token = _compile_internal_token(raw, context=context)
        reason = raw.get("reason")
        reason = str(reason).strip() or None if reason else None
        entries.append(
            BlocklistEntry(token=token, reason=reason, source_repo=source_repo, source_tier=tier)
        )
    return entries


def _dedup_key(token: str) -> str:
    """Case-aware deduplication key for a token.

    Lowercasing every token (as the aggregator used to) breaks a
    ``case_sensitive`` entry: ``ABC`` and ``abc`` compile to distinct
    ``(?-i:...)`` regex tokens that must stay distinct, not collapse into
    one. A literal token is still lowercased (its own matching is always
    case-insensitive, so two differently-cased spellings of the same
    literal genuinely are the same entry); a ``regex:``-prefixed token's
    pattern keeps its exact case -- only the ``regex:`` marker itself is
    normalized, matching how the CI consumer's own loader already
    distinguishes the two kinds.
    """
    if token.lower().startswith("regex:"):
        return "regex:" + token[len("regex:"):]
    return token.lower()


def sweep(target: str | None) -> list[BlocklistEntry]:
    """Aggregate every registered repo's applicable blocklist tiers for *target*.

    *target* names a registered repo; ``None`` resolves to maximum exposure
    (every tier applies) since an unresolvable target is the fail-safe case.
    The target repo itself is excluded from source discovery -- the
    convention is "what every *other* repo forbids", not self-reference,
    and including it would let a repo's own newly-added blocklist file
    immediately flag its own forbidden token inside that very file.

    Returns a deduplicated (see :func:`_dedup_key` -- case-sensitive for a
    ``regex:`` token, case-insensitive for a literal), deterministically
    sorted list. One source repo's blocklist file failing to parse never
    discards every other source's valid entries: every *other* file is
    still parsed and aggregated normally, and only once every source has
    been attempted does :class:`BlocklistParseError` get raised -- carrying
    every successfully-parsed entry in :attr:`BlocklistParseError.
    partial_entries` so a caller can still use them rather than treating
    one broken peer as reason to discard the whole sweep.
    """
    target_entry = repos_mod.find_repo(target) if target else None
    target_rank = resolve_visibility_rank(target_entry)
    tiers = applicable_tiers(target_rank)
    if not tiers:
        return []

    seen: dict[tuple[str, str | None], BlocklistEntry] = {}
    errors: list[str] = []
    for repo_entry in repos_mod.list_repos():
        if target_entry is not None and repo_entry.name == target_entry.name:
            continue
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
            try:
                parsed = parse_blocklist_file(file_path, repo_entry.name, tier)
            except BlocklistParseError as exc:
                errors.append(str(exc))
                continue
            for entry in parsed:
                key = (_dedup_key(entry.token), entry.reason)
                seen.setdefault(key, entry)

    result = sorted(seen.values(), key=lambda e: (_dedup_key(e.token), e.source_repo))
    if errors:
        plural = "s" if len(errors) != 1 else ""
        raise BlocklistParseError(
            f"{len(errors)} blocklist file{plural} failed to parse: " + "; ".join(errors),
            partial_entries=result,
        )
    return result


def _sanitize_ci_text(value: str) -> str:
    """Strip characters the CI-format grammar treats as hard delimiters.

    ``_load_ci_identifiers`` (the consumer, in
    ``tools/check-no-internal-identifiers.py``) splits entries on any
    newline or semicolon with no escape mechanism for either -- so a reason
    (or a literal token) containing one would silently fragment into extra,
    bogus lines. Neither character carries meaning worth preserving in this
    context, so they are simply replaced with a space/comma.
    """
    return value.replace("\r", " ").replace("\n", " ").replace(";", ",")


def _render_token_for_ci(token: str) -> str:
    """Return a CI-format-safe token string.

    The consumer's grammar only has an escape mechanism for a literal ``|``
    inside a ``regex:``-prefixed token (double it to ``||``) -- a *plain*
    literal token has no such escape at all (its separator scan is a bare
    ``str.partition("|")``), so a literal token containing a ``|`` must be
    promoted to an escaped regex token to survive the round trip at all.
    """
    if token.lower().startswith("regex:"):
        pattern = token[len("regex:"):]
        return "regex:" + pattern.replace("|", "||")
    if "|" in token:
        return "regex:" + _re.escape(token).replace("|", "||")
    return token


def render_ci_format(entries: list[BlocklistEntry]) -> str:
    """Render entries as ``token|reason`` lines (CI-secret-ready format).

    Both the token (escaping a literal ``|``, promoting to regex if needed)
    and the reason (stripping hard delimiter characters) are made safe for
    the consumer's line-oriented grammar before emission.
    """
    lines = []
    for e in entries:
        token = _render_token_for_ci(e.token)
        reason = _sanitize_ci_text(e.reason) if e.reason else None
        lines.append(f"{token}|{reason}" if reason else token)
    return "\n".join(lines)
