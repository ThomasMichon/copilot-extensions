"""Public-safe codename generation for worktrees (effort:
``pr-attribution-codenames``, issue #2838).

A codename is a short, branch/filename-safe handle assigned to a worktree so
a public PR marker (or, historically, a leaked branch name) can carry a
lookup key without decoding any machine/worktree/session/timestamp
information. Two sources of a handle:

- **Built-in generator** -- a small, organization-neutral word list shipped
  with this plugin. Deliberately whimsical but *not* themed after any
  product, franchise, or brand: this is a general-purpose public plugin
  (see CONTRIBUTING.md's contribution boundary), so the *default* vocabulary
  stays neutral. A control repo wanting its own flavor uses a wordlist file
  (below) instead.
- **External wordlist file** -- a JSON or YAML file (adopter-configured by
  path) declaring the adopter's own noun/adjective vocabulary and, if
  wanted, an explicit list of *permitted* adjective-noun pairings (to avoid
  combinations the adopter doesn't want). This is intentionally **purely
  declarative data, never executable code** -- earlier design drafts of
  this feature used an adopter-configured shell hook instead, which turned
  out to require extensive subprocess-safety hardening (process-tree
  cleanup, timeout bounding, output-size bounding, encoding handling) for a
  problem that is really just "read some words from a file." A static data
  file has none of that surface: no timeout, no process to kill, no
  encoding-from-a-subprocess concerns, no zombie/leak risk.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

# Deliberately generic and whimsical -- small mechanical/workshop objects and
# their moods, not tied to any product, franchise, or brand. Kept short so
# generated handles stay compact; extend either list to grow variety.
CODENAME_NOUNS: tuple[str, ...] = (
    "gizmo", "widget", "sprocket", "gadget", "contraption", "doohickey",
    "cog", "pulley", "lever", "hinge", "bolt", "rivet", "spanner", "wrench",
    "bracket", "bearing", "flywheel", "valve", "gauge", "dial", "switch",
    "circuit", "capacitor", "resistor", "beacon", "compass", "lantern",
    "satchel", "toolbox", "ledger", "blueprint", "workbench", "crate",
)
CODENAME_ADJECTIVES: tuple[str, ...] = (
    "rusty", "humming", "lopsided", "tinkling", "restless", "squeaky",
    "wobbly", "gleaming", "tarnished", "nimble", "stubborn", "tidy",
    "patient", "curious", "drowsy", "brisk", "quiet", "sturdy", "faded",
    "polished", "crooked", "steady", "eager", "placid",
)

#: Branch/filename-safe handle: one or more lowercase-alnum segments joined
#: by single hyphens, no leading/trailing hyphen, no double hyphen. ``\Z``
#: (not ``$``) so a trailing newline can never sneak a match past the end of
#: the intended value -- ``$`` matches immediately before a final ``\n`` too.
HANDLE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")

#: A single word (noun or adjective) contributed by a wordlist file: no
#: hyphens of its own (the generator joins words with hyphens itself), just
#: lowercase alnum -- so a malicious/malformed word can't inject an
#: unexpected extra hyphen segment or any other character into the final
#: handle.
_WORD_RE = re.compile(r"^[a-z0-9]+\Z")

#: A hard ceiling on any handle. Long enough for a 3-word handle plus a
#: suffix, short enough to never strain a branch name.
MAX_HANDLE_LENGTH = 64

#: A hard ceiling on any single word from a wordlist file.
MAX_WORD_LENGTH = 24


def is_valid_handle(value: str) -> bool:
    """Whether ``value`` is a well-formed handle: lowercase, hyphen-joined,
    alnum segments only, within :data:`MAX_HANDLE_LENGTH`.
    """
    if not value or len(value) > MAX_HANDLE_LENGTH:
        return False
    return bool(HANDLE_RE.match(value))


def _is_valid_word(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_WORD_LENGTH
        and bool(_WORD_RE.match(value))
    )


@dataclass(frozen=True)
class Wordlist:
    """A codename vocabulary: standalone nouns (for a 1-word handle) and
    either an explicit set of permitted adjective-noun ``pairs`` (for a
    curated 2-word handle) or a plain ``adjectives`` list (crossed with
    every noun) when the adopter doesn't need to restrict combinations.

    ``pairs`` takes precedence when non-empty: it is the mechanism for an
    adopter who wants to declare *permitted relationships* rather than
    "any adjective with any noun" (e.g. to avoid an unwanted combination).
    """

    nouns: tuple[str, ...] = CODENAME_NOUNS
    adjectives: tuple[str, ...] = CODENAME_ADJECTIVES
    pairs: tuple[tuple[str, str], ...] = ()

    def two_word_choices(self) -> tuple[tuple[str, str], ...]:
        """The (adjective, noun) combinations available for a 2-word
        handle: ``pairs`` verbatim if declared, otherwise every
        adjective crossed with every noun."""
        if self.pairs:
            return self.pairs
        return tuple(
            (adjective, noun)
            for adjective in self.adjectives
            for noun in self.nouns
        )


#: The built-in, organization-neutral wordlist -- used whenever a repo
#: doesn't configure its own ``wordlist_path``.
DEFAULT_WORDLIST = Wordlist()


class WordlistError(ValueError):
    """Raised by :func:`load_wordlist` for a missing, malformed, or invalid
    wordlist file. Callers should treat this the same as "no wordlist
    configured" (fall back to :data:`DEFAULT_WORDLIST`) rather than crash
    worktree creation over an adopter's data-file typo -- see
    :func:`load_wordlist_or_default`.
    """


def load_wordlist(path: str | Path) -> Wordlist:
    """Load and validate a :class:`Wordlist` from a JSON or YAML file.

    Expected shape (YAML shown; JSON is the same structurally)::

        nouns: [gizmo, widget, ...]        # required, non-empty
        adjectives: [rusty, humming, ...]  # optional
        pairs:                             # optional; overrides the
          - [rusty, gizmo]                 # adjectives cross-product when
          - [humming, sprocket]            # given and non-empty

    Raises :class:`WordlistError` for anything that doesn't parse into
    exactly that shape: a missing file, invalid UTF-8, malformed JSON/YAML,
    a missing or empty ``nouns``, a non-list value for any key (including
    an explicit ``null``, distinguished from the key being absent
    entirely), a non-string item, an item that isn't a lowercase-alnum word
    (see :data:`MAX_WORD_LENGTH`), or a ``pairs`` entry that isn't a
    2-item list/tuple.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WordlistError(f"cannot read wordlist file {file_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise WordlistError(f"wordlist file {file_path} is not valid UTF-8: {exc}") from exc
    try:
        if file_path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise WordlistError(f"cannot parse wordlist file {file_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WordlistError(f"{file_path}: top level must be a mapping")

    def _word_list(key: str, *, required: bool) -> tuple[str, ...]:
        if key not in data:
            if required:
                raise WordlistError(f"{file_path}: '{key}' is required")
            return ()
        value = data[key]
        if not isinstance(value, list) or not value:
            raise WordlistError(f"{file_path}: '{key}' must be a non-empty list")
        for item in value:
            if not _is_valid_word(item):
                raise WordlistError(
                    f"{file_path}: '{key}' entry {item!r} must be a "
                    f"lowercase alphanumeric word (max {MAX_WORD_LENGTH} chars)"
                )
        return tuple(value)

    nouns = _word_list("nouns", required=True)
    adjectives = _word_list("adjectives", required=False) or CODENAME_ADJECTIVES

    pairs: tuple[tuple[str, str], ...] = ()
    if "pairs" in data:
        raw_pairs = data["pairs"]
        if not isinstance(raw_pairs, list) or not raw_pairs:
            raise WordlistError(f"{file_path}: 'pairs' must be a non-empty list")
        validated_pairs = []
        for entry in raw_pairs:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise WordlistError(
                    f"{file_path}: 'pairs' entry {entry!r} must be a "
                    "2-item [adjective, noun] list"
                )
            adjective, noun = entry
            if not _is_valid_word(adjective) or not _is_valid_word(noun):
                raise WordlistError(
                    f"{file_path}: 'pairs' entry {entry!r} must contain "
                    f"lowercase alphanumeric words (max {MAX_WORD_LENGTH} chars)"
                )
            validated_pairs.append((adjective, noun))
        pairs = tuple(validated_pairs)

    return Wordlist(nouns=nouns, adjectives=adjectives, pairs=pairs)


def load_wordlist_or_default(path: str) -> Wordlist:
    """:func:`load_wordlist` with a fail-soft fallback: an empty ``path``
    or any :class:`WordlistError` (missing file, malformed data) returns
    :data:`DEFAULT_WORDLIST` rather than raising -- a wordlist file typo
    should never block worktree creation.
    """
    if not path:
        return DEFAULT_WORDLIST
    try:
        return load_wordlist(path)
    except WordlistError:
        return DEFAULT_WORDLIST


def generate_handle(
    *,
    words: int = 2,
    rng: random.Random | None = None,
    wordlist: Wordlist = DEFAULT_WORDLIST,
) -> str:
    """Generate one handle from ``wordlist`` (the built-in neutral
    vocabulary by default).

    ``words`` is 1 (a lone noun) or 2 (adjective-noun, from
    ``wordlist.two_word_choices()``); any other value is treated as 2.
    Uses ``rng`` if given (for deterministic tests), otherwise the
    module-level random source.
    """
    r = rng or random
    if words == 1:
        return r.choice(wordlist.nouns)
    adjective, noun = r.choice(wordlist.two_word_choices())
    return f"{adjective}-{noun}"


def assign_codename(
    existing: Iterable[str],
    *,
    words: int = 2,
    max_attempts: int = 50,
    rng: random.Random | None = None,
    wordlist: Wordlist = DEFAULT_WORDLIST,
) -> str:
    """Assign a codename not already present in ``existing``.

    Retries up to ``max_attempts`` times to avoid a collision (a local
    collision check only -- cross-machine uniqueness is a separate,
    shared-registry concern, not this function's job).
    """
    taken = set(existing)
    for _ in range(max_attempts):
        candidate = generate_handle(words=words, rng=rng, wordlist=wordlist)
        if candidate not in taken:
            return candidate
    raise ValueError(
        f"could not assign a unique codename after {max_attempts} attempts"
    )
