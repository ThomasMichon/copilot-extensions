"""Optional model APIs remain independent of budget-guidance."""

import json

import pytest

from test_model_routing import _config, _repo, _run, _write


def test_optional_model_lookup_does_not_inject_budget_advice(tmp_path):
    repo, home = _repo(tmp_path)
    _write(home / ".copilot/model-routing.json", _config({"coding": [{
        "model": "qualified", "state": "demonstrated", "surfaces": ["task"],
        "evidence": [{
            "ref": "https://example.com/result", "observedAt": "2030-01-01",
            "sampleCount": 3,
        }],
    }]}))
    result = _run(
        repo, home, "--purpose", "coding", "--surface", "task",
        "--available-model", "qualified", "--as-of", "2030-01-10",
    )
    decision = json.loads(result.stdout)["decision"]
    assert decision["model"] == "qualified"
    assert "budgetAdvice" not in decision


@pytest.mark.parametrize("flag", ["--budget-posture", "--budget-at"])
def test_retired_budget_routing_arguments_are_not_silently_accepted(tmp_path, flag):
    repo, home = _repo(tmp_path)
    result = _run(repo, home, flag, "unused", check=False)
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
