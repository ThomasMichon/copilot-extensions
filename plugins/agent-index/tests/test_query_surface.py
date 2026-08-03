"""Byte-shape tests for the shared query-surface text formatters.

These lock the exact rendering of ``format_hits`` / ``format_clusters`` so both
agent-index's own MCP tools and VEI's ``vei_*`` tool-shim (which imports these)
keep identical output. ``show_ids`` is the only difference between the two forms.
"""

from __future__ import annotations

from agent_index.query_surface import clip, format_clusters, format_hits

_HIT = {
    "chunk_id": "c1",
    "score": 0.987,
    "source": "monorepo",
    "file_path": "docs/x.md",
    "chunk_type": "heading",
    "language": "markdown",
    "content": "hello world",
    "line_start": 1,
    "line_end": 2,
}


def test_clip() -> None:
    assert clip("abc") == "abc"
    assert clip("x" * 600).endswith("...")
    assert len(clip("x" * 600)) == 503


def test_format_hits_without_ids_is_vei_shape() -> None:
    out = format_hits([_HIT], "Found 1 results for: hello")
    assert out == (
        "Found 1 results for: hello\n"
        "\n"
        "[1] docs/x.md (L1-2) [markdown/heading] score=0.987\n"
        "hello world\n"
    )
    # No id=/src= fields in the VEI form.
    assert "id=" not in out
    assert "src=" not in out


def test_format_hits_with_ids_appends_id_and_src() -> None:
    out = format_hits([_HIT], "Found 1 result(s) for: hello", show_ids=True)
    assert "score=0.987  id=c1  src=monorepo" in out


def test_format_hits_missing_line_start_omits_location() -> None:
    hit = {**_HIT, "line_start": None, "line_end": None}
    out = format_hits([hit], "h")
    assert "docs/x.md [markdown/heading]" in out
    assert "(L" not in out


def test_format_clusters_shape() -> None:
    clusters = [
        {
            "bucket": "git",
            "model_id": "code",
            "size": 2,
            "avg_score": 0.951,
            "has_exact_dupes": True,
            "representative": {"source": "git:repo", "file_path": "a.py"},
            "members": [
                {"source": "git:repo", "file_path": "a.py", "score": 1.0,
                 "is_exact_dupe": False},
                {"source": "git:repo", "file_path": "b.py", "score": 0.95,
                 "is_exact_dupe": True},
            ],
        }
    ]
    out = format_clusters(clusters, 1)
    assert out == (
        "Found 1 cluster(s)\n"
        "\n"
        "[1] git / code -- 2 items, avg=0.951 [has exact dupes]\n"
        "    rep: git:repo :: a.py\n"
        "      - git:repo :: a.py (score=1.000)\n"
        "      - git:repo :: b.py (score=0.950) (exact)\n"
    )
