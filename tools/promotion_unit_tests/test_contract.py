"""In-process negative admission cases for the unit packet itself."""

import copy
import hashlib

import pytest

from promotion_unit_contract import (
    PROFILE, UnitEvidenceError, admit_manifest, changed_paths, ci_requires_code_checks, ci_requires_units, decode_json, manifest_digest, relative_file,
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
    assert not requires_units(["plugins/example/skills/task/SKILL.md"])
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


@pytest.mark.parametrize("event,expected", [
    ("push", False), ("workflow_dispatch", True), ("unknown", True),
])
def test_ci_events_preserve_promotion_ownership_and_conservative_fallback(event, expected):
    def no_diff(arguments):
        pytest.fail("non-PR admission must not attempt a Git diff")

    assert ci_requires_units(event, "", "", no_diff) is expected


@pytest.mark.parametrize("paths,expected", [
    (b"docs/guide.md\0", False),
    (b"tools/source.py\0", True),
    (b"tools/source.py\0docs/guide.md\0", True),
    (b"", True),
])
def test_combined_pr_admission_preserves_exact_source_and_narrative_selection(paths, expected):
    assert ci_requires_units("pull_request", "a" * 40, "b" * 40, lambda _: paths) is expected


@pytest.mark.parametrize("path", [
    "README.md", "AGENTS.md", "plugins/example/README.md",
    "plugins/example/skills/task/SKILL.md", "worktree-manager/docs/usage.MD",
    ".github/workflows/example.md", "arbitrary/new-document.md",
])
def test_markdown_has_no_executable_unit_or_guard_overlap(path):
    assert not requires_units([path])
    for event in ("pull_request", "push"):
        assert not ci_requires_code_checks(
            event, "a" * 40, "b" * 40, lambda _: path.encode() + b"\0",
        )


@pytest.mark.parametrize("paths", [
    b"docs/readme.md\0tools/source.py\0", b"docs/example.json\0",
    b".github/workflows/example.yml\0", b"tools/deleted.py\0docs/moved.md\0", b"",
])
def test_code_guard_selection_keeps_mixed_unknown_and_empty_changes(paths):
    assert ci_requires_code_checks(
        "pull_request", "a" * 40, "b" * 40, lambda _: paths,
    )


@pytest.mark.parametrize("event,base,head", [
    ("workflow_dispatch", "", ""), ("future-event", "a" * 40, "b" * 40),
    ("push", "0" * 40, "b" * 40), ("pull_request", "", ""),
])
def test_unavailable_code_guard_inputs_never_claim_no_overlap(event, base, head):
    def no_diff(_):
        pytest.fail("unavailable input must not be mistaken for an empty diff")

    assert ci_requires_code_checks(event, base, head, no_diff)


@pytest.mark.parametrize("event,merge_base", [("pull_request", True), ("push", False)])
def test_pr_scope_uses_merge_base_while_push_scope_retains_both_tips(event, merge_base):
    calls = []

    def read(argv):
        calls.append(argv)
        return b"docs/guide.md\0" if "--merge-base" in argv else b"tools/upstream.py\0docs/guide.md\0"

    assert ci_requires_code_checks(event, "a" * 40, "b" * 40, read) is (not merge_base)
    assert ("--merge-base" in calls[0]) is merge_base
    if merge_base:
        assert not ci_requires_units(event, "a" * 40, "b" * 40, read)
