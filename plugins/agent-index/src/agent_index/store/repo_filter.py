"""Repo-scoped source filtering, shared by the FTS/SQL layer (content_store)
and the vector-search post-filter layer (multi_model_store) so the two can't
silently diverge on what counts as "this repo's" source.

The identity is the first colon-delimited component after the source scheme,
optionally followed by a byproduct suffix. This covers ``git:<repo>``,
``github:<owner>/<repo>`` and ``ado:<org>/<project>:workitems``. A bare repo
matches that exact identity or its final slash-delimited name; a qualified
repo matches the entire identity, including any host qualification.

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
    if not repo:
        return "FALSE"
    identity = "split_part(source, ':', 2)"
    exact = f"{identity} = '{_sql_str(repo)}'"
    if "/" in repo:
        return exact
    return f"{exact} OR {identity} LIKE '%/{_like_pattern(repo)}' ESCAPE '\\'"
