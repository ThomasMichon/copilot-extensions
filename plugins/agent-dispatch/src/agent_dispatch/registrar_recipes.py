"""Registrar ``extends:`` resolution -- recipe references + deep-merge.

See ``efforts/active/agent-dispatch-recipe-library/phase-3-extends-registrar.md``
for the full design (why this sits ahead of ``registrar_discovery.py``'s
``kind``-dispatch rather than adding a new branch to it, the three
reference kinds, and merge semantics). In short: an ``extends:``-bearing
declaration resolves to an ordinary ``kind:``-shaped mapping *before*
anything else sees it, so every existing validate/expand function needs
zero changes -- this module's only job is that one resolution step.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .recipes import EXTERNAL_AUTHOR_CLAUSE, RESOLUTION_CLAUSE, STAGNATION_CLAUSE, SUSPEND_CLAUSE
from .registrar import RegistrarError

#: Shared, generalized standing-loop charters for the reviewer and
#: conflict-resolution archetypes (Sub-PR 2, ThomasMichon/copilot-extensions
#: #4691 Phase 3). Unlike the ad-hoc ``agent_dispatch.recipes`` CLI
#: archetypes' own charter templates, these carry no ``{repo}``/``{pr}``
#: placeholders: a standing ``reviewer-loop`` pool's ``pool.body.charter``
#: is static, general-purpose conduct applied to *every* task the pool
#: claims -- the specific repo/PR/landing-model detail for one occurrence
#: lives in that occurrence's own task (``goal``/``prompt``), which the
#: declaration's own discovery emitter sets, not this static charter. The
#: four shared clauses are reused verbatim from the ad-hoc recipes (see
#: ``recipes/registry.py``) rather than duplicated, so sharpening one
#: clause improves every charter built on it.
_REVIEWER_CHARTER = (
    "You are a standing reviewer for this pool's target repository, picking up "
    "one pull request at a time under that task's own stated landing model "
    "(`land=self` or `land=author` -- read the task's own goal/prompt for "
    "which applies, since a single pool may serve more than one review "
    "policy).\n\n"
    "Loop: read the change and post specific feedback or approve. Under "
    "`land=self`, drive it toward merge and take ownership of landing when "
    "ready. Under `land=author`, the author owns updates and landing: record "
    "the delivered verdict, suspend without holding worker capacity, and "
    "resume only when the change updates or the non-response policy expires. "
    "Never merge on the author's behalf in that model. " + SUSPEND_CLAUSE
    + " When the change updates, resume and re-review only what moved.\n\n"
    + EXTERNAL_AUTHOR_CLAUSE + "\n\n"
    + STAGNATION_CLAUSE + "\n\n"
    + RESOLUTION_CLAUSE
)

_CONFLICT_RESOLUTION_CHARTER = (
    "You are a standing conflict-resolution worker for this pool's target "
    "repository: each task you pick up names a pull request an automated "
    "producer opened that is now stuck -- it has merge conflicts against its "
    "base and nobody is driving it. Take the last mile to a mergeable state "
    "-- check out its branch into a local worktree, rebase (or merge) the "
    "base branch in, resolve the conflicts, and **force-push the resolved "
    "branch back over the PR head** so the same PR updates in place -- never "
    "open a second PR. Then answer its review and build state.\n\n"
    + SUSPEND_CLAUSE + " Resume on the next review/build/update and iterate "
    "until it lands.\n\n"
    "Stay within the intent of the existing change -- you are unblocking it, "
    "not redesigning it.\n\n"
    + STAGNATION_CLAUSE + "\n\n"
    + RESOLUTION_CLAUSE
)

#: Labels excluded from backlog eligibility by every real repository-issue-loop
#: adopter today (both this repo's own harness/dotfiles triage and backlog
#: loops already repeat this exact list verbatim) -- the effort's own
#: motivating operational finding (per-repo duplication of genuinely shared
#: config) made concrete for one field.
_COMMON_EXCLUDE_LABELS = ["bootstrap", "wontfix", "invalid", "duplicate", "question"]

#: Built-in, plugin-shipped recipe templates, keyed by name (referenced as
#: ``extends: "global:<name>"``). Each covers the fields real adopters
#: already repeat verbatim (shared exclude-label conventions, the headless
#: pool body type, the archetype's standing-conduct charter) while leaving
#: everything genuinely repo-specific (target repo, forge producer login,
#: task label, emitter discovery command, evaluator verdict-application
#: policy) for the declaration itself to supply -- see
#: ``phase-3-extends-registrar.md``'s Sub-PR 2 description.
#:
#: ``repository-issue-loop`` is the one truly generic entry (no archetype
#: charter -- what the loop is *for* varies completely per adopter).
#: ``goal-driven`` reuses the same engine with the goal-driven archetype's
#: standing identity. ``reviewer``/``conflict-resolution`` both reuse the
#: ``reviewer-loop`` engine (the standing-loop counterpart of those two
#: ad-hoc CLI archetypes), differing only in ``pool.body.charter``.
GLOBAL_RECIPES: dict[str, Mapping[str, Any]] = {
    "repository-issue-loop": {
        "kind": "repository-issue-loop",
        "exclude_labels": list(_COMMON_EXCLUDE_LABELS),
        "pool": {"body": {"type": "headless"}},
    },
    "goal-driven": {
        "kind": "repository-issue-loop",
        "exclude_labels": list(_COMMON_EXCLUDE_LABELS),
        "worker_identity": "goal-driven",
        "pool": {"body": {"type": "headless"}},
    },
    "reviewer": {
        "kind": "reviewer-loop",
        "pool": {"body": {"type": "headless", "charter": _REVIEWER_CHARTER}},
    },
    "conflict-resolution": {
        "kind": "reviewer-loop",
        "pool": {
            "body": {"type": "headless", "charter": _CONFLICT_RESOLUTION_CHARTER}
        },
    },
}

#: Recipe files are declaration documents (YAML/JSON), same suffix contract
#: `registrar_discovery.py` enforces for every other declaration file.
_RECIPE_SUFFIXES = (".yaml", ".yml", ".json")

#: Matches only a bare ``{identifier}`` placeholder -- never a format spec
#: (``{0}``, ``{x:>10}``) and never anything containing a space, quote, or
#: colon. This is deliberately a plain regex substitution, not
#: ``str.Formatter``/``.format_map``: a template's prose may legitimately
#: contain literal braces (e.g. a worker-guidance string documenting an
#: expected JSON shape like ``{"decision": "emit"}``), and format-mini-
#: language parsing raises on exactly that content. A regex match on a
#: narrow identifier pattern can never raise. The surrounding
#: (?<!\{) / (?!\}) guards exclude a placeholder doubled up in extra braces
#: (``{{name}}``) from matching -- that's a literal-brace escaping
#: convention (mirroring ``str.format``'s own ``{{``/``}}`` escape), not a
#: placeholder, and must be left completely untouched.
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` over ``base``.

    A nested mapping present on both sides is merged key-by-key (recursing);
    every other value -- including a list -- is replaced wholesale by
    ``override``'s value when ``override`` sets it. ``override`` always wins
    on conflict; a key only ``base`` sets passes through unchanged.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _fill(value: str, params: Mapping[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(params[key]) if key in params else match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, value)


def substitute_placeholders(
    value: Any, params: Mapping[str, Any], *, _ancestors: frozenset[int] = frozenset()
) -> Any:
    """Recursively fill ``{param}``-style placeholders in every string leaf
    of ``value`` (a recipe template's field, which may be a nested mapping
    or list) from ``params``. Only scalar (``str``/``int``/``float``/``bool``)
    param values are usable as substitutions -- a nested structure can't
    sensibly fill a string placeholder. An unresolved placeholder, or any
    other brace content a narrow identifier pattern doesn't match, is left
    completely intact -- never an error (see :data:`_PLACEHOLDER_RE`).

    ``_ancestors`` tracks the object ids currently on the recursion stack
    (not every object ever seen), so a YAML document's self-referential
    alias -- a mapping/list that (directly or transitively) contains
    itself -- raises a clear ``RegistrarError`` instead of recursing
    forever (``RecursionError`` is not a ``RegistrarError`` and would abort
    the whole registrar scan, not just this one malformed recipe). A
    *non*-cyclic shared alias (the same sub-object reachable from two
    different sibling branches, never from itself) is unaffected: each
    branch's ``_ancestors`` only tracks its own chain of parents.
    """
    if isinstance(value, str):
        return _fill(value, params)
    if isinstance(value, (Mapping, list)):
        marker = id(value)
        if marker in _ancestors:
            raise RegistrarError(
                "extends: recipe template contains a cyclic reference "
                "(a mapping/list that contains itself)"
            )
        child_ancestors = _ancestors | {marker}
        if isinstance(value, Mapping):
            return {
                key: substitute_placeholders(v, params, _ancestors=child_ancestors)
                for key, v in value.items()
            }
        return [
            substitute_placeholders(v, params, _ancestors=child_ancestors)
            for v in value
        ]
    if isinstance(value, tuple):
        return tuple(
            substitute_placeholders(v, params, _ancestors=_ancestors) for v in value
        )
    return value


def _load_recipe_document(ref: str, *, base_dir: Path) -> Mapping[str, Any]:
    """Read and decode a repo-local or cross-repo recipe file reference.

    ``ref`` is a plain filesystem path, relative (resolved against
    ``base_dir``) or absolute -- both a repo-local ref (``./recipes/x.yaml``)
    and a cross-repo one (``../other-repo/.../x.yaml``) are plain paths read
    identically; the distinction is purely in what the author writes, not in
    how this function treats it. A declaration author is already a trusted
    party for the repo's own registrar declarations.
    """
    from .registrar_discovery import (  # local import: avoid an import cycle
        RegistrarIndeterminateError,
        _decode,
    )

    try:
        path = Path(ref)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
    except ValueError as exc:
        raise RegistrarError(
            f"extends: recipe ref {ref!r} could not be resolved to a path: {exc}"
        ) from exc
    if path.suffix not in _RECIPE_SUFFIXES:
        raise RegistrarError(
            f"extends: recipe file {ref!r} (resolved {path}) has unrecognized "
            f"suffix {path.suffix!r}; expected one of {list(_RECIPE_SUFFIXES)}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RegistrarError(
            f"extends: recipe file {ref!r} (resolved {path}) has invalid encoding: {exc}"
        ) from exc
    except OSError as exc:
        # Indeterminate, not invalid: a transient permission/read race on an
        # extends: target must not be classified the same as a genuinely
        # malformed declaration -- registrar_registry._classify_declaration()
        # preserves a plugin declaration's last-known state only for this
        # error class, same contract read_declaration_file_set's own
        # equivalent OSError branch already follows.
        raise RegistrarIndeterminateError(
            f"extends: could not read recipe file {ref!r} (resolved {path}): {exc}"
        ) from exc
    return _decode(text, path.suffix, where=str(path))


def resolve_recipe_ref(ref: object, *, base_dir: Path) -> Mapping[str, Any]:
    """Resolve an ``extends:`` reference to its recipe template document.

    Three reference kinds: ``global:<name>`` (plugin-shipped, looked up in
    :data:`GLOBAL_RECIPES`), and a repo-local or cross-repo plain file path
    (both resolved relative to ``base_dir`` when not absolute). ``ref`` is
    typed ``object`` (not ``str``) deliberately: a malformed declaration may
    set ``extends: null`` or some other non-string value, and that must
    raise the same clear error here rather than crash a string-only caller.
    """
    if not isinstance(ref, str) or not ref:
        raise RegistrarError(f"extends: expected a non-empty string ref, got {ref!r}")
    if ref.startswith("global:"):
        name = ref[len("global:"):]
        try:
            return GLOBAL_RECIPES[name]
        except KeyError:
            known = ", ".join(sorted(GLOBAL_RECIPES)) or "(none shipped yet)"
            raise RegistrarError(
                f"extends: unknown global recipe {name!r}; known: {known}"
            ) from None
    return _load_recipe_document(ref, base_dir=base_dir)


def resolve_extends(data: Mapping[str, Any], *, base_dir: Path) -> dict[str, Any]:
    """Expand an ``extends:``-bearing declaration mapping into its fully
    resolved, ordinary ``kind:``-shaped form.

    Two reserved keys sit alongside ``extends:``, never passed through to
    the resolved output:

    - ``params:`` -- a mapping of substitution-only values. Use this for a
      value the template needs purely to fill a ``{placeholder}`` but that
      is not itself a valid top-level field of the resolved declaration
      (e.g. a provider login the template interpolates into a nested
      ``spec.command`` string).
    - every *other* top-level key (the ordinary override fields -- `name`,
      `repo`, `owner`, ...) is used for substitution **and** deep-merged
      into the output, since those are legitimate declaration fields in
      their own right.

    The resolved template's string fields are filled first (unresolved
    placeholders left intact), then the ordinary override fields are
    deep-merged *over* the filled template -- the declaration wins on
    conflict. Returns ``data`` unchanged (as a plain ``dict``) when it
    carries no ``extends:`` key at all, so a caller can run every
    declaration through this function unconditionally -- but a *present*
    ``extends:`` key, even ``null``, is never silently treated as absent
    (that would bypass validation and surface as a misleading unrelated
    error further down the pipeline).
    """
    if "extends" not in data:
        return dict(data)
    template = resolve_recipe_ref(data["extends"], base_dir=base_dir)
    if not isinstance(template, Mapping):
        raise RegistrarError(
            f"extends: recipe {data['extends']!r} resolved to a non-mapping "
            f"document ({type(template).__name__})"
        )
    params_block = data.get("params", {})
    if not isinstance(params_block, Mapping):
        raise RegistrarError(
            f"extends: 'params' must be a mapping, got {type(params_block).__name__}"
        )
    overrides = {
        key: value for key, value in data.items() if key not in ("extends", "params")
    }
    scalar_params = {
        key: value
        for key, value in {**overrides, **params_block}.items()
        if isinstance(value, (str, int, float, bool))
    }
    filled_template = substitute_placeholders(template, scalar_params)
    return deep_merge(filled_template, overrides)
