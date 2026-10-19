"""In-process negative admission cases for the unit packet itself."""

import copy
import hashlib

import pytest

from promotion_unit_contract import (
    PROFILE, UnitEvidenceError, admit_manifest, changed_paths, decode_json, manifest_digest, relative_file,
    requires_units, source_sha, verify_observations, verify_sources,
)

NODE = "tools/promotion_unit_tests/test_example.py::test_example"
SOURCE = "tools/example.py"


def manifest():
    return {
        "profile": PROFILE, "nodes": [NODE],
        "sources": {NODE.split("::")[0]: "a" * 64, SOURCE: "b" * 64},
        "coverage_targets": [SOURCE], "max_seconds": 300,
    }


def reports():
    return [{"node": NODE, "phase": phase, "outcome": "passed"}
            for phase in ("setup", "call", "teardown")]


def test_positive_unit_manifest_and_complete_observations():
    data = manifest()
    assert admit_manifest(data) == data
    verify_observations([NODE], [NODE], reports(), [], 1.25, 300)
    assert manifest_digest(data) == manifest_digest(copy.deepcopy(data))
    assert source_sha("a" * 40) == "a" * 40


def test_duplicate_json_fields_cannot_hide_unit_selection():
    with pytest.raises(UnitEvidenceError, match="duplicate"):
        decode_json('{"profile":"units","profile":"full"}')
    with pytest.raises(UnitEvidenceError, match="duplicate"):
        decode_json('{"nested":{"nodes":[],"nodes":["hidden"]}}')


def test_unit_source_inventory_cannot_have_case_aliases():
    data = manifest()
    data["sources"][SOURCE.upper().replace(".PY", ".py")] = "c" * 64
    with pytest.raises(UnitEvidenceError, match="case aliases"):
        admit_manifest(data)


@pytest.mark.parametrize("path", [
    "../test.py", "/test.py", "tools/./test.py", "tools//test.py",
    "tools\\test.py", ".git/test.py", "tools/test.pyc",
])
def test_noncanonical_or_git_source_is_refused(path):
    with pytest.raises(UnitEvidenceError):
        relative_file(path)


@pytest.mark.parametrize("change", [
    {"profile": "full"},
    {"nodes": []},
    {"nodes": [NODE, NODE]},
    {"nodes": ["tools/test_mixed.py::test_example"]},
    {"coverage_targets": ["tools/unadmitted.py"]},
    {"max_seconds": 301},
    {"max_seconds": True},
    {"selection": "-k smoke"},
])
def test_invalid_manifest_is_refused(change):
    data = manifest()
    data.update(change)
    with pytest.raises(UnitEvidenceError):
        admit_manifest(data)


@pytest.mark.parametrize("collected", [[], [NODE, NODE], [NODE + "_unexpected"]])
def test_missing_duplicate_or_unexpected_collection_is_refused(collected):
    with pytest.raises(UnitEvidenceError):
        verify_observations([NODE], collected, reports(), [], 1, 300)


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
@pytest.mark.parametrize("outcome", ["skipped", "failed"])
def test_every_phase_must_pass_without_skips(phase, outcome):
    observed = reports()
    next(report for report in observed if report["phase"] == phase)["outcome"] = outcome
    with pytest.raises(UnitEvidenceError):
        verify_observations([NODE], [NODE], observed, [], 1, 300)


@pytest.mark.parametrize("observed", [[], reports()[:-1], reports() + reports()[:1]])
def test_missing_or_duplicate_phase_is_refused(observed):
    with pytest.raises(UnitEvidenceError):
        verify_observations([NODE], [NODE], observed, [], 1, 300)


@pytest.mark.parametrize("effects,elapsed", [(["subprocess.Popen"], 1), ([], 300)])
def test_effect_attempt_and_whole_packet_timeout_are_refused(effects, elapsed):
    with pytest.raises(UnitEvidenceError):
        verify_observations([NODE], [NODE], reports(), effects, elapsed, 300)


def test_source_digest_rejects_changed_or_missing_bytes(tmp_path):
    root = tmp_path.resolve()
    path = root / "example.py"
    original = b"value = 1\n"
    path.write_bytes(original)
    expected = {"example.py": hashlib.sha256(original).hexdigest()}
    verify_sources(root, expected)
    path.write_bytes(b"value = 2\n")
    with pytest.raises(UnitEvidenceError, match="changed"):
        verify_sources(root, expected)
    with pytest.raises(UnitEvidenceError, match="missing"):
        verify_sources(root, {"missing.py": "a" * 64})


def test_only_known_narrative_changes_can_skip_the_admitted_packet():
    assert not requires_units(["docs/guide.md", "efforts/active/example/README.md"])
    assert requires_units(["plugins/example/skills/task/SKILL.md"])
    assert requires_units(["tools/unknown.py"])
    assert requires_units(["docs/guide.md", "tools/promotion_unit_contract.py"])
    assert requires_units([])
    with pytest.raises(UnitEvidenceError):
        requires_units(["../unsafe"])


def test_source_to_document_rename_preserves_the_deleted_source_for_impact():
    commands = []

    def read(arguments):
        commands.append(arguments)
        return b"tools/deleted.py\0docs/example.md\0"

    paths = changed_paths("a" * 40, "b" * 40, read)
    assert paths == ["tools/deleted.py", "docs/example.md"]
    assert commands == [[
        "git", "diff", "--no-renames", "--name-only", "-z", "a" * 40, "b" * 40,
    ]]
    assert requires_units(paths)
