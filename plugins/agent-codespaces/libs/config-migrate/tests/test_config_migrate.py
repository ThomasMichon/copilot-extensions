"""Unit tests for config_migrate.

Covers the safety model B4 commits to: idempotency, atomicity (interrupted
write), backup/rollback, fail-closed-on-newer, formatting-preserving baseline
stamp, and the backward-compatibility invariant via prior-version fixtures
(``v_{cur-1}`` / ``v_{cur-2}`` migrate cleanly to current and load).
"""

from __future__ import annotations

import os

import pytest
import yaml

from config_migrate import (
    SCHEMA_VERSION_KEY,
    ManagedFile,
    MigrationError,
    NewerThanCurrentError,
    SchemaRegistry,
    migrate_doc,
    migrate_file,
    read_version,
    run,
)
from config_migrate.registry import MigratorGapError, SchemaError, UnknownSchemaError


# --------------------------------------------------------------------------- #
# Registry validation
# --------------------------------------------------------------------------- #
def test_registry_rejects_migrator_gap():
    reg = SchemaRegistry()
    with pytest.raises(MigratorGapError):
        # current=3 needs migrators for v1 and v2; only v1 supplied.
        reg.register("x/y", current_version=3, migrators={1: lambda d: d})


def test_registry_rejects_bad_baseline():
    reg = SchemaRegistry()
    with pytest.raises(SchemaError):
        reg.register("x/y", current_version=1, baseline_version=2)


def test_get_unknown_schema_raises():
    reg = SchemaRegistry()
    with pytest.raises(UnknownSchemaError):
        reg.get("nope")


# --------------------------------------------------------------------------- #
# read_version
# --------------------------------------------------------------------------- #
def test_read_version_absent_is_baseline():
    assert read_version({}, baseline=1) == 1
    assert read_version({}, baseline=3) == 3


def test_read_version_present():
    assert read_version({SCHEMA_VERSION_KEY: 4}) == 4


@pytest.mark.parametrize("bad", [0, -1, "2", 1.5, True])
def test_read_version_invalid_marker(bad):
    with pytest.raises(MigrationError):
        read_version({SCHEMA_VERSION_KEY: bad})


# --------------------------------------------------------------------------- #
# migrate_doc
# --------------------------------------------------------------------------- #
def _baseline_reg() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register("aw/config", current_version=1)
    return reg


def test_migrate_doc_baseline_stamps_unmarked():
    reg = _baseline_reg()
    new, changed = migrate_doc({"a": 1}, "aw/config", reg)
    assert changed is True  # marker was absent
    assert new[SCHEMA_VERSION_KEY] == 1
    assert new["a"] == 1


def test_migrate_doc_idempotent_when_current_and_marked():
    reg = _baseline_reg()
    new, changed = migrate_doc({SCHEMA_VERSION_KEY: 1, "a": 1}, "aw/config", reg)
    assert changed is False
    assert new[SCHEMA_VERSION_KEY] == 1


def test_migrate_doc_does_not_mutate_input():
    reg = _baseline_reg()
    src = {"a": 1}
    migrate_doc(src, "aw/config", reg)
    assert SCHEMA_VERSION_KEY not in src


def test_migrate_doc_multistep():
    reg = SchemaRegistry()

    def v1_to_v2(d: dict) -> dict:
        d["b"] = d.pop("a", None)
        return d

    def v2_to_v3(d: dict) -> dict:
        d["c"] = (d.get("b") or 0) + 1
        return d

    reg.register("m/c", current_version=3, migrators={1: v1_to_v2, 2: v2_to_v3})
    new, changed = migrate_doc({"a": 10}, "m/c", reg)
    assert changed is True
    assert new[SCHEMA_VERSION_KEY] == 3
    assert "a" not in new and new["b"] == 10 and new["c"] == 11


def test_migrate_doc_fail_closed_on_newer():
    reg = _baseline_reg()
    with pytest.raises(NewerThanCurrentError):
        migrate_doc({SCHEMA_VERSION_KEY: 2}, "aw/config", reg)


def test_migrate_doc_non_dict_root():
    reg = _baseline_reg()
    with pytest.raises(MigrationError):
        migrate_doc(["not", "a", "map"], "aw/config", reg)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# migrate_file
# --------------------------------------------------------------------------- #
def test_migrate_file_missing_is_skipped(tmp_path):
    reg = _baseline_reg()
    res = migrate_file(tmp_path / "absent.yaml", "aw/config", reg)
    assert res.skipped and not res.changed


def test_migrate_file_baseline_stamp_preserves_comments(tmp_path):
    reg = _baseline_reg()
    p = tmp_path / "config.yaml"
    p.write_text("# a header comment\n\nfoo: bar\nnested:\n  x: 1\n", encoding="utf-8")
    res = migrate_file(p, "aw/config", reg)
    assert res.changed
    text = p.read_text(encoding="utf-8")
    # Comment survives; marker inserted after the comment block; body intact.
    assert "# a header comment" in text
    assert f"{SCHEMA_VERSION_KEY}: 1" in text
    assert "foo: bar" in text and "  x: 1" in text
    loaded = yaml.safe_load(text)
    assert loaded[SCHEMA_VERSION_KEY] == 1 and loaded["foo"] == "bar"


def test_migrate_file_baseline_stamp_summary_wording(tmp_path):
    reg = _baseline_reg()
    p = tmp_path / "config.yaml"
    p.write_text("foo: bar\n", encoding="utf-8")
    res = migrate_file(p, "aw/config", reg)
    assert res.summary().endswith("stamped v1")


def test_migrate_file_idempotent_second_run(tmp_path):
    reg = _baseline_reg()
    p = tmp_path / "config.yaml"
    p.write_text("foo: bar\n", encoding="utf-8")
    first = migrate_file(p, "aw/config", reg)
    after_first = p.read_text(encoding="utf-8")
    second = migrate_file(p, "aw/config", reg)
    assert first.changed and not second.changed
    assert p.read_text(encoding="utf-8") == after_first  # byte-identical no-op


def test_migrate_file_creates_backup(tmp_path):
    reg = _baseline_reg()
    p = tmp_path / "config.yaml"
    original = "foo: bar\n"
    p.write_text(original, encoding="utf-8")
    migrate_file(p, "aw/config", reg)
    bak = p.with_name(p.name + ".bak")
    assert bak.exists() and bak.read_text(encoding="utf-8") == original


def test_migrate_file_shape_change_reserializes(tmp_path):
    reg = SchemaRegistry()

    def v1_to_v2(d: dict) -> dict:
        d["renamed"] = d.pop("old", None)
        return d

    reg.register("m/c", current_version=2, migrators={1: v1_to_v2})
    p = tmp_path / "config.yaml"
    p.write_text("old: 42\n", encoding="utf-8")
    res = migrate_file(p, "m/c", reg)
    assert res.changed and res.to_version == 2
    loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert loaded[SCHEMA_VERSION_KEY] == 2
    assert "old" not in loaded and loaded["renamed"] == 42


def test_migrate_file_fail_closed_on_newer(tmp_path):
    reg = _baseline_reg()
    p = tmp_path / "config.yaml"
    p.write_text(f"{SCHEMA_VERSION_KEY}: 5\nfoo: bar\n", encoding="utf-8")
    with pytest.raises(NewerThanCurrentError):
        migrate_file(p, "aw/config", reg)


def test_migrate_file_malformed_yaml_raises(tmp_path):
    reg = _baseline_reg()
    p = tmp_path / "config.yaml"
    p.write_text("foo: : : bad\n  - broken", encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate_file(p, "aw/config", reg)


def test_migrate_file_atomic_interrupted_write_preserves_original(tmp_path, monkeypatch):
    """If the final rename fails, the original file is untouched and no temp leaks."""
    reg = SchemaRegistry()
    reg.register("m/c", current_version=2, migrators={1: lambda d: {**d, "added": True}})
    p = tmp_path / "config.yaml"
    original = "old: 1\n"
    p.write_text(original, encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        migrate_file(p, "m/c", reg)

    # Original intact; no leftover temp files in the directory.
    assert p.read_text(encoding="utf-8") == original
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == []


# --------------------------------------------------------------------------- #
# run (batch)
# --------------------------------------------------------------------------- #
def test_run_batch_reports_per_file(tmp_path):
    reg = _baseline_reg()
    good = tmp_path / "good.yaml"
    good.write_text("foo: bar\n", encoding="utf-8")
    newer = tmp_path / "newer.yaml"
    newer.write_text(f"{SCHEMA_VERSION_KEY}: 9\n", encoding="utf-8")
    missing = tmp_path / "missing.yaml"

    results = run(
        [
            ManagedFile(good, "aw/config"),
            ManagedFile(newer, "aw/config"),
            ManagedFile(missing, "aw/config"),
        ],
        reg,
    )
    by_name = {r.path.name: r for r in results}
    assert by_name["good.yaml"].changed is True
    assert by_name["newer.yaml"].skipped is True  # fail-closed captured, not raised
    assert by_name["missing.yaml"].skipped is True
    # A newer file in a batch does not corrupt the good one.
    assert good.read_text(encoding="utf-8").startswith(f"{SCHEMA_VERSION_KEY}: 1")


# --------------------------------------------------------------------------- #
# Backward-compatibility invariant -- prior-version fixtures
# --------------------------------------------------------------------------- #
def _windowed_registry() -> SchemaRegistry:
    """A schema at current v3 with a full v1->v2->v3 chain (the supported window)."""
    reg = SchemaRegistry()

    def v1_to_v2(d: dict) -> dict:
        # v2 renamed ``name`` -> ``title``.
        d["title"] = d.pop("name", None)
        return d

    def v2_to_v3(d: dict) -> dict:
        # v3 introduced ``enabled`` defaulting to True.
        d.setdefault("enabled", True)
        return d

    reg.register("demo/config", current_version=3, migrators={1: v1_to_v2, 2: v2_to_v3})
    return reg


# Checked-in prior-version fixtures: a config a version or two back must migrate
# cleanly to current and load. A schema change that breaks one of these fails
# here -- the guardrail against *accidental* backward-incompatibility.
_FIXTURES = {
    "v_cur-2": {SCHEMA_VERSION_KEY: 1, "name": "widget", "count": 2},
    "v_cur-1": {SCHEMA_VERSION_KEY: 2, "title": "widget", "count": 2},
}


@pytest.mark.parametrize("label", sorted(_FIXTURES))
def test_prior_version_fixtures_migrate_to_current(tmp_path, label):
    reg = _windowed_registry()
    p = tmp_path / f"{label}.yaml"
    p.write_text(yaml.safe_dump(_FIXTURES[label]), encoding="utf-8")

    res = migrate_file(p, "demo/config", reg)
    assert res.changed and res.to_version == 3

    loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert loaded[SCHEMA_VERSION_KEY] == 3
    assert loaded["title"] == "widget"  # survived the v1->v2 rename
    assert loaded["enabled"] is True  # added by v2->v3
    assert loaded["count"] == 2  # untouched field preserved

    # And it is now idempotent (already current).
    again = migrate_file(p, "demo/config", reg)
    assert not again.changed
