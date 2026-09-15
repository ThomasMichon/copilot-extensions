"""Shared boolean-predicate engine over a path/op mini-language.

Used by both ``gate`` (evaluated against a preflight lookup's result) and
``input_gate`` (evaluated against a tool call's own arguments) so the two
share identical path resolution and operator semantics -- a config author
who learns one gate's predicate syntax already knows the other's.

```yaml
allow_when:                                  # (or deny_when, etc. -- caller-named)
  all:
    - any:
        - { path: "tags[*]", in: ["public", "internal"] }
        - { path: "title", matches: "\\[OK\\]" }
    - { path: "isSensitive", equals: false }
```

Leaf ops: ``in``, ``equals``, ``matches``, ``contains``, ``exists`` (positive),
and their negative twins ``not_in``, ``not_equals``, ``not_matches`` (a
negative op is vacuously TRUE when its path resolves to nothing -- "nothing
violates it"). Combinators: ``all`` (AND), ``any`` (OR), ``not``.
"""

from __future__ import annotations

import re
from typing import Any

_STEP_RE = re.compile(r"([^.\[\]]+)|\[(\*)\]|\[(-?\d+)\]")

# A path must be FULLY covered by steps (key / [*] / [n]) joined by literal
# dots -- used to REJECT a path containing invalid syntax at validation time.
# _STEP_RE's own `findall` is intentionally permissive at resolve-time (it
# just skips anything it doesn't recognize), which means a typo like
# "tags[foo]" or an unterminated "tags[" silently degrades to whatever steps
# DO match instead of raising -- for a security predicate that's a fail-open
# risk (see validate_predicate), so this stricter fullmatch pattern is used
# ONLY to validate a path string's syntax, never to resolve it.
_PATH_FULLMATCH_RE = re.compile(
    r"^[^.\[\]]+(?:\[(?:\*|-?\d+)\])*(?:\.[^.\[\]]+(?:\[(?:\*|-?\d+)\])*)*$")

# Leaf comparison ops. "Positive" ops are satisfied when ANY resolved value
# matches; their negative twins are satisfied when NO resolved value matches
# (vacuously true when the path resolves to nothing).
_POSITIVE_OPS = ("in", "equals", "matches", "contains", "exists")
_NEGATIVE_OPS = {"not_in": "in", "not_matches": "matches", "not_equals": "equals"}
_ALL_OPS = (*_POSITIVE_OPS, *_NEGATIVE_OPS)

__all__ = ["eval_leaf", "eval_predicate", "parse_path", "resolve_path", "validate_predicate"]


def parse_path(path: str) -> list[tuple[str, Any]]:
    """Tokenize a path (``a.b[*].c`` / ``tags[*]`` / ``x[0]``) into steps."""
    steps: list[tuple[str, Any]] = []
    for key, wild, idx in _STEP_RE.findall(str(path)):
        if key:
            steps.append(("key", key))
        elif wild:
            steps.append(("wild", None))
        elif idx:
            steps.append(("idx", int(idx)))
    return steps


def resolve_path(doc: Any, path: str) -> list[Any]:
    """Resolve ``path`` in ``doc`` to the (0..n) values it addresses.

    ``[*]`` fans out over a list; a bare key descends an object; ``[n]`` indexes a
    list. A key that misses, or a type mismatch, simply contributes no values --
    so an absent path yields ``[]`` (which reads as "condition not satisfied").
    """
    nodes: list[Any] = [doc]
    for kind, val in parse_path(path):
        nxt: list[Any] = []
        for node in nodes:
            if kind == "key":
                if isinstance(node, dict) and val in node:
                    nxt.append(node[val])
            elif kind == "wild":
                if isinstance(node, list):
                    nxt.extend(node)
            elif kind == "idx" and isinstance(node, list) and -len(node) <= val < len(node):
                nxt.append(node[val])
        nodes = nxt
    return nodes


def _leaf_positive(op: str, value: Any, resolved: list[Any]) -> bool:
    """Evaluate a positive leaf op: true if ANY resolved value satisfies it."""
    if op == "exists":
        present = len(resolved) > 0
        return present if bool(value) else not present
    for v in resolved:
        if op == "in" and isinstance(value, list) and v in value:
            return True
        if op == "equals" and v == value:
            return True
        if op == "matches" and isinstance(v, str) and re.search(str(value), v):
            return True
        if op == "contains":
            # `x in v` is safe for any `value` when `v` is a list (element-wise
            # `==`), but raises TypeError for a str `v` unless `value` is ALSO
            # a str (e.g. `1 in "abc"`). input_gate evaluates this against
            # caller-controlled call arguments, so a mismatched-type config
            # must not crash predicate evaluation -- restrict the str branch
            # to str values instead of letting it raise.
            if isinstance(v, list) and value in v:
                return True
            if isinstance(v, str) and isinstance(value, str) and value in v:
                return True
    return False


def eval_leaf(node: dict, doc: Any, *, log=None) -> bool:
    path = node.get("path")
    resolved = resolve_path(doc, path) if path is not None else []
    for op, value in node.items():
        if op == "path":
            continue
        if op in _NEGATIVE_OPS:
            # No resolved value may satisfy the positive twin (vacuously true).
            if _leaf_positive(_NEGATIVE_OPS[op], value, resolved):
                return False
        elif op in _POSITIVE_OPS:
            if not _leaf_positive(op, value, resolved):
                return False
        elif log is not None:
            log.warning("predicate: unknown op '%s' (ignored)", op)
    return True


def validate_predicate(node: Any, label: str) -> list[str]:
    """Recursively validate a predicate tree's SHAPE (not the data it will run
    against): every combinator/leaf is a well-formed node, every leaf names a
    known op, and every ``matches``/``not_matches`` regex compiles.

    This exists so a malformed predicate is caught at config-load time
    (``agent-mcp validate``) rather than silently mis-evaluating at runtime.
    Two failure modes this specifically closes:

    * A malformed node (e.g. ``null`` inside an ``any:`` list, or a leaf with
      no recognized op) would otherwise reach :func:`eval_predicate`, which
      treats "not a dict" / "no matching op" as simply FALSE -- for a
      restrictive predicate like ``input_gate``'s ``deny_when`` this is
      FAIL-OPEN (a broken config silently denies nothing instead of refusing
      to load).
    * An invalid regex in ``matches``/``not_matches`` would otherwise only
      surface as an uncaught ``re.error`` the first time a matching call
      arrives at runtime, well after the config was accepted.
    """
    errors: list[str] = []
    if not isinstance(node, dict):
        errors.append(f"{label}: predicate node must be a mapping, got {node!r}")
        return errors
    combinators = [k for k in ("all", "any", "not") if k in node]
    if combinators:
        if len(combinators) > 1 or len(node) > 1:
            errors.append(
                f"{label}: a combinator node must contain exactly one of "
                "'all'/'any'/'not' and nothing else")
        key = combinators[0]
        if key == "not":
            errors.extend(validate_predicate(node["not"], f"{label}.not"))
        else:
            children = node[key]
            if not isinstance(children, list):
                errors.append(f"{label}.{key} must be a list of predicate nodes")
            else:
                for i, child in enumerate(children):
                    errors.extend(validate_predicate(child, f"{label}.{key}[{i}]"))
        return errors
    # A leaf: must have a string 'path' and at least one recognized op.
    path = node.get("path")
    if not isinstance(path, str) or not path:
        errors.append(f"{label}: leaf predicate requires a non-empty string 'path'")
    elif not _PATH_FULLMATCH_RE.fullmatch(path):
        # _resolve_path's tokenizer is a permissive `findall` that silently
        # SKIPS anything it doesn't recognize (e.g. "tags[foo]" degrades to
        # just "tags", "tags[" degrades to just "tags") -- it never errors.
        # For a security predicate that's a fail-open risk: a typo'd path
        # could resolve to a DIFFERENT (wrong) field than intended and never
        # match what the author meant to gate on. Reject it at validation
        # time instead of letting it silently mis-resolve at runtime.
        errors.append(
            f"{label}.path: {path!r} is not valid path syntax (dotted keys, "
            "'[*]' wildcards, and '[n]'/'[-n]' indices only -- e.g. "
            "'tags[*]', 'items[*].n', 'a.b[-1]')")
    ops_present = [k for k in node if k != "path"]
    if not ops_present:
        errors.append(f"{label}: leaf predicate requires at least one op ({', '.join(_ALL_OPS)})")
    for op in ops_present:
        if op not in _ALL_OPS:
            errors.append(f"{label}.{op}: unknown predicate op (known: {', '.join(_ALL_OPS)})")
        elif op in ("matches", "not_matches"):
            try:
                re.compile(str(node[op]))
            except re.error as exc:
                errors.append(f"{label}.{op}: invalid regex {node[op]!r} ({exc})")
        elif op in ("in", "not_in") and not isinstance(node[op], list):
            errors.append(f"{label}.{op} must be a list")
    return errors


def eval_predicate(node: Any, doc: Any, *, log=None) -> bool:
    """Evaluate a predicate node (``all``/``any``/``not`` combinator or a leaf)."""
    if not isinstance(node, dict):
        return False
    if "all" in node:
        return all(eval_predicate(c, doc, log=log) for c in (node["all"] or []))
    if "any" in node:
        return any(eval_predicate(c, doc, log=log) for c in (node["any"] or []))
    if "not" in node:
        return not eval_predicate(node["not"], doc, log=log)
    return eval_leaf(node, doc, log=log)
