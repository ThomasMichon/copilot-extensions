"""In-process byte and provenance tests; no execution or filesystem effects."""

import copy
import hashlib
import json

import pytest

from promotion_development_map import build_mapping, project_coverage, verify_mapping

DEV = "a" * 40
TREE = "b" * 40
SOURCE = b'__version__ = "1.0.0"\ndef answer():\n    return 42\n'


def inputs(**changes):
    result = {
        "dev_sha": DEV, "candidate_tree": TREE, "sources": {"src/a.py": SOURCE},
        "candidate_sources": {"src/a.py": SOURCE}, "origins": {},
    }
    result.update(changes)
    return result


def resign(mapping):
    unsigned = {k: v for k, v in mapping.items() if k != "digest"}
    mapping["digest"] = hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def test_exact_source_is_byte_bound_and_canonical():
    args = inputs()
    mapping = build_mapping(**args)
    assert mapping["files"]["src/a.py"]["lines"] == {"1": 1, "2": 2, "3": 3}
    assert mapping["files"]["src/a.py"]["transform"] == {"kind": "exact-copy", "version": 1}
    assert mapping["sources"]["src/a.py"] == hashlib.sha256(SOURCE).hexdigest()
    assert verify_mapping(mapping, **args) == mapping
    assert build_mapping(**args) == mapping


def test_vendor_copies_keep_identity_then_union_phase_contexts():
    args = inputs(
        candidate_sources={"vendor/one.py": SOURCE, "vendor/two.py": SOURCE},
        origins={"vendor/one.py": "src/a.py", "vendor/two.py": "src/a.py"},
    )
    mapping = verify_mapping(build_mapping(**args), **args)
    phases = ["test_answer|setup", "test_answer|call", "test_answer|teardown"]
    raw = {"coverage": {
        "vendor/one.py": {"3": phases[:2]}, "vendor/two.py": {"3": phases[1:]},
    }, "tests": {"test_answer": {"duration_s": 0.1}}}
    before = copy.deepcopy(raw)
    projected = project_coverage(raw, mapping)
    assert raw == before == projected["raw_candidate"]
    assert projected["coverage"] == {"src/a.py": {"3": sorted(phases)}}
    assert set(projected["copies"]) == {"vendor/one.py", "vendor/two.py"}
    assert projected["copies"]["vendor/one.py"]["mapped"]["3"] == sorted(phases[:2])
    assert projected["evidence"] == {"lines": True, "branches": False, "functions": False}
    assert projected["attribution"] == "derived-dev-coordinates-not-dev-byte-execution"
    projected["raw_candidate"]["coverage"].clear()
    assert raw == before


def test_same_line_version_edit_maps_only_identical_lines():
    args = inputs(candidate_sources={"src/a.py": SOURCE.replace(b"1.0.0", b"1.0.1-dev3")})
    mapping = build_mapping(**args)
    record = mapping["files"]["src/a.py"]
    assert record["transform"]["kind"] == "known-version-stamp"
    assert record["lines"] == {"2": 2, "3": 3}
    assert record["unmapped"] == {"1": "changed-version-statement"}
    raw = {"coverage": {"src/a.py": {"1": ["test|setup"], "3": ["test|call"]}}}
    projected = project_coverage(raw, verify_mapping(mapping, **args))
    assert projected["coverage"] == {"src/a.py": {"3": ["test|call"]}}
    assert projected["copies"]["src/a.py"]["unmapped"]["1"] == {
        "contexts": ["test|setup"], "reason": "changed-version-statement",
    }
    assert projected["raw_candidate"] == raw


@pytest.mark.parametrize("candidate", [
    SOURCE.replace(b"42", b"43"),
    b"\n" + SOURCE,
    SOURCE.replace(b'"1.0.0"', b'get_version()'),
    SOURCE.replace(b"1.0.0", b"not-a-version"),
    SOURCE.replace(b"1.0.0", b"1.0.1") + b"\n",
    SOURCE.replace(b"1.0.0", b"1.0.1").replace(b"42", b"43"),
    SOURCE.replace(b'__version__ = "1.0.0"', b'__version__ = "1.0.1"; run()'),
    SOURCE.replace(b"__version__ =", b"__version__  ="),
    SOURCE.replace(b"1.0.0", b"1.0.1").replace(b"__version__ =", b"__version__  ="),
])
def test_unknown_rewrites_and_line_shifts_have_no_attribution(candidate):
    mapping = build_mapping(**inputs(candidate_sources={"src/a.py": candidate}))
    record = mapping["files"]["src/a.py"]
    assert record["transform"]["kind"] == "unmapped"
    assert record["lines"] == {}
    assert record["unmapped_reason"] == "unsupported-transform"
    assert project_coverage({"coverage": {"src/a.py": {"1": ["test"]}}}, mapping)["coverage"] == {}


def test_vendor_version_edits_are_not_blessed_as_same_path_stamps():
    mapping = build_mapping(**inputs(
        candidate_sources={"vendor/a.py": SOURCE.replace(b"1.0.0", b"1.0.1")},
        origins={"vendor/a.py": "src/a.py"},
    ))
    assert mapping["files"]["vendor/a.py"]["unmapped_reason"] == "unsupported-transform"


def test_generated_file_without_origin_keeps_unmapped_hits():
    mapping = build_mapping(**inputs(candidate_sources={"generated/a.py": b"pass\n"}))
    result = project_coverage({"coverage": {"generated/a.py": {"1": ["test"]}}}, mapping)
    assert result["coverage"] == {}
    assert result["copies"]["generated/a.py"]["origin"] is None
    assert result["copies"]["generated/a.py"]["unmapped"]["1"]["reason"] == "missing-origin"


@pytest.mark.parametrize("path", [
    "../a.py", "/a.py", "a//b.py", "a/./b.py", "a/../b.py", "a\\b.py",
    "C:/a.py", ".git/a.py", "a/.GIT/b.py", "a. /b.py", "a\x00.py", "a.pyc", "",
    "CON.py", "a/NUL/x.py", "cafe\u0301.py",
])
def test_noncanonical_paths_are_rejected(path):
    with pytest.raises(ValueError):
        build_mapping(**inputs(candidate_sources={path: SOURCE}))


def test_case_aliases_and_untrusted_origins_are_rejected():
    for change in (
        {"candidate_sources": {"SRC/a.py": SOURCE}},
        {"origins": {"src/a.py": "missing.py"}},
        {"origins": {"missing.py": "src/a.py"}},
        {"origins": {"src/a.py": "../a.py"}},
    ):
        with pytest.raises(ValueError):
            build_mapping(**inputs(**change))


@pytest.mark.parametrize("change", [
    {"dev_sha": "short"}, {"candidate_tree": "B" * 40},
    {"sources": {"src/a.py": "not bytes"}}, {"origins": []},
    {"candidate_sources": []}, {"sources": {1: SOURCE}},
])
def test_invalid_identity_and_inventory_types_fail(change):
    with pytest.raises(ValueError):
        build_mapping(**inputs(**change))


def test_two_candidates_one_dev_cannot_reuse_mapping():
    args = inputs()
    mapping = build_mapping(**args)
    for change in (
        {"candidate_tree": "c" * 40},
        {"candidate_sources": {"src/a.py": SOURCE.replace(b"1.0.0", b"1.0.1")}},
        {"sources": {"src/a.py": SOURCE + b"\n"}},
        {"origins": {"src/a.py": "src/a.py"}},
    ):
        other = inputs(**change)
        assert build_mapping(**other)["digest"] != mapping["digest"]
        with pytest.raises(ValueError):
            verify_mapping(mapping, **other)


def test_digest_tampering_is_rejected_and_rehash_is_not_provenance():
    args = inputs()
    mapping = build_mapping(**args)
    mapping["files"]["src/a.py"]["lines"]["3"] = 2
    with pytest.raises(ValueError):
        project_coverage({"coverage": {}}, mapping)
    resign(mapping)
    with pytest.raises(ValueError):
        verify_mapping(mapping, **args)
    with pytest.raises(ValueError):
        project_coverage({"coverage": {}}, mapping)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(version=True),
    lambda m: m.update(schema="foreign"),
    lambda m: m["files"]["src/a.py"]["transform"].update(kind="fuzzy"),
    lambda m: m["files"]["src/a.py"]["lines"].update({"1": True}),
    lambda m: m["candidate_sources"].update({"src/a.py": "bad-hash"}),
    lambda m: m.update(extra="field"),
])
def test_schema_types_and_rehashed_tampering_fail(mutate):
    args = inputs()
    mapping = build_mapping(**args)
    mutate(mapping)
    resign(mapping)
    with pytest.raises(ValueError):
        verify_mapping(mapping, **args)
    with pytest.raises(ValueError):
        project_coverage({"coverage": {}}, mapping)


@pytest.mark.parametrize("raw", [
    {}, {"coverage": []}, {"coverage": {"missing.py": {"1": ["test"]}}},
    {"coverage": {"src/a.py": {"0": ["test"]}}},
    {"coverage": {"src/a.py": {"4": ["test"]}}},
    {"coverage": {"src/a.py": {1: ["test"]}}},
    {"coverage": {"src/a.py": {"1": "test"}}},
    {"coverage": {"src/a.py": {"1": [False]}}},
    {"coverage": {1: {}, "src/a.py": {}}},
])
def test_invalid_raw_coverage_fails_closed(raw):
    with pytest.raises(ValueError):
        project_coverage(raw, build_mapping(**inputs()))


def test_promotion_files_shape_preserves_import_context_and_original_record():
    raw = {
        "meta": {"branch_coverage": False},
        "files": {"src/a.py": {
            "executed_lines": [1, 2, 3],
            "contexts": {"1": [""], "3": ["", "test|setup", "test|call", "test|teardown"]},
            "missing_lines": [], "summary": {"covered_lines": 3},
        }},
        "totals": {"covered_lines": 3},
    }
    original = copy.deepcopy(raw)
    result = project_coverage(raw, build_mapping(**inputs()))
    assert result["raw_candidate"] == raw == original
    assert result["coverage"] == {"src/a.py": {
        "1": [""], "2": [], "3": ["", "test|call", "test|setup", "test|teardown"],
    }}
    assert result["copies"]["src/a.py"]["mapped"] == result["coverage"]["src/a.py"]
    result["raw_candidate"]["files"].clear()
    assert raw == original


def test_promotion_unmapped_version_line_keeps_empty_import_context():
    raw = {"meta": {}, "files": {"src/a.py": {
        "executed_lines": [1], "contexts": {"1": [""]},
    }}, "totals": {}}
    args = inputs(candidate_sources={"src/a.py": SOURCE.replace(b"1.0.0", b"1.0.1")})
    result = project_coverage(raw, build_mapping(**args))
    assert result["coverage"] == {}
    assert result["copies"]["src/a.py"]["unmapped"]["1"] == {
        "contexts": [""], "reason": "changed-version-statement",
    }
    assert result["raw_candidate"] == raw


def test_legacy_empty_import_context_is_preserved():
    raw = {"coverage": {"src/a.py": {"1": [""]}}}
    assert project_coverage(raw, build_mapping(**inputs()))["coverage"] == {
        "src/a.py": {"1": [""]},
    }


@pytest.mark.parametrize("raw", [
    {"files": {}, "coverage": {}},
    {"files": []},
    {"files": {"src/a.py": []}},
    {"files": {"src/a.py": {"executed_lines": [True]}}},
    {"files": {"src/a.py": {"executed_lines": [0]}}},
    {"files": {"src/a.py": {"executed_lines": [1, 1]}}},
    {"files": {"src/a.py": {"executed_lines": "1"}}},
    {"files": {"src/a.py": {"executed_lines": [1], "contexts": []}}},
    {"files": {"src/a.py": {"executed_lines": [1], "contexts": {"2": [""]}}}},
    {"files": {"src/a.py": {"executed_lines": [1], "contexts": {1: [""]}}}},
    {"files": {"src/a.py": {"executed_lines": [1], "contexts": {"1": [False]}}}},
])
def test_invalid_or_ambiguous_promotion_shape_is_rejected(raw):
    with pytest.raises(ValueError):
        project_coverage(raw, build_mapping(**inputs()))
