"""The ``--repo`` search filter must match agent-index's OWN source-naming
convention (``git:<repo>`` / ``github:<owner>/<repo>``, each optionally
followed by a derived-byproduct suffix like ``:commits``/``:issues``/
``:pulls``) -- not the inherited ``forge:<subtype>:owner/repo`` convention
from whatever system this core was originally carved from. Before this fix,
``--repo <name>`` matched at most a repo's bare ``git:`` source and NONE of
its byproducts or its ``github:`` counterpart, making it effectively useless
for "find everything about this repo."

Covers both call sites that implement repo matching: the Python post-filter
(``multi_model_store.repo_matches``, used for the vector-search path and as
FTS's own safety-net re-filter) and the SQL WHERE-clause builder
(``content_store.repo_filter_sql``, used to narrow FTS candidates at the
query level) -- the two must agree, since a source the SQL filter never
fetches can never reach the Python post-filter either.
"""

from __future__ import annotations

import pytest

from agent_index.store.content_store import repo_filter_sql
from agent_index.store.multi_model_store import repo_matches

# (source, repo, expected) -- exercised against BOTH repo_matches and a
# literal re-implementation of the SQL semantics via sqlite3 below.
CASES = [
    # Bare-name query matches the git: source...
    ("git:odsp-web-harness-state", "odsp-web-harness-state", True),
    # ...and its byproducts...
    ("git:odsp-web-harness-state:commits", "odsp-web-harness-state", True),
    # ...and the github: counterpart, any owner...
    ("github:gim-home/odsp-web-harness-state", "odsp-web-harness-state", True),
    ("github:gim-home/odsp-web-harness-state:issues", "odsp-web-harness-state", True),
    ("github:other-owner/odsp-web-harness-state", "odsp-web-harness-state", True),
    ("github:other-owner/odsp-web-harness-state:pulls", "odsp-web-harness-state", True),
    # Owner-qualified query matches ONLY that owner's github: source...
    ("github:gim-home/odsp-web-harness-state", "gim-home/odsp-web-harness-state", True),
    ("github:gim-home/odsp-web-harness-state:issues", "gim-home/odsp-web-harness-state", True),
    # ...never a different owner...
    ("github:other-owner/odsp-web-harness-state", "gim-home/odsp-web-harness-state", False),
    # ...and never a git: source (no owner segment exists in that scheme).
    ("git:odsp-web-harness-state", "gim-home/odsp-web-harness-state", False),
    # Must not substring-match a longer/different repo name.
    ("git:odsp-web-harness", "odsp-web-harness-state", False),
    ("git:odsp-web-harness-state", "odsp-web-harness", False),
    ("github:gim-home/odsp-web-harness", "odsp-web-harness-state", False),
    # A source with no colon at all (shouldn't occur in practice) never matches.
    ("not-a-real-source", "odsp-web-harness-state", False),
]


@pytest.mark.parametrize("source,repo,expected", CASES)
def test_repo_matches(source: str, repo: str, expected: bool) -> None:
    assert repo_matches(source, repo) is expected


@pytest.mark.parametrize("source,repo,expected", CASES)
def test_repo_filter_sql_agrees_with_repo_matches(source: str, repo: str, expected: bool) -> None:
    """The SQL WHERE clause must accept/reject the exact same sources
    ``repo_matches`` does -- evaluated for real via sqlite3 (LIKE/ESCAPE
    semantics are sqlite-specific enough that hand-checking the string isn't
    good enough; LanceDB's own SQL filter engine shares sqlite's LIKE/ESCAPE
    grammar for this subset)."""
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (source TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (source,))
    where = repo_filter_sql(repo)
    matched = con.execute(f"SELECT 1 FROM t WHERE {where}").fetchone() is not None
    assert matched is expected


def test_repo_filter_sql_escapes_special_characters() -> None:
    """A repo name containing SQL-quote or LIKE-metacharacters must not break
    out of the generated clause or be treated as a wildcard."""
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (source TEXT)")
    # A literal percent/underscore in the name must be matched LITERALLY, not
    # as a LIKE wildcard matching any character.
    con.executemany(
        "INSERT INTO t VALUES (?)",
        [("git:weird%name",), ("git:weirdXname",), ("git:weird_name",)],
    )
    where = repo_filter_sql("weird%name")
    rows = {r[0] for r in con.execute(f"SELECT source FROM t WHERE {where}")}
    assert rows == {"git:weird%name"}
