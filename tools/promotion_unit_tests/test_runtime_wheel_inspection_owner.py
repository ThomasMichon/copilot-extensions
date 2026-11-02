"""One byte/semantic inspector, with promotion's independent source binding."""
from __future__ import annotations

import io

import pytest

import promotion_python_artifacts as promotion
import runtime_profile_wheels as profiles
import runtime_wheel_inspection as inspection
from wheel_input_fixture import wheel_bytes


def test_runtime_and_promotion_use_one_canonical_semantic_inspector(monkeypatch):
    assert profiles.inspect_wheel is inspection.inspect_wheel
    assert promotion.inspect_wheel_facts is inspection.inspect_wheel_facts
    actual = inspection.inspect_archive
    observed = []

    def inspect(*args, **options):
        facts = actual(*args, **options)
        observed.append(facts)
        return facts

    monkeypatch.setattr(inspection, "inspect_archive", inspect)
    wheel = {"filename": "demo_pkg-1.0-py3-none-any.whl"}
    data = wheel_bytes()
    runtime_metadata, runtime_size = inspection.inspect_wheel(io.BytesIO(data), wheel)
    promotion_metadata, promotion_size = promotion.inspect_wheel(io.BytesIO(data), wheel)
    assert len(observed) == 2
    assert runtime_metadata.items() == promotion_metadata.items()
    assert runtime_size == promotion_size == observed[0].unpacked_size


def test_promotion_still_binds_inspected_members_to_actual_source(tmp_path, monkeypatch):
    data = wheel_bytes(wheel=(
        b"Wheel-Version: 1.0\nGenerator: locked\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n\n"
    ))
    seen = []

    def validate(bundle, hashes, **options):
        seen.append((bundle.read("demo_pkg/__init__.py"), hashes, options))
        raise promotion.PromotionArtifactError("source member changed")

    monkeypatch.setattr(promotion, "validate_wheel_sources", validate)
    with pytest.raises(promotion.PromotionArtifactError, match="source member changed"):
        promotion.inspect_wheel(
            io.BytesIO(data), {"filename": "demo_pkg-1.0-py3-none-any.whl"},
            expected_generator="locked", source_dir=tmp_path,
            component="fixture", candidate_root=tmp_path,
        )
    assert len(seen) == 1
    assert seen[0][0] == b"VALUE = 1\n"
    assert "demo_pkg/__init__.py" in seen[0][1]
    assert seen[0][2]["prefix"] == "demo_pkg-1.0.dist-info/"
    assert seen[0][2]["source_dir"] == seen[0][2]["candidate_root"] == tmp_path
    assert seen[0][2]["component"] == "fixture"
    assert seen[0][2]["metadata"]["Name"] == "demo-pkg"


@pytest.mark.parametrize("owner", [inspection.inspect_wheel, promotion.inspect_wheel])
@pytest.mark.parametrize("mutation", ["generator", "record", "tags", "bytes", "deadline"])
def test_both_owners_preserve_byte_and_metadata_refusals(owner, mutation):
    options = {"expected_generator": "locked"}
    values = {"wheel": (
        b"Wheel-Version: 1.0\nGenerator: locked\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n\n"
    )}
    if mutation == "generator":
        options["expected_generator"] = "another"
    elif mutation == "record":
        values["record_mutator"] = lambda rows: rows[0].__setitem__(1, "sha256=invalid")
    elif mutation == "tags":
        values["wheel"] = values["wheel"].replace(b"py3-none-any", b"cp311-none-any")
    elif mutation == "deadline":
        options["deadline"] = 0
    data = b"invalid wheel" if mutation == "bytes" else wheel_bytes(**values)
    with pytest.raises(inspection.PromotionArtifactError):
        owner(io.BytesIO(data), {"filename": "demo_pkg-1.0-py3-none-any.whl"}, **options)
