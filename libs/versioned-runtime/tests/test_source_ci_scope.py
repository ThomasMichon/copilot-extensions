from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_source_runtime_changes_select_bounded_units_not_required_integration() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    planning = jobs["unit-plan"]
    testing = jobs["unit-tests"]
    assert planning["timeout-minutes"] <= 2
    assert testing["timeout-minutes"] <= 5
    detector = next(step for step in planning["steps"] if step.get("id") == "impact")
    execution = next(
        step for step in testing["steps"]
        if step.get("name") == "Execute only the positively admitted unit packet once"
    )
    assert "from promotion_unit_contract import changed_paths, requires_units" in detector["run"]
    assert "needed = requires_units(paths)" in detector["run"]
    spec = importlib.util.spec_from_file_location(
        "source_ci_unit_contract", ROOT / "tools" / "promotion_unit_contract.py",
    )
    assert spec is not None and spec.loader is not None
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    for path in (
        "libs/versioned-runtime/versioned_runtime_source.py",
        "libs/versioned-runtime/versioned_runtime_publication.py",
        "libs/versioned-runtime/tests/test_prepare_runtime_source.py",
        "tools/prepare_runtime_source.py",
        "tools/materialize_main.py",
        "tools/nested_uv_editable_ref.py",
        "plugins/agent-bridge/scripts/install.ps1",
        "plugins/agent-bridge/scripts/install.sh",
        "tools/clean-room/scenarios/merged-dev-runtime/scenario.sh",
        "tools/clean-room/scenarios/merged-dev-runtime/manifest.json",
        ".github/workflows/ci.yml",
        "plugins/agent-bridge/src/agent_bridge/sessions.py",
    ):
        assert contract.requires_units([path]) is True, path
    assert contract.requires_units(["docs/README.md"]) is False
    assert testing["if"] == "needs.unit-plan.outputs.run == 'true'"
    assert execution["run"] == (
        'python tools/run-promotion-unit-tests.py --source-sha "$GITHUB_SHA" '
        '--output-dir "$RUNNER_TEMP/promotion-unit-evidence"'
    )
    assert "test-runner-linux" not in jobs and "test-runner-windows" not in jobs
    assert "run-plugin-tests.py runtime-source" not in WORKFLOW.read_text(encoding="utf-8")
