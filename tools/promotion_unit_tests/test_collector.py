"""In-process collector regression with one controlled, no-production-call case."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from promotion_unit_contract import PROFILE, UnitEvidenceError
from promotion_unit_packet import verify_packet

SPEC = importlib.util.spec_from_file_location(
    "promotion_unit_runner", Path(__file__).resolve().parents[1] / "run-promotion-unit-tests.py",
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_no_production_call_is_rejected_by_the_collector_and_packet_verifier(tmp_path, monkeypatch):
    root = tmp_path / "source"
    tests = root / "tools/promotion_unit_tests"
    tests.mkdir(parents=True)
    business = root / "tools/noop_business.py"
    business.write_text("VALUE = 1\n", encoding="utf-8")
    test = tests / "test_collector_noop_case.py"
    test.write_text(
        "import importlib.util\n"
        "from pathlib import Path\n"
        "import sys\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'unit_collector_noop_business', Path(__file__).resolve().parents[1] / 'noop_business.py')\n"
        "business = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = business\n"
        "spec.loader.exec_module(business)\n"
        "def test_no_production_call():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    sources = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (test, business)
    }
    node = test.relative_to(root).as_posix() + "::test_no_production_call"
    manifest = {
        "profile": PROFILE, "nodes": [node], "sources": sources,
        "coverage_targets": [business.relative_to(root).as_posix()], "max_seconds": 300,
    }
    manifest_path = root / "tools/promotion-unit-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "packet"
    monkeypatch.setattr(RUNNER, "ROOT", root)
    monkeypatch.setattr(sys, "argv", [
        "run-promotion-unit-tests.py", "--source-sha", "a" * 40,
        "--manifest", str(manifest_path), "--output-dir", str(output),
    ])
    real_pytest_main = pytest.main

    def scoped_collection(arguments, **kwargs):
        assert Path(arguments[arguments.index("--confcutdir") + 1]) == root
        return real_pytest_main(arguments, **kwargs)

    monkeypatch.setattr(pytest, "main", scoped_collection)
    with pytest.raises(UnitEvidenceError, match="production call coverage"):
        RUNNER.main()
    with pytest.raises(UnitEvidenceError, match="production call coverage"):
        verify_packet(
            output, expected_source_sha="a" * 40, expected_manifest=manifest, source_root=root,
        )
    proof = json.loads((output / "unit-proof.json").read_text(encoding="utf-8"))
    assert all(report["outcome"] == "passed" for report in proof["reports"])
