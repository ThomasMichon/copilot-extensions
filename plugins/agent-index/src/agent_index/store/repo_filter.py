"""Repo-scoped source filtering, shared by the FTS/SQL layer (content_store)
and the vector-search post-filter layer (multi_model_store) so the two can't
silently diverge on what counts as "this repo's" source.

agent-index's OWN source-naming convention is ``git:<repo>`` or
``github:<owner>/<repo>``, each optionally followed by a derived-byproduct
suffix such as ``:commits``/``:issues``/``:pulls`` (see
``_resolve_explicit_source_spec`` in ``indexing/engine.py``). A bare ``repo``
query (``"odsp-web-harness"``) matches ANY type prefix/owner; an
``owner/repo``-qualified query (``"gim-home/odsp-web-harness"``) matches ONLY
that exact ``github:`` owner -- a ``git:`` source never carries an owner
segment in this scheme, so it's excluded entirely for an owner-qualified
query, not merely left unmatched by coincidence.

This replaces an inherited, mismatched filter (previously: "matches the
trailing owner/repo of any Forge source" -- a DIFFERENT source-naming
convention, ``forge:<subtype>:owner/repo``, from whatever system this core
was originally carved from) that silently matched at most a repo's bare
``git:`` source and NONE of its byproducts or its ``github:`` counterpart --
effectively useless for an agent trying to scope a search to "everything
about this repo."
"""

from __future__ import annotations


def repo_matches(source: str, repo: str) -> bool:
    """True if ``source`` belongs to ``repo`` (see module docstring)."""
    if not repo or ":" not in source:
        return False
    _prefix, rest = source.split(":", 1)
    identity = rest.split(":", 1)[0]  # strip a trailing :<byproduct>, if any
    if identity == repo:
        return True
    return "/" in identity and identity.rsplit("/", 1)[1] == repo


def _sql_str(value: str) -> str:
    """Escape a value for use inside a single-quoted SQL string literal."""
    return value.replace("'", "''")


def _like_pattern(value: str) -> str:
    """Escape a value for a SQL ``LIKE`` pattern (with ``ESCAPE '\\'``)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return _sql_str(escaped)


def repo_filter_sql(repo: str) -> str:
    """Build the OR'd SQL condition matching every source belonging to ``repo``.

    Mirrors ``repo_matches`` exactly (same branching), used to narrow FTS
    candidates at the query level instead of only post-filtering already-
    fetched rows -- a source the SQL layer never fetches can never reach the
    Python post-filter either.
    """
    name_like = _like_pattern(repo)
    if "/" in repo:
        return (
            f"source = 'github:{_sql_str(repo)}' OR "
            f"source LIKE 'github:{name_like}:%' ESCAPE '\\'"
        )
    return (
        f"source = 'git:{_sql_str(repo)}' OR "
        f"source LIKE 'git:{name_like}:%' ESCAPE '\\' OR "
        f"source LIKE 'github:%/{name_like}' ESCAPE '\\' OR "
        f"source LIKE 'github:%/{name_like}:%' ESCAPE '\\'"
    )
