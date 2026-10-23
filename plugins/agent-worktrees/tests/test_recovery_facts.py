"""G1 is a disconnected reader/placement slice, exercised only in owned fixtures."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest

from agent_worktrees.recovery_facts import (
    MAX_BYTES, RecoveryError, decode_fact, encode_fact, patch_payload,
)
from agent_worktrees.recovery_markers import inspect_layout, marker_value

REPOSITORY = "00000000-0000-0000-0000-000000000001"
RESOURCE = "00000000-0000-0000-0000-000000000002"
OPERATION = "00000000-0000-0000-0000-000000000003"
WRITER = "00000000-0000-0000-0000-000000000004"
READ = frozenset({"fixture.read"})
WRITE = READ | {"fixture.patch"}
KNOWN = frozenset({("observation", "label")})


def fact() -> dict:
    return {
        "schema": "agent-worktrees.recovery-fact", "version": 1,
        "repository_id": REPOSITORY, "resource_id": RESOURCE,
        "operation_id": OPERATION, "writer_incarnation": WRITER,
        "revision": 7, "kind": "fixture.relationship",
        "required_capabilities": {"read": ["fixture.read"], "patch": ["fixture.patch"]},
        "payload": {
            "observation": {"label": "before", "future": {"unknown": [1, None, True]}},
            "opaque": {"future_semantics": "preserved"},
        },
        "future_envelope": {"nested": ["keep"]},
    }


def wire(value: dict) -> bytes:
    return json.dumps(value).encode()


def assert_reason(reason, action):
    with pytest.raises(RecoveryError) as error:
        action()
    assert error.value.reason == reason


def test_unknown_fields_round_trip_and_candidate_patch():
    original = fact()
    before = copy.deepcopy(original)
    parsed = decode_fact(wire(original), supported=READ)
    edited = patch_payload(
        parsed, {"observation": {"label": "after"}}, supported=WRITE, known_paths=KNOWN,
    )
    expected = copy.deepcopy(original)
    expected["payload"]["observation"]["label"] = "after"
    assert decode_fact(encode_fact(edited, supported=READ), supported=READ) == expected
    assert original == parsed == before
    assert edited["writer_incarnation"] == WRITER
    assert edited["revision"] == 7  # A codec does not commit or elect a writer.


@pytest.mark.parametrize("software_version", ["1.0.0", "99.0.0", "rollback"])
def test_software_version_does_not_decide_reader_compatibility(software_version):
    value = fact()
    value["producer_version"] = software_version
    assert decode_fact(wire(value), supported=READ) == value
    assert_reason(
        "required_capability_unsupported",
        lambda: patch_payload(value, {}, supported=READ, known_paths=KNOWN),
    )


def test_unknown_required_read_semantics_not_foreign_owner():
    value = fact()
    value["required_capabilities"]["read"].append("future.required")
    assert_reason("required_capability_unsupported", lambda: decode_fact(wire(value), supported=READ))
    assert decode_fact(wire(value), supported=READ | {"future.required"}) == value


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("schema", "other", "unsupported_schema"),
    ("version", 2, "unsupported_schema"),
    ("version", True, "unsupported_schema"),
    ("version", 1.0, "unsupported_schema"),
    ("revision", 0, "malformed_revision"),
    ("revision", True, "malformed_revision"),
    ("repository_id", "../elsewhere", "malformed_identity"),
    ("resource_id", None, "malformed_identity"),
    ("operation_id", "not-an-id", "malformed_identity"),
    ("writer_incarnation", "", "malformed_identity"),
    ("kind", "", "malformed_kind"),
    ("payload", [], "malformed_payload"),
    ("required_capabilities", {}, "malformed_capabilities"),
    ("required_capabilities", {"read": [], "patch": ["x", "x"]}, "malformed_capabilities"),
])
def test_malformed_envelopes_are_scoped(field, value, reason):
    document = fact()
    document[field] = value
    assert_reason(reason, lambda: decode_fact(wire(document), supported=WRITE))


@pytest.mark.parametrize(("raw", "reason"), [
    (b'{"schema":1,"schema":2}', "duplicate_field"),
    (b'{"x":{"a":1,"a":2}}', "duplicate_field"),
    (b'{"number":NaN}', "malformed_json"),
    (b"\xff", "malformed_json"),
    (b'{"unfinished":', "malformed_json"),
    (b" " * (MAX_BYTES + 1), "record_limit"),
    (b"[" * 1000 + b"]" * 1000, "record_limit"),
], ids=["duplicate-root", "duplicate-nested", "nonfinite", "utf8", "truncated", "size", "depth"])
def test_bounded_strict_json(raw, reason):
    assert_reason(reason, lambda: decode_fact(raw, supported=WRITE))


@pytest.mark.parametrize("number", [b"1e999", b"-1e999", b"9" * 5000],
                         ids=["positive-overflow", "negative-overflow", "integer-limit"])
def test_unknown_numeric_fields_must_round_trip(number):
    raw = wire(fact())[:-1] + b',"future_numeric":' + number + b"}"
    assert_reason("malformed_json", lambda: decode_fact(raw, supported=READ))


@pytest.mark.parametrize("number", [
    b"1e-999", b"-1e-999", b"9007199254740993.0", b"0.1234567890123456789",
    b"1e-" + b"9" * 40,
])
def test_decimal_precision_loss_refuses_before_candidate_edit(number):
    raw = wire(fact())[:-1] + b',"future_numeric":' + number + b"}"
    assert_reason("number_precision_unsupported", lambda: decode_fact(raw, supported=READ))


@pytest.mark.parametrize("number", [b"0.1", b"1.2300", b"-0.0", b"1.25e-12"])
def test_supported_decimal_values_survive_unrelated_edits(number):
    from decimal import Decimal
    raw = wire(fact())[:-1] + b',"future_numeric":' + number + b"}"
    value = decode_fact(raw, supported=READ)
    edited = patch_payload(value, {"observation": {"label": "after"}},
                           supported=WRITE, known_paths=KNOWN)
    encoded = encode_fact(edited, supported=READ)
    assert json.loads(encoded, parse_float=Decimal)["future_numeric"] == Decimal(number.decode())


@pytest.mark.parametrize(("character", "count"), [("é", 12000), ("😀", 6000)])
def test_canonical_unicode_expansion_is_refused_before_candidate(character, count):
    value = fact()
    value["future_unicode"] = character * count
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    assert len(raw) < MAX_BYTES
    assert_reason("record_limit", lambda: decode_fact(raw, supported=READ))


@pytest.mark.parametrize(("character", "count"), [("é", 10000), ("😀", 5000)])
def test_near_limit_unicode_round_trips(character, count):
    value = fact()
    value["future_unicode"] = character * count
    parsed = decode_fact(json.dumps(value, ensure_ascii=False).encode(), supported=READ)
    edited = patch_payload(parsed, {}, supported=WRITE, known_paths=KNOWN)
    assert decode_fact(encode_fact(edited, supported=READ), supported=READ) == value


@pytest.mark.parametrize("changes", [
    {"writer_incarnation": "replacement"},
    {"opaque": None},
    {"opaque": {}},
    {"observation": {"new": "unknown"}},
])
def test_patch_cannot_drop_or_add_unowned_fields(changes):
    original = fact()
    assert_reason(
        "unknown_patch_field",
        lambda: patch_payload(original, changes, supported=WRITE, known_paths=KNOWN),
    )
    assert original == fact()


@pytest.mark.parametrize("replacement", [None, "erased", [], [1]])
def test_patch_cannot_erase_unknown_descendants(replacement):
    assert_reason(
        "incompatible_patch_shape",
        lambda: patch_payload(
            fact(), {"observation": replacement}, supported=WRITE,
            known_paths=frozenset({("observation",)}),
        ),
    )


def roots(tmp_path):
    worktree, common = tmp_path / "checkout", tmp_path / "git-common"
    worktree.mkdir()
    common.mkdir()
    return worktree, common


def inspect(worktree, common, **options):
    return inspect_layout(
        worktree, common, repository_id=REPOSITORY, resource_id=RESOURCE,
        marker_ignored=options.get("marker_ignored", True),
    )


def seed_marker(directory: Path, value: dict) -> None:
    """Fixture-only persistence; no production writer is admitted in G1."""
    directory.mkdir()
    with (directory / "identity.json").open("xb") as stream:
        stream.write(wire(value))
        stream.flush()
        os.fsync(stream.fileno())


def test_preview_has_no_side_effects(tmp_path):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    assert not layout.marker_directory.exists()
    assert not layout.journal_directory.exists()
    assert list(worktree.iterdir()) == list(common.iterdir()) == []
    assert_reason("marker_not_ignored", lambda: inspect(worktree, common, marker_ignored=False))
    assert list(worktree.iterdir()) == list(common.iterdir()) == []


@pytest.mark.parametrize("at_journal", [False, True])
@pytest.mark.parametrize("foreign_kind", ["file", "empty_directory", "wrong_identity"])
def test_foreign_paths_are_never_overwritten(tmp_path, at_journal, foreign_kind):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    target = layout.journal_directory if at_journal else layout.marker_directory
    if foreign_kind == "file":
        target.write_bytes(b"user content")
    elif foreign_kind == "empty_directory":
        target.mkdir()
    else:
        seed_marker(target, {"schema": "foreign", "user": "content"})
    before = target.read_bytes() if target.is_file() else sorted(
        (child.name, child.read_bytes()) for child in target.iterdir()
    )
    reason = "identity_conflict" if foreign_kind == "wrong_identity" else "foreign_path_conflict"
    assert_reason(reason, lambda: inspect(worktree, common))
    after = target.read_bytes() if target.is_file() else sorted(
        (child.name, child.read_bytes()) for child in target.iterdir()
    )
    assert before == after


@pytest.mark.parametrize("required", [["future.marker"], {"read": ["future.marker"]}])
def test_future_required_marker_semantics_remain_unsupported(tmp_path, required):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    value = marker_value(REPOSITORY, RESOURCE)
    value["required_capabilities"] = required
    seed_marker(layout.marker_directory, value)
    assert_reason("required_capability_unsupported", lambda: inspect(worktree, common))


@pytest.mark.parametrize("version", [2, True, 1.0])
def test_marker_version_is_not_an_identity_conflict(tmp_path, version):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    value = marker_value(REPOSITORY, RESOURCE)
    value["version"] = version
    seed_marker(layout.marker_directory, value)
    assert_reason("unsupported_schema", lambda: inspect(worktree, common))


@pytest.mark.parametrize("operation", ["lstat", "open"])
@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_marker_io_failure_is_unavailable_not_corrupt(tmp_path, monkeypatch, operation, error):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    seed_marker(layout.marker_directory, marker_value(REPOSITORY, RESOURCE))
    marker = layout.marker_directory / "identity.json"
    original = getattr(Path, operation)
    def unavailable(path, *args, **kwargs):
        if path == marker:
            raise error("fixture-only unavailable source")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, operation, unavailable)
    assert_reason("source_unavailable", lambda: inspect(worktree, common))


@pytest.mark.parametrize(("raw", "reason"), [
    (b"{", "malformed_marker"),
    (b"[" * 1000 + b"]" * 1000, "record_limit"),
])
def test_marker_malformed_and_deep_input_are_scoped(tmp_path, raw, reason):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    seed_marker(layout.marker_directory, marker_value(REPOSITORY, RESOURCE))
    (layout.marker_directory / "identity.json").write_bytes(raw)
    assert_reason(reason, lambda: inspect(worktree, common))


@pytest.mark.parametrize(("number", "reason"), [
    (b"1e999", "malformed_json"), (b"-1e999", "malformed_json"),
    (b"1e-999", "number_precision_unsupported"),
    (b"9007199254740993.0", "number_precision_unsupported"),
])
def test_marker_rejects_lossy_optional_numbers(tmp_path, number, reason):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    seed_marker(layout.marker_directory, marker_value(REPOSITORY, RESOURCE))
    marker = layout.marker_directory / "identity.json"
    raw = marker.read_bytes()[:-1] + b',"future_numeric":' + number + b"}"
    marker.write_bytes(raw)
    assert_reason(reason, lambda: inspect(worktree, common))
    assert marker.read_bytes() == raw


def test_relocation_and_backup_preserve_one_journal_authority(tmp_path):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    seed_marker(layout.marker_directory, marker_value(REPOSITORY, RESOURCE))
    seed_marker(layout.journal_directory, marker_value(REPOSITORY))
    record = layout.journal_directory / f"{OPERATION}.json"
    encoded = encode_fact(fact(), supported=READ)
    with record.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    marker_before = (layout.marker_directory / "identity.json").read_bytes()
    # Both protected roots must travel in a backup; copying a marker is not
    # reconstructing a lost journal, nor does a copy elect an active writer.
    backup = tmp_path / "backup"
    shutil.copytree(common, backup / "common")
    shutil.copytree(worktree, backup / "worktree")
    moved = tmp_path / "moved-worktree"
    worktree.rename(moved)
    relocated = inspect(moved, common)
    restored = inspect(backup / "worktree", backup / "common")
    for result in (relocated, restored):
        assert (result.marker_directory / "identity.json").read_bytes() == marker_before
        assert (result.journal_directory / record.name).read_bytes() == encoded
        assert decode_fact((result.journal_directory / record.name).read_bytes(), supported=READ) == fact()
    assert list(layout.marker_directory.parent.glob("*.json")) == []
    assert "writer_incarnation" not in json.loads(marker_before)


def test_partial_fixture_fact_is_not_an_acknowledged_transition(tmp_path):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    seed_marker(layout.journal_directory, marker_value(REPOSITORY))
    complete = layout.journal_directory / f"{OPERATION}.json"
    complete.write_bytes(encode_fact(fact(), supported=READ))
    interrupted = layout.journal_directory / "interrupted.part"
    interrupted.write_bytes(b'{"schema":"agent-worktrees.recovery-fact"')
    before = complete.read_bytes()
    assert_reason("malformed_json", lambda: decode_fact(interrupted.read_bytes(), supported=READ))
    assert complete.read_bytes() == before
    assert interrupted.exists()  # Reader does not repair/delete unknown files.


def test_marker_optional_fields_and_foreign_files_are_preserved(tmp_path):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    value = marker_value(REPOSITORY, RESOURCE)
    value["future_optional"] = {"keep": ["unknown"]}
    seed_marker(layout.marker_directory, value)
    foreign = layout.marker_directory / "user-file"
    foreign.write_bytes(b"do not remove")
    marker = layout.marker_directory / "identity.json"
    before = marker.read_bytes()
    assert inspect(worktree, common) == layout
    assert marker.read_bytes() == before
    assert foreign.read_bytes() == b"do not remove"


def test_link_or_reparse_ancestor_is_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace
    worktree, common = roots(tmp_path)
    original = Path.lstat
    def observed(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == common:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", observed)
    assert_reason("path_indirection", lambda: inspect(worktree, common))
    assert not (common / "agent-worktrees-recovery").exists()


@pytest.mark.parametrize("historical_count", [0, 100, 1000])
def test_layout_reads_are_independent_of_historical_cardinality(tmp_path, monkeypatch, historical_count):
    worktree, common = roots(tmp_path)
    layout = inspect(worktree, common)
    seed_marker(layout.marker_directory, marker_value(REPOSITORY, RESOURCE))
    seed_marker(layout.journal_directory, marker_value(REPOSITORY))
    for index in range(historical_count):
        (layout.journal_directory / f"history-{index}.json").write_bytes(b"opaque")
    def forbidden(*_args, **_kwargs):
        pytest.fail("ordinary placement must not enumerate or replay history")
    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    opened = []
    original = Path.open
    def observed(path, *args, **kwargs):
        opened.append(path.name)
        assert path.name == "identity.json"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    assert inspect(worktree, common) == layout
    assert opened == ["identity.json", "identity.json"]
