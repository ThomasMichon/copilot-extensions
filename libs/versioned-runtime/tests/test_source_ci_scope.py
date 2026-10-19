from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


@pytest.mark.parametrize("job", ["test-runner-linux", "test-runner-windows"])
def test_source_runtime_lane_is_bounded_and_change_scoped(job: str) -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][job]["steps"]
    detector = next(step for step in steps if step.get("id") == "source-runtime-contracts")
    execution = next(
        step for step in steps if step.get("name") == "Source runtime admission and native boundary"
    )
    pattern = re.search(r"grep -Eq \\\n\s*'([^']+)'", detector["run"])
    assert pattern is not None
    trigger = re.compile(pattern.group(1))
    for path in (
        "libs/versioned-runtime/versioned_runtime_source.py",
        "libs/versioned-runtime/tests/test_prepare_runtime_source.py",
        "tools/prepare_runtime_source.py",
        "tools/materialize_main.py",
        "tools/nested_uv_editable_ref.py",
        "plugins/agent-bridge/scripts/install.ps1",
        "plugins/agent-bridge/scripts/install.sh",
        "tools/clean-room/scenarios/merged-dev-runtime/scenario.sh",
        "tools/clean-room/scenarios/merged-dev-runtime/manifest.json",
        ".github/workflows/ci.yml",
    ):
        assert trigger.search(path), path
    for path in ("docs/README.md", "plugins/agent-bridge/src/agent_bridge/sessions.py"):
        assert trigger.search(path) is None, path
    assert execution["if"] == "steps.source-runtime-contracts.outputs.run == 'true'"
    assert execution["run"] == (
        "python tools/run-plugin-tests.py runtime-source --admission-wait 60 "
        "--plugin-timeout 180 --subsuite-timeout 180"
    )
