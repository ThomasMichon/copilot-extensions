"""The staged writer has no activation, head execution or credential authority."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXPECTED_WORKFLOW = """\
name: Maintainer PR completion

on:
  workflow_dispatch:
    inputs:
      pr:
        description: "Optional PR number for bounded manual recovery"
        required: false
        type: string

permissions:
  contents: read
  pull-requests: read
  issues: read
  checks: read

concurrency:
  group: maintainer-pr-completion-${{ github.repository }}
  cancel-in-progress: false

jobs:
  reconcile:
    # Authority, protected-environment and live-canary acceptance are separate.
    # Automatic events are wired only with activation, avoiding idle skipped runs.
    # Enabling requires a reviewed change; a writable repository variable is not consent.
    if: ${{ false }}
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check out trusted workflow tooling without credentials
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          ref: ${{ github.workflow_sha }}
          persist-credentials: false
      - name: Report only using the staged source policy
        env:
          GH_TOKEN: ${{ github.token }}
          TARGET_PR: ${{ inputs.pr }}
        run: |
          args=()
          if [ -n "$TARGET_PR" ]; then
            if [[ ! "$TARGET_PR" =~ ^[1-9][0-9]*$ ]]; then
              echo "::error::Invalid target PR number."
              exit 1
            fi
            args+=(--pr "$TARGET_PR")
          fi
          python tools/maintainer_pr_completion.py --report-only "${args[@]}"
"""


def test_entire_staged_workflow_has_only_the_reviewed_read_only_disabled_shape() -> None:
    actual = (ROOT / ".github" / "workflows" / "maintainer-pr-completion.yml").read_text(
        encoding="utf-8",
    )
    assert actual == EXPECTED_WORKFLOW
    assert "pull_request_review:" not in actual
    assert "pull_request_target:" not in actual
    assert "workflow_run:" not in actual and "schedule:" not in actual
    assert "secrets." not in actual


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return importlib.import_module("maintainer_pr_completion")


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"version": True}, {"mode": "automatic"}, {"base_ref": "main"},
    {"mode": "enabled"}, {"app_id": True}, {"app_id": 0},
    {"app_slug": "not an app"}, {"hold_labels": [""]}, {"unexpected": "field"},
])
def test_configuration_changes_cannot_silently_authorize_a_writer(
    controller, tmp_path, changes,
) -> None:
    original = json.loads(controller.CONFIG.read_text(encoding="utf-8"))
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**original, **changes}), encoding="utf-8")
    with pytest.raises(controller.EvidenceUnavailable):
        controller.settings(path)


def test_targeted_recovery_is_not_an_unbounded_sweep(controller) -> None:
    assert controller.candidates(None, 7, "auto-merge") == [7]
    with pytest.raises(controller.EvidenceUnavailable):
        controller.candidates(None, 0, "auto-merge")


def test_discovery_filters_by_consent_but_requires_complete_pagination(controller) -> None:
    class Client:
        repository = "example/project"

        def pages(self, endpoint):
            return [
                {"number": 1, "labels": []},
                {"number": 2, "labels": [{"name": "AUTO-MERGE"}]},
            ]

    assert controller.candidates(Client(), None, "auto-merge") == [2]


def test_catch_up_refuses_to_hide_overflow_behind_the_first_page(controller) -> None:
    class Client:
        repository = "example/project"

        def pages(self, endpoint):
            return [{"number": value, "labels": [{"name": "auto-merge"}]}
                    for value in range(1, 102)]

    with pytest.raises(controller.EvidenceUnavailable, match="targeted manual recovery"):
        controller.candidates(Client(), None, "auto-merge")
