"""Raw-source proof cannot authorize a later stamped composition."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import candidate_qualification_receipt as receipt
from promotion_unit_contract import PROFILE, admit_manifest

VERSION_SOURCE = "services/example/src/example/__init__.py"
NODE = "tools/promotion_unit_tests/test_example.py::test_example"
ASSIGNMENTS = (
    pytest.param("__version__", id="plain"),
    pytest.param("__version__: str", id="typed"),
    pytest.param("_FALLBACK_VERSION", id="fallback"),
)


def _inputs(
    directory: Path, original: bytes, transformed: bytes,
) -> tuple[Path, Path, dict]:
    source = directory / "source"
    candidate = directory / "candidate"
    test_source = b"def test_example(): assert True\n"
    sources = {NODE.split("::")[0]: test_source, VERSION_SOURCE: original}
    manifest = admit_manifest({
        "profile": PROFILE, "nodes": [NODE],
        "sources": {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()},
        "coverage_targets": [VERSION_SOURCE], "max_seconds": 300,
    })
    for root in (source, candidate):
        for name, raw in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(transformed if root == candidate and name == VERSION_SOURCE else raw)
    return source, candidate, manifest


def _original(assignment: str) -> bytes:
    return f'{assignment} = "1.2.3-dev1"\n\ndef value():\n    return 7\n'.encode()


@pytest.mark.parametrize("assignment", ASSIGNMENTS)
def test_identical_version_source_preserves_legacy_equivalence(
    tmp_path: Path, assignment: str,
) -> None:
    original = _original(assignment)
    source, candidate, manifest = _inputs(tmp_path, original, original)
    assert receipt.input_equivalence(source, candidate, manifest) == receipt.canonical_digest(
        manifest["sources"],
    )


@pytest.mark.parametrize("assignment", ASSIGNMENTS)
@pytest.mark.parametrize("drift", ("version-only", "version-and-code", "line-endings"))
def test_legacy_proof_rejects_stamped_or_other_source_drift(
    tmp_path: Path, assignment: str, drift: str,
) -> None:
    original = _original(assignment)
    transformed = original.replace(b"1.2.3-dev1", b"1.2.4-dev1")
    if drift == "version-and-code":
        transformed = transformed.replace(b"return 7", b"return 8")
    elif drift == "line-endings":
        transformed = original.replace(b"\n", b"\r\n")
    source, candidate, manifest = _inputs(tmp_path, original, transformed)
    with pytest.raises(receipt.UnitQualificationError) as error:
        receipt.input_equivalence(source, candidate, manifest)
    assert str(error.value).endswith(VERSION_SOURCE)


@pytest.mark.parametrize("assignment", ASSIGNMENTS)
@pytest.mark.parametrize("rewrite", ("admitted-hash", "source-after-test"))
def test_relabelling_stamped_bytes_does_not_repair_legacy_evidence(
    tmp_path: Path, assignment: str, rewrite: str,
) -> None:
    original = _original(assignment)
    transformed = original.replace(b"1.2.3-dev1", b"1.2.4-dev1")
    source, candidate, manifest = _inputs(tmp_path, original, transformed)
    if rewrite == "admitted-hash":
        manifest["sources"][VERSION_SOURCE] = hashlib.sha256(transformed).hexdigest()
        manifest = admit_manifest(manifest)
    else:
        (source / VERSION_SOURCE).write_bytes(transformed)
    with pytest.raises(receipt.UnitQualificationError) as error:
        receipt.input_equivalence(source, candidate, manifest)
    assert str(error.value).endswith(VERSION_SOURCE)
