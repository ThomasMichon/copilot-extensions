"""Optional source-worktree attribution markers for PR bodies/comments.

When a closed-circuit repo opts in with ``pr.source_attribution: true``, a PR
opened by agent-worktrees carries an initial hidden HTML-comment marker naming
the **source worktree** (+ machine / session / head SHA). Later pushed heads are
published as dedicated marker comments so mutable metadata never replaces the
authored PR description. Consumers use the newest marker across both surfaces.
The feature is off by default because hidden PR metadata is still public and raw
machine, worktree, and session identifiers are inappropriate for public repos.

The marker is a single HTML comment, invisible in rendered Markdown:

    <!-- agent-worktrees:source worktree=<id> machine=<m> session=<sid> head=<sha> -->

A third mode, ``pr.source_attribution: codename`` (effort
``pr-attribution-codenames`` Phase 4), is for a public repo that still wants
author-side traceability: it emits :func:`build_codename_marker` instead --
**only** the worktree's assigned codename, no machine/worktree-id/session/
head. The codename decodes to nothing on its own; the author resolves it back
to a worktree locally (``resolve --codename``) or, on another machine, via an
automated cross-machine SSH scan (Phase 3, ``codename_reverse_lookup.py``) --
a match on a different machine reports it (fails closed) rather than
attempting a remote launch.
"""

from __future__ import annotations

import re
import string
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import SourceAttribution

_MARKER_RE = re.compile(
    r"<!--\s*agent-worktrees:source\s+(?P<fields>.*?)\s*-->",
    re.DOTALL,
)
_FIELD_RE = re.compile(r"(\w+)=(\S+)")


def build_marker(
    worktree_id: str,
    *,
    machine: str = "",
    session: str = "",
    head: str = "",
) -> str:
    """Build the hidden source-attribution comment for a PR body."""
    parts = [f"worktree={worktree_id}"]
    if machine:
        parts.append(f"machine={machine}")
    if session:
        parts.append(f"session={session}")
    if head:
        parts.append(f"head={head}")
    return f"<!-- agent-worktrees:source {' '.join(parts)} -->"


def build_codename_marker(codename: str) -> str:
    """Build the codename-only source-attribution comment (``codename``
    mode). Carries **no** machine, worktree id, session id, or timestamp --
    only the assigned codename, which decodes to nothing without local
    access to the authoring machine's own tracking store (or a manual SSH
    session onto it -- there is no automated cross-machine lookup yet).
    """
    return f"<!-- agent-worktrees:source codename={codename} -->"


def may_publish_codename(
    *, codename_source: str | None, source_attribution_configured: bool,
) -> bool:
    """Decide whether an ALREADY-ASSIGNED codename is safe to publish, given
    its record's own persisted provenance (codename-attribution-by-default,
    rounds 8/14/17/22/32/34).

    Both codename-marker publish call sites (``_open_via_provider``'s
    initial-PR-body branch and ``refresh_source_attribution``'s
    managed-comment branch, used on every later push) must apply this
    IDENTICAL check before treating an already-assigned codename as safe to
    publish, factored into this one shared helper so the two paths cannot
    drift out of sync (round-17 finding: only one of them originally
    checked provenance at all).

    Callers already gate on ``attribution == "codename"`` before invoking
    this helper -- it decides ONLY the two remaining codename-mode cases:

    * ``codename_source == "built-in"`` always publishes -- always safe,
      whether the ``codename`` default is implicit or explicit
      (``source_attribution_configured`` doesn't matter here).
    * ``codename_source == "custom"`` publishes ONLY when
      ``source_attribution_configured`` is ``True`` -- an operator who has
      explicitly opted in has reviewed this repo's current custom
      vocabulary; the bare implicit default never does (the exact
      silent-leak scenario this whole effort exists to prevent).
    * Anything else -- a missing/unrecognized/malformed ``codename_source``
      (a legacy record predating this field, or hand-edited YAML) -- NEVER
      publishes, explicit opt-in or not (round-32 finding, narrows the
      original round-14/22 rule: explicit opt-in only ever bypasses the
      built-in/custom ALLOCATION distinction, never provenance itself).
      Checked as ``codename_source == "built-in"``, never the inverted
      ``codename_source != "custom"`` shape (round-12 finding): an
      unrecognized stored value must never be silently treated as safe.

    This helper does NOT decide whether ``attribution`` itself resolves to
    ``"codename"``, nor does it handle the independent raw-marker
    (``source_attribution: True``) path, which never consults
    ``codename_source`` and is unaffected by this function entirely (round-34
    finding: scoped to codename markers only).
    """
    if codename_source == "built-in":
        return True
    if codename_source == "custom" and source_attribution_configured:
        return True
    return False


def append_marker(body: str, marker: str) -> str:
    """Append *marker* to a PR *body*, replacing any existing source marker."""
    stripped = _MARKER_RE.sub("", body or "").rstrip()
    if stripped:
        return f"{stripped}\n\n{marker}\n"
    return f"{marker}\n"


def strip_marker(body: str) -> str:
    """Return authored PR content with managed source markers removed."""
    return _MARKER_RE.sub("", body or "").rstrip()


def parse_marker(body: str) -> dict[str, str] | None:
    """Extract the source-attribution fields from a PR *body* (or None)."""
    m = _MARKER_RE.search(body or "")
    if not m:
        return None
    return dict(_FIELD_RE.findall(m.group("fields")))


# ── Branch-name leak class (effort ``pr-attribution-codenames`` Phase 5) ────
#
# The hidden PR-body marker above is not the only way a private identifier can
# reach a public repo: the PR HEAD's *branch name itself* is public, and one
# has been observed published as the literal ``worktree/<id>`` name -- the raw
# worktree id (which embeds the authoring machine and a creation timestamp)
# landing directly in a public branch name. Neither ``head_scheme`` default
# publishes that name on its own (see the effort README's Context section);
# the leak comes from an override -- an explicit ``--branch``, an
# existing-PR-reuse, or a ``head_pattern`` containing ``{machine}``/
# ``{worktree_id}`` -- so the fix validates the *effective* resolved head at
# the publish boundary, not just the scheme default.

# Matches a bare ``{machine}``/``{worktree_id}`` token AND any valid
# ``str.format`` conversion/format-spec variant of it (e.g. ``{machine!s}``,
# ``{machine:>10}``, ``{machine:{width}}`` with a NESTED replacement field)
# -- ``pr_head_name`` renders ``head_pattern`` with ``str.format(**tokens)``,
# which accepts all of these and substitutes the SAME underlying value.
# Field names are extracted with :class:`string.Formatter` (never a regex)
# because ``str.format``'s mini-language allows nested replacement fields
# inside a format spec (``{machine:{width}}``) that a regex cannot reliably
# recognize; ``Formatter.parse`` is the same parser ``str.format`` itself
# uses, so it can never miss (or misidentify) a field ``str.format`` would
# actually substitute. Used by ``validate_effective_head``'s own defensive
# "unresolved marker" check against an ALREADY-RESOLVED head string (any
# caller's chosen branch name, not specifically a rendered ``head_pattern``)
# -- a literal ``{worktree_id}`` surviving there is suspicious regardless of
# whether that token is part of ``pr_head_name``'s actual rendering contract.
_UNRESOLVED_TOKEN_NAMES = frozenset({"machine", "worktree_id"})

# The static config-only audit (``head_pattern_leak_risk``) inspects a
# *configured* ``head_pattern`` string, not a resolved head -- so it must
# only flag tokens ``pr_head_name`` can ACTUALLY substitute.
# ``pr_head_name``'s token dict is exactly ``prefix``/``slug``/``suffix``/
# ``username``/``machine`` -- it has no ``worktree_id`` key, so a pattern
# containing ``{worktree_id}`` raises ``KeyError`` inside ``str.format`` and
# is caught, falling back to the safe legacy default pattern; that literal
# text never reaches a published branch. Flagging it as risky here would be
# a false positive the audit cannot actually observe at ``create-pr`` time.
_HEAD_PATTERN_RISKY_TOKEN_NAMES = frozenset({"machine"})


def _referenced_field_names(text: str) -> list[str]:
    """Field names ``str.format`` would substitute from *text*, in order.

    Uses :class:`string.Formatter` -- the same parser ``str.format`` itself
    uses -- rather than a regex, so nested replacement fields are still
    recognized. ``Formatter.parse`` itself only returns TOP-LEVEL field
    names -- a field referenced inside another field's ``format_spec``
    (e.g. ``{slug:{machine}}``, where ``machine`` is nested inside
    ``slug``'s spec) comes back embedded in that spec as literal text, not
    as its own tuple entry. Recurse into every ``format_spec`` so a field
    nested at any depth is still found. Malformed ``str.format`` syntax
    (unbalanced braces) is treated as containing no recognized fields at
    that point -- ``str.format`` itself would raise on it too, so it can
    never reach a published branch either.
    """
    names: list[str] = []

    def _collect(fragment: str) -> None:
        try:
            parsed = list(string.Formatter().parse(fragment or ""))
        except ValueError:
            return
        for _, field_name, format_spec, _ in parsed:
            if field_name:
                names.append(field_name)
            if format_spec and "{" in format_spec:
                _collect(format_spec)

    _collect(text)
    return names


class BranchLeakError(ValueError):
    """An effective PR head branch would leak a private identifier.

    Raised by :func:`validate_effective_head`. The caller must treat this as
    a hard, publish-blocking error -- never downgrade it to a warning that
    lets the push proceed.
    """


def validate_effective_head(
    head: str,
    *,
    worktree_id: str,
    machine: str | tuple[str, ...],
    source_attribution: "SourceAttribution",
) -> None:
    """Block an effective PR head that would leak a private identifier.

    ``head`` is the fully-resolved branch name about to be published --
    however it was chosen (an explicit ``--branch``, a reused existing-PR
    branch, or a rendered ``head_pattern`` template). When
    *source_attribution* is not exactly ``True`` (i.e. it is ``False`` or
    ``"codename"``), ``head`` must not contain the raw *worktree_id*, any of
    the given *machine* name(s) (a single string, or a tuple to cover both
    the *current* config machine and a worktree's originally-*recorded*
    machine -- a renamed/migrated machine can otherwise leave an old
    identifying branch name unchecked against the live config alone), or an
    unresolved ``{machine}``/``{worktree_id}`` template marker (a defensive
    check: neither token should ever survive unsubstituted into a published
    ref, but a config bug must not silently leak one). The *worktree_id* and
    *machine* containment checks are case-insensitive (case-folded), since a
    branch or hostname is commonly re-cased somewhere along the path and the
    identifier still leaks either way. Raises :class:`BranchLeakError` naming
    the offending reason(s); callers must surface this as a hard error and
    refuse to push, never warn-and-continue.

    A repo that has opted into the full raw marker
    (``source_attribution: true``) already accepts machine/worktree/session
    identifiers reaching the PR, so this check is a no-op for it -- it exists
    to protect repos that have NOT opted in.
    """
    if source_attribution is True or not head:
        return
    head_folded = head.casefold()
    machines = (machine,) if isinstance(machine, str) else tuple(machine)
    reasons: list[str] = []
    if worktree_id and worktree_id.casefold() in head_folded:
        reasons.append(f"raw worktree id {worktree_id!r}")
    for candidate in dict.fromkeys(m for m in machines if m):
        if candidate.casefold() in head_folded:
            reasons.append(f"machine name {candidate!r}")
    unresolved = [
        tok for tok in dict.fromkeys(_referenced_field_names(head))
        if tok in _UNRESOLVED_TOKEN_NAMES
    ]
    if unresolved:
        reasons.append(
            "unresolved template marker "
            + ", ".join(f"{{{tok}}}" for tok in unresolved)
        )
    if reasons:
        raise BranchLeakError(
            f"PR head {head!r} would leak " + "; ".join(reasons) +
            f" to a public branch name while source_attribution is "
            f"{source_attribution!r}. Set pr.source_attribution: true if "
            f"this repo accepts that exposure, or choose a --branch/"
            f"head_pattern that does not embed a private identifier."
        )


def head_pattern_leak_risk(head_pattern: str) -> list[str]:
    """Static, config-only risk check for a repo's configured ``head_pattern``.

    Used by the migration audit (Phase 5) to flag repos whose
    ``pr.head_pattern`` embeds ``{machine}`` (in any ``str.format``
    conversion/spec variant, including a nested replacement field like
    ``{machine:{width}}``) while ``pr.source_attribution`` isn't ``true`` --
    a risk that is visible from config alone, without needing a live
    ``create-pr`` run. Deliberately does NOT flag ``{worktree_id}``: it is
    not part of ``pr_head_name``'s actual rendering contract (only
    ``prefix``/``slug``/``suffix``/``username``/``machine`` are), so a
    pattern containing it raises inside ``str.format`` and falls back to the
    safe default rather than ever publishing that literal text. Returns the
    list of risky tokens found (empty when the pattern is safe); does not
    itself know the repo's ``source_attribution`` setting -- callers pair
    this with that check (see ``audit_source_attribution_risk``).
    """
    return [
        tok for tok in dict.fromkeys(_referenced_field_names(head_pattern))
        if tok in _HEAD_PATTERN_RISKY_TOKEN_NAMES
    ]


def audit_source_attribution_risk(
    *,
    source_attribution: object,
    head_pattern: str = "",
) -> list[str]:
    """Flag a repo PR config combination that risks the branch-name leak class.

    Migration-audit helper (Phase 5): a repo is at risk when
    ``source_attribution`` is not exactly ``True`` (``False`` **or absent** --
    the key defaults to ``False``, so a config that omits it entirely is
    just as much at risk as one that sets it explicitly) *and* its
    ``head_pattern`` embeds a ``{machine}`` token (in any ``str.format``
    conversion/spec variant) that could carry a private identifier into a
    published branch name -- see :func:`head_pattern_leak_risk` for why
    ``{worktree_id}`` is deliberately excluded. Returns a list of
    human-readable findings (empty when the config is not at risk).
    """
    if source_attribution is True:
        return []
    risky_tokens = head_pattern_leak_risk(head_pattern)
    if not risky_tokens:
        return []
    if source_attribution is None:
        attribution_desc = "absent (defaults to false)"
        remedy = (
            "migrate to source_attribution: true (if this repo accepts "
            "full exposure) or codename (public-safe body marker), and "
            "either way drop {tok} from head_pattern"
        )
    elif source_attribution == "codename":
        # Already in codename mode -- telling this repo to "migrate to
        # codename" is a no-op that leaves the risky token in place.
        # codename mode is a body-marker concern only; it does nothing to
        # protect a branch NAME, so the only real remedies are accepting
        # full exposure (true) or removing the token from head_pattern.
        attribution_desc = "'codename'"
        remedy = (
            "codename mode only protects the PR-body marker, not the "
            "branch name itself -- set source_attribution: true if this "
            "repo accepts full exposure, or drop {tok} from head_pattern"
        )
    else:
        attribution_desc = f"{source_attribution!r}"
        remedy = (
            "migrate to source_attribution: true or codename, or drop "
            "{tok} from head_pattern"
        )
    return [
        f"pr.head_pattern {head_pattern!r} embeds {{{tok}}} while "
        f"pr.source_attribution is {attribution_desc} -- "
        + remedy.format(tok=f"{{{tok}}}") + "."
        for tok in risky_tokens
    ]

