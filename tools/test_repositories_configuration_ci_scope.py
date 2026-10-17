"""Keep the real native Configuration consumer bounded, selected and required."""

from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
JOB = WORKFLOW["jobs"]["repositories-configuration"]
DETECTION = next(step for step in JOB["steps"] if step.get("id") == "repositories-configuration")
PATTERN = re.search(r"grep -Eq \\\n\s*'([^'\n]+)'", DETECTION["run"]).group(1)


def test_real_native_consumer_is_separate_bounded_and_required():
    assert JOB["timeout-minutes"] == 5
    assert JOB["runs-on"] == "ubuntu-latest"
    assert JOB["if"] == WORKFLOW["jobs"]["worktree-manager"]["if"]
    assert "repositories-configuration" in WORKFLOW["jobs"]["pr-gate"]["needs"]
    assert "repositories-configuration" in WORKFLOW["jobs"]["promotion-gate"]["needs"]
    assert WORKFLOW["jobs"]["worktree-manager"]["timeout-minutes"] == 6
    full = next(step for step in WORKFLOW["jobs"]["worktree-manager"]["steps"]
                if step.get("name") == "Test worktree-manager")
    assert full["run"] == "uv run --extra dev pytest -q"
    assert "REPOSITORIES_CONFIG_TEST_PYTHON" not in full.get("env", {})
    contract = next(step for step in JOB["steps"] if step.get("name") == "Repositories Configuration CI contract")
    assert "tools/seasoned_uv.py --uv uv -- --system pytest pyyaml" in contract["run"]
    owner = next(step for step in JOB["steps"] if step.get("id") == "repositories-owner")
    ui = next(step for step in JOB["steps"] if step.get("name") == "Test real Repositories Configuration UI")
    assert owner["if"] == ui["if"] == "steps.repositories-configuration.outputs.run == 'true'"
    assert ' -k "repos_configuration or repos_registry_transaction or registry_paths or picker_bootstrap"' in owner["run"]
    assert ".test-venvs/linux/agent-worktrees/bin/python" in owner["run"]
    assert ui["env"]["REPOSITORIES_CONFIG_TEST_PYTHON"] == "${{ steps.repositories-owner.outputs.python }}"
    assert ui["env"]["PYTEST_ADDOPTS"] == "-k repositories_configuration"
    assert "run-standalone-tests.py worktree-manager --prepare" in ui["run"]
    assert "--timeout 120" in ui["run"]
    assert "$BASE_SHA...$HEAD_SHA" in DETECTION["run"]


@pytest.mark.parametrize("path,selected", [
    (".github/workflows/ci.yml", True),
    ("tools/test_repositories_configuration_ci_scope.py", True),
    ("tools/run-plugin-tests.py", True),
    ("tools/run-standalone-tests.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/repos_configuration_catalog.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/repos_registry_transaction.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/related.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/config_dropins.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/inrepo_config_source.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/state_root.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/context_cli.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/project_state.py", True),
    ("plugins/agent-worktrees/src/agent_worktrees/tracking.py", True),
    ("plugins/agent-worktrees/tests/test_repos_configuration.py", True),
    ("plugins/agent-worktrees/tests/test_repos_registry_transaction.py", True),
    ("plugins/agent-worktrees/tests/test_registry_paths.py", True),
    ("plugins/agent-worktrees/tests/test_context_resolution.py", True),
    ("plugins/agent-worktrees/pivots/repositories-config.json", True),
    ("worktree-manager/src/worktree_manager/production_picker/picker_tui/configuration_preview.py", True),
    ("worktree-manager/src/worktree_manager/production_picker/picker_tui/tasks.py", True),
    ("worktree-manager/src/worktree_manager/production_picker/picker_tui/pivot_registry_materialization.py", True),
    ("worktree-manager/src/worktree_manager/production_picker/context.py", True),
    ("worktree-manager/tests/production_picker/test_repositories_configuration.py", True),
    ("plugins/agent-worktrees/README.md", False),
    ("plugins/other/src/repos.py", False),
    ("docs/architecture.md", False),
])
def test_native_configuration_change_scope(path, selected):
    assert bool(re.search(PATTERN, path)) is selected
