"""Reconciliation identity reads use keysets, not corpus-sized offsets."""

from __future__ import annotations

import pytest

from agent_index.indexing.path_index import PathIndex


@pytest.mark.parametrize("family", ["forge:repo", "forge:r_%", "forge:é"])
def test_pages_preserve_exact_binary_family_and_identity_order(tmp_path, family):
    paths = PathIndex(tmp_path / "paths.db")
    expected = sorted([
        (family, "a"), (family, "z"), (family + ":issues", "1"),
        (family + ":issues", "2"), (family + ":pulls", "1"),
    ])
    unrelated = [
        (family + "0", "outside"), (family + ";other", "outside"),
        (family.upper(), "outside"), ("other:" + family, "outside"),
    ]
    paths.mark_indexed_batch([(source, path, None, 1) for source, path in expected + unrelated])
    found = []
    after = None
    while page := paths.get_family_page(family, after=after, limit=2):
        assert len(page) <= 2
        found.extend(page)
        after = page[-1]
    assert found == expected


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
@pytest.mark.parametrize("namespace", ["forge:repo", "forge:repo:issues"])
def test_late_keyset_units_do_not_scan_preceding_identities(tmp_path, monkeypatch, size, namespace):
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([(namespace, f"{i:06}", None, 1) for i in range(size)])
    paths.mark_indexed_batch([("forge:repo0", f"{i:06}", None, 1) for i in range(size)])
    connect = paths._connect
    steps = 0

    def measured():
        conn = connect()

        def step():
            nonlocal steps
            steps += 1
            return 0

        conn.set_progress_handler(step, 1)
        return conn

    monkeypatch.setattr(paths, "_connect", measured)
    page = paths.get_family_page("forge:repo", after=(namespace, f"{size - 8:06}"), limit=5)
    assert page == [(namespace, f"{i:06}") for i in range(size - 7, size - 2)]
    assert steps < 500, f"keyset unit executed {steps} SQLite VM steps at cardinality {size}"


@pytest.mark.parametrize("limit", [0, -1, 257, True, 1.5])
def test_invalid_identity_budget_refuses_before_read(tmp_path, limit):
    with pytest.raises(ValueError, match="1 to 256"):
        PathIndex(tmp_path / "paths.db").get_family_page("forge:repo", limit=limit)


@pytest.mark.parametrize("after", [("", "path"), ("foreign", "path"), ("forge:repo", ""), ["forge:repo", "p"]])
def test_invalid_identity_cursor_refuses(tmp_path, after):
    with pytest.raises(ValueError, match="cursor"):
        PathIndex(tmp_path / "paths.db").get_family_page("forge:repo", after=after)
