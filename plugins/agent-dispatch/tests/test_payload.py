"""Tests for the content-addressed payload blob store and engine spill."""

from __future__ import annotations

import pytest

from agent_dispatch.payload import BLOB_PREFIX, PayloadStore, is_blob_ref
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def store(tmp_path):
    return PayloadStore(tmp_path / "payloads")


# -- store -------------------------------------------------------------------


def test_put_get_roundtrip(store):
    ref = store.put("# hello\nworld\n")
    assert is_blob_ref(ref)
    assert ref.startswith(BLOB_PREFIX)
    assert store.get(ref) == "# hello\nworld\n"


def test_content_addressed_dedup(store):
    ref1 = store.put("same content")
    ref2 = store.put("same content")
    assert ref1 == ref2  # identical content -> identical ref
    files = list((store.root).glob("*.md"))
    assert len(files) == 1  # only one blob written


def test_distinct_content_distinct_refs(store):
    assert store.put("a") != store.put("b")


def test_has_and_missing(store):
    ref = store.put("x")
    assert store.has(ref) is True
    assert store.has("blob:deadbeef") is False
    assert store.has("pr/123") is False


def test_get_rejects_non_blob_ref(store):
    with pytest.raises(KeyError):
        store.get("pr/123")


def test_get_missing_blob(store):
    with pytest.raises(KeyError):
        store.get("blob:" + "0" * 64)


def test_is_blob_ref():
    assert is_blob_ref("blob:abc") is True
    assert is_blob_ref("pr/123") is False
    assert is_blob_ref(None) is False
    assert is_blob_ref("") is False


# -- engine integration ------------------------------------------------------


@pytest.fixture
def q(tmp_path):
    # small threshold so tests don't need giant strings
    return TaskQueue(tmp_path / "tasks.db", blob_threshold=16)


def test_small_payload_stays_inline(q):
    t = q.create("t", payload_inline="tiny")
    assert t.payload_inline == "tiny"
    assert t.payload_ref is None
    assert q.read_payload(t) == "tiny"


def test_large_payload_spills_to_blob(q):
    big = "x" * 100
    t = q.create("t", payload_inline=big)
    assert t.payload_inline is None
    assert is_blob_ref(t.payload_ref)
    assert q.read_payload(t) == big


def test_explicit_ref_respected(q):
    # a caller-supplied ref is never overwritten by the blob spill
    t = q.create("t", payload_ref="pr/123", payload_inline="x" * 100)
    assert t.payload_ref == "pr/123"  # not replaced by a blob ref
    # inline content is literal and still resolves first
    assert q.read_payload(t) == "x" * 100


def test_external_ref_is_opaque(q):
    t = q.create("t", payload_ref="pr/123")
    assert t.payload_ref == "pr/123"
    assert q.read_payload(t) is None  # external ref is opaque to the store


def test_read_payload_by_id(q):
    t = q.create("t", payload_inline="y" * 100)
    assert q.read_payload(t.id) == "y" * 100


def test_read_payload_none_for_empty(q):
    t = q.create("t")
    assert q.read_payload(t) is None


def test_default_payload_dir_beside_db(tmp_path):
    (tmp_path / "sub").mkdir()
    q = TaskQueue(tmp_path / "sub" / "tasks.db")
    assert q.payloads.root == tmp_path / "sub" / "payloads"


def test_dedup_key_survives_spill(q):
    big = "z" * 100
    a = q.create("t", payload_inline=big, dedup_key="k1")
    b = q.create("t", payload_inline=big, dedup_key="k1")
    assert a.id == b.id  # dedup still works when the payload spilled
    assert q.read_payload(b) == big
