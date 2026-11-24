"""In-process collector regression with one controlled, no-production-call case."""

import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast
from warnings import WarningMessage

import coverage
import pytest

from promotion_unit_contract import PROFILE, UnitEvidenceError, windows_native_manifest
from promotion_unit_effects import UnitRun
from promotion_unit_packet import verify_packet

SPEC = importlib.util.spec_from_file_location(
    "promotion_unit_runner", Path(__file__).resolve().parents[1] / "run-promotion-unit-tests.py",
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_framework_io_bootstrap_selects_only_the_windows_adapter(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    imported: list[str] = []
    monkeypatch.setattr(RUNNER, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(RUNNER, "importlib", SimpleNamespace(import_module=imported.append))
    RUNNER.prepare_framework_io()
    assert imported == (["colorama"] if platform == "win32" else [])


def test_framework_io_bootstrap_does_not_hide_a_missing_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(name: str) -> None:
        raise ImportError("framework adapter unavailable")

    monkeypatch.setattr(RUNNER, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(RUNNER, "importlib", SimpleNamespace(import_module=unavailable))
    with pytest.raises(ImportError, match="framework adapter unavailable"):
        RUNNER.prepare_framework_io()


def test_collection_selects_exact_nodes_in_admitted_order(tmp_path: Path) -> None:
    nodes = ["test_units.py::test_second[value]", "test_units.py::test_first"]
    run = UnitRun(coverage.Coverage(data_file=None, config_file=False), tmp_path, {}, tmp_path, nodes=nodes)
    items = [SimpleNamespace(nodeid=node) for node in [
        nodes[1], "test_units.py::test_second[other]", nodes[0], "test_units.py::test_unadmitted",
    ]]
    excluded = [items[1], items[3]]
    calls = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda **kw: calls.append(kw)))
    session = SimpleNamespace(items=items)
    run.pytest_collection_modifyitems(
        cast(pytest.Session, session), cast(pytest.Config, config), cast(list[pytest.Item], items),
    )
    run.pytest_collection_finish(cast(pytest.Session, session))
    assert run.collected == nodes
    assert calls == [{"items": excluded}]


@pytest.mark.parametrize("observed", [[], ["admitted", "admitted"], ["unadmitted"]],
                         ids=["missing", "duplicate", "unadmitted-only"])
def test_collection_refuses_missing_or_duplicate_admitted_nodes(tmp_path: Path, observed: list[str]) -> None:
    run = UnitRun(coverage.Coverage(data_file=None, config_file=False), tmp_path, {}, tmp_path, nodes=["admitted"])
    items = [SimpleNamespace(nodeid=node) for node in observed]
    with pytest.raises(UnitEvidenceError, match="exact admitted inventory"):
        run.pytest_collection_modifyitems(
            cast(pytest.Session, None), cast(pytest.Config, None), cast(list[pytest.Item], items),
        )
    assert [item.nodeid for item in items] == observed
    assert run.collected == []


def test_no_production_call_is_rejected_by_the_collector_and_packet_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    tests = root / "tools/promotion_unit_tests"
    tests.mkdir(parents=True)
    business = root / "tools/noop_business.py"
    business.write_text("VALUE = 1\n", encoding="utf-8")
    bytecode = tmp_path / "bytecode"
    with monkeypatch.context() as cache:
        cache.setattr(sys, "pycache_prefix", str(bytecode))
        cache.setattr(sys, "dont_write_bytecode", True)
        RUNNER.prepare_source_bytecode(root, {"tools/noop_business.py": "fixture"}, bytecode)
        assert Path(importlib.util.cache_from_source(str(business))).is_file()
        original = business.stat()
        business.write_text("VALUE = 2\n", encoding="utf-8")
        os.utime(business, ns=(original.st_atime_ns, original.st_mtime_ns))
        spec = importlib.util.spec_from_file_location("unit_collector_cache_probe", business)
        assert spec and spec.loader
        namespace: dict[str, Any] = {}
        assert isinstance(spec.loader, SourceFileLoader)
        code = spec.loader.get_code(spec.name)
        assert code is not None
        exec(code, namespace)
        assert namespace["VALUE"] == 2
    test = tests / "test_collector_noop_case.py"
    test.write_text(
        "import importlib.util\n"
        "from pathlib import Path\n"
        "import sys\n"
        "import pytest\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'unit_collector_noop_business', Path(__file__).resolve().parents[1] / 'noop_business.py')\n"
        "business = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = business\n"
        "spec.loader.exec_module(business)\n"
        "@pytest.mark.portfolio_tier('T0')\n"
        "@pytest.mark.effect('filesystem')\n"
        "@pytest.mark.coverage_auditability('auditable', dependencies=())\n"
        "@pytest.mark.contract('synthetic.collector')\n"
        "@pytest.mark.parametrize('case', ['x' * 120])\n"
        "def test_no_production_call(case):\n"
        "    assert True\n"
        "def test_second_no_production_call():\n"
        "    assert True\n"
        "def test_not_admitted():\n"
        "    raise AssertionError('unadmitted test executed')\n",
        encoding="utf-8",
    )
    (tests / "test_unadmitted_file.py").write_text(
        "raise AssertionError('unadmitted file imported')\n", encoding="utf-8",
    )
    first = tests / "test_z_first.py"
    first.write_text(
        "import sys\n"
        "assert 'unit_collector_noop_business' not in sys.modules\n"
        "def test_first():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    sources = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (test, first, business)
    }
    node = test.relative_to(root).as_posix() + "::test_no_production_call[" + "x" * 120 + "]"
    manifest: dict[str, Any] = {
        "profile": PROFILE, "nodes": [first.relative_to(root).as_posix() + "::test_first",
                                    node, test.relative_to(root).as_posix()
                                    + "::test_second_no_production_call"], "sources": sources,
        "coverage_targets": [business.relative_to(root).as_posix()], "max_seconds": 300,
    }
    manifest_path = root / "tools/promotion-unit-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "packet"
    monkeypatch.setattr(RUNNER, "ROOT", root)
    def fixture_helpers(
        selected_root: Path, selected_manifest: dict[str, Any], *, development_root: Path | None,
    ) -> set[str]:
        assert selected_root == root and selected_manifest == manifest
        assert development_root is None
        return set()

    monkeypatch.setattr(RUNNER, "verify_helper_contract", fixture_helpers)
    monkeypatch.setattr(sys, "argv", [
        "run-promotion-unit-tests.py", "--source-sha", "a" * 40,
        "--manifest", str(manifest_path), "--output-dir", str(output),
        *(["--windows-native"] if sys.platform == "win32" else []),
    ])
    real_pytest_main = pytest.main
    observed_warnings: list[str] = []

    class WarningObserver:
        def pytest_warning_recorded(self, warning_message: WarningMessage) -> None:
            observed_warnings.append(str(warning_message.message))

    def scoped_collection(arguments: list[str], **kwargs: Any) -> int:
        assert Path(arguments[arguments.index("--confcutdir") + 1]) == root
        assert arguments[arguments.index("-o") + 1] == "parametrize_long_str_id_strategy=legacy"
        assert arguments.count(str(tests)) == 1
        assert str(test) not in arguments and str(first) not in arguments
        assert not any("::" in argument for argument in arguments)
        kwargs["plugins"].append(WarningObserver())
        return real_pytest_main(arguments, **kwargs)

    monkeypatch.setattr(pytest, "main", scoped_collection)
    with pytest.raises(UnitEvidenceError, match="production call coverage") as rejected:
        RUNNER.main()
    assert "missing=3" in str(rejected.value)
    assert all(node in str(rejected.value) for node in manifest["nodes"])
    assert "omitted=0" in str(rejected.value)
    assert not any("Unknown pytest.mark." in message for message in observed_warnings)
    with pytest.raises(UnitEvidenceError, match="production call coverage"):
        verify_packet(
            output, expected_source_sha="a" * 40,
            expected_manifest=(
                windows_native_manifest(manifest, platform=sys.platform)
                if sys.platform == "win32" else manifest
            ),
            source_root=root, allow_windows_native=sys.platform == "win32",
        )
    proof = json.loads((output / "unit-proof.json").read_text(encoding="utf-8"))
    assert proof["collected"] == manifest["nodes"]
    assert all(report["outcome"] == "passed" for report in proof["reports"])
