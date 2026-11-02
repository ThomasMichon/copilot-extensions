"""The explicit marketplace provider has opt-in native qualification only."""
from pathlib import Path

import yaml


def test_marketplace_source_qualification_is_native_partitioned_and_opt_in():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "installation-context-acceptance.yml").read_text(),
    )
    for platform, runner in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        job = workflow["jobs"][f"marketplace-source-{platform}"]
        assert job["if"] == "${{ inputs.dependency_compile }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 6
        assert not job.get("continue-on-error")
        assert job["strategy"]["fail-fast"] is False
        partitions = job["strategy"]["matrix"]["include"]
        assert [item["suite"] for item in partitions] == [
            "provider-selection", "provider-mutation",
            "inventory-cache", "inventory-selection", "inventory-refusal",
        ]
        assert all(item["test"] == "tools/test_runtime_marketplace_source.py" for item in partitions[:2])
        assert partitions[0]["expression"] == "not " + partitions[1]["expression"]
        assert all(
            item["test"] == "libs/plugin-resolve/tests/test_native_git_inventory.py"
            for item in partitions[2:]
        )
        refusal = partitions[-1]["expression"]
        assert partitions[2]["expression"] == f"native_git_cache and not ({refusal})"
        assert partitions[3]["expression"] == f"not native_git_cache and not ({refusal})"
        assert job["steps"][-1]["run"] == (
            'python -m pytest -q ${{ matrix.test }} -k "${{ matrix.expression }}"'
        )
