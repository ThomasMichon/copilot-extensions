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

from .registrar import RegistrarError

#: Built-in, plugin-shipped recipe templates, keyed by name (referenced as
#: ``extends: "global:<name>"``).
#:
#: Only ``reviewer`` and ``repository-issue-loop`` ship here today -- both
#: already have a dedicated registrar ``kind:`` (``reviewer-loop`` /
#: ``repository-issue-loop``) that expands to a concrete emitter/evaluator/pool
#: triple (see ``reviewer_loops.py`` / ``repository_issue_loops.py``), so
#: templating them requires no new engine: the template supplies the
#: structural defaults (evaluator ruleset, pool shape, cadence/label/reservation
#: defaults); a declaration still supplies its own irreducibly domain-specific
#: fields (``name``, ``repo``, ``task_label``, the emitter's discovery
#: ``command``, ``forge.producer_login``, ``pool.body.agent``) -- there is no
#: sensible generic default for those.
#:
#: ``conflict-resolution`` and ``goal-driven`` are not shipped here: neither
#: has a registrar ``kind:`` to expand to today, so there is no
#: emitter/evaluator pair yet for a template to describe. See
#: ``efforts/active/agent-dispatch-recipe-library/phase-3-extends-registrar.md``
#: for the sequencing.
GLOBAL_RECIPES: dict[str, Mapping[str, Any]] = {
    "reviewer": {
        "kind": "reviewer-loop",
        "emitter": {
            "interval_seconds": 300,
            "task_output": "json",
        },
        "evaluator": {
            "evaluator_spec": {"rules": []},
        },
        "pool": {
            "max_active_processes": 1,
            "body": {"type": "headless"},
        },
    },
    "repository-issue-loop": {
        "kind": "repository-issue-loop",
        "source": "repository-backlog",
        "cadence_seconds": 21600,
        "quiet_period_seconds": 1800,
        "include_labels": ["ready"],
        "exclude_labels": ["bootstrap", "wontfix"],
        "batch_size": 1,
        "forge": {
            "provider": "github",
        },
        "reservation": {
            "label": "agent-reserved",
            "comment": True,
        },
        "pool": {
            "max_active_processes": 1,
            "body": {"type": "headless"},
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
