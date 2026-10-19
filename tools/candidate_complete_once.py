"""Inert, fail-closed recipe admission for complete-once candidate CI."""
from __future__ import annotations

import re
from pathlib import Path

from materialized_candidate_inventory import CandidateValidationError, python_suites, regular

PROFILE = "complete-once-v1"
FULL_OWNERS = (
    "agent-bridge", "agent-codespaces", "agent-containers", "agent-dispatch",
    "agent-logger", "agent-mcp", "agent-ssh", "agent-vault", "agent-worktrees",
)
DISCOVERY_ENV = """        env:
          EVENT_NAME: ${{ github.event_name }}
          PROMOTION_RUN: ${{ inputs.promotion_run }}
          PROMOTION_PHASE: ${{ inputs.promotion_phase }}
          CANDIDATE_PORTFOLIO_PROFILE: complete-once-v1
"""
DISCOVERY_FILTER = """          if [ "$EVENT_NAME" = "workflow_dispatch" ] && [ -n "$PROMOTION_RUN" ] && [ "$PROMOTION_PHASE" = "candidate" ]; then
            SMOKE=$(python tools/ci_plugin_partitions.py candidate-smoke "$SMOKE" \\
              --event "$EVENT_NAME" --promotion-run "$PROMOTION_RUN" \\
              --phase "$PROMOTION_PHASE" --profile "$CANDIDATE_PORTFOLIO_PROFILE")
          fi
"""
FULL_JOB = """  full:
    name: full - ${{ matrix.plugin }}
    if: >-
      github.event_name == 'workflow_dispatch' &&
      (inputs.promotion_run == '' || inputs.promotion_phase == 'candidate')
    runs-on: ubuntu-latest
    timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 8 || 30 }}
    strategy:
      fail-fast: false
      matrix:
        plugin:
""" + "".join(f"          - {name}\n" for name in FULL_OWNERS) + """    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: astral-sh/setup-uv@v9.0.0
      - name: Full suite - ${{ matrix.plugin }}
        env:
          PYTEST_ADDOPTS: ${{ (matrix.plugin == 'agent-worktrees' || matrix.plugin == 'agent-bridge') && inputs.promotion_run != '' && '-n 2 --dist=worksteal' || '' }}
        run: python tools/run-plugin-tests.py ${{ matrix.plugin }}
      - name: Test shared SSH proxy contracts
        if: matrix.plugin == 'agent-bridge' && inputs.promotion_run != '' && inputs.promotion_phase == 'candidate'
        env:
          PYTHONPATH: libs/ssh-manager/src:libs/agent-procutil/src
        run: >-
          .test-venvs/linux/agent-bridge/bin/python -m pytest -q
          libs/ssh-manager/tests/test_process.py
          libs/ssh-manager/tests/test_proxy.py
"""
PARTITIONED_SMOKE_STEP = """      - name: Test ${{ matrix.plugin }}
        if: matrix.plugin != 'agent-dispatch' || github.event_name != 'pull_request'
        env:
          PLUGIN: ${{ matrix.plugin }}
          PARTITION: ${{ matrix.partition }}
          PARTITIONS: ${{ matrix.partitions }}
        run: |
          args=()
          if [ "$PARTITIONS" -gt 1 ]; then
            files=$(python tools/ci_plugin_partitions.py files \\
              --plugin "$PLUGIN" --partition "$PARTITION" --partitions "$PARTITIONS")
            mapfile -t args <<< "$files"
          fi
          python tools/run-plugin-tests.py "$PLUGIN" "${args[@]}"
"""
PARTITIONED_SMOKE_WORKERS_STEP = PARTITIONED_SMOKE_STEP.replace(
    "        env:\n",
    "        env:\n"
    "          PYTEST_ADDOPTS: ${{ matrix.plugin == 'agent-bridge' && "
    "inputs.promotion_run != '' && '-n 2 --dist=worksteal' || '' }}\n",
)
UNPARTITIONED_SMOKE_STEP = """      - name: Test ${{ matrix.plugin }}
        env:
          PYTEST_ADDOPTS: ${{ matrix.plugin == 'agent-bridge' && inputs.promotion_run != '' && '-n 2 --dist=worksteal' || '' }}
        run: python tools/run-plugin-tests.py ${{ matrix.plugin }}
"""


def _recipe(text: str) -> str:
    return "\n".join(
        line.rstrip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def complete_once_plan(text: str, suites: set[str]) -> dict | None:
    """Validate data, never execute candidate code to establish coverage ownership."""
    text = text.replace("\r\n", "\n")
    if "\r" in text:
        raise CandidateValidationError("complete-once recipe has an unsupported lone carriage return")
    declarations = re.findall(r"(?m)^\s*CANDIDATE_PORTFOLIO_PROFILE: (.+)$", text)
    if not declarations and "candidate-smoke" not in text:
        return None
    if declarations != [PROFILE]:
        raise CandidateValidationError("unknown or ambiguous complete-once profile")
    parts = re.split(r"(?m)^  ([a-z][a-z0-9_-]*):\n", text.split("\njobs:\n", 1)[-1])
    jobs = dict(zip(parts[1::2], parts[2::2]))
    if len(jobs) != len(parts[1::2]):
        raise CandidateValidationError("ambiguous complete-once job inventory")
    if _recipe("  full:\n" + jobs.get("full", "")) != _recipe(FULL_JOB):
        raise CandidateValidationError("unrecognized complete-once full owner recipe")
    header = text.split("\njobs:\n", 1)[0]
    environment = re.findall(r"(?ms)^env:\n.*?(?=^[^\s#]|\Z)", header)
    expected_env = (
        "env:\n  CI_PROMOTION_RUN: ${{ inputs.promotion_run }}\n"
        "  HEAVY_PLUGIN: agent-worktrees\n  COVERAGE_GUIDED_SELECTION_MODE: "
    )
    if (
        len(environment) != 1
        or _recipe(environment[0]) not in {_recipe(expected_env + mode) for mode in ("shadow", "off")}
        or re.search(r"(?m)^defaults:", header)
    ):
        raise CandidateValidationError("complete-once cannot inherit an ambient test selection")
    discover = jobs.get("discover", "")
    if (
        discover.count(DISCOVERY_ENV) != 1
        or discover.count(DISCOVERY_FILTER) != 1
        or 'SMOKE=$(python tools/run-plugin-tests.py $MODE --exclude "$HEAVY_PLUGIN" --list)'
        not in discover
        or 'echo "smoke=$SMOKE" >> "$GITHUB_OUTPUT"' not in discover
    ):
        raise CandidateValidationError("unrecognized complete-once discovery recipe")
    before, after = discover.split(DISCOVERY_FILTER)
    if (
        "SMOKE=$(python tools/run-plugin-tests.py" not in before
        or re.search(r"(?m)^\s*SMOKE=", after)
        or 'echo "smoke=$SMOKE"' in before
    ):
        raise CandidateValidationError("complete-once discovery changes or publishes unfiltered coverage")
    smoke = jobs.get("smoke", "")
    if re.search(r"(?m)^    (?:env|defaults):", smoke.split("    steps:", 1)[0]):
        raise CandidateValidationError("complete-once smoke cannot inherit a hidden selection")
    strategies = re.findall(r"(?ms)^    strategy:\n.*?(?=^    steps:)", smoke)
    strategy_prefix = "    strategy:\n      fail-fast: false\n      matrix:\n"
    supported_matrices = {
        _recipe(strategy_prefix + "        plugin: ${{ fromJson(needs.discover.outputs.smoke) }}\n"),
        _recipe(strategy_prefix + "        include: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}\n"),
    }
    if len(strategies) != 1 or _recipe(strategies[0]) not in supported_matrices:
        raise CandidateValidationError("unrecognized complete-once smoke matrix")
    executions = re.findall(
        r"(?ms)^      - name: Test \$\{\{ matrix\.plugin \}\}\n.*?(?=^      - |\Z)", smoke,
    )
    if len(executions) != 1 or _recipe(executions[0]) not in {
        _recipe(UNPARTITIONED_SMOKE_STEP), _recipe(PARTITIONED_SMOKE_STEP),
        _recipe(PARTITIONED_SMOKE_WORKERS_STEP),
    }:
        raise CandidateValidationError("unrecognized complete-once residual smoke recipe")
    heavy = re.findall(r"(?m)^  HEAVY_PLUGIN: ([a-z][a-z0-9-]*)$", text)
    if heavy != ["agent-worktrees"] or not set(FULL_OWNERS) <= suites:
        raise CandidateValidationError("complete-once full owner inventory is incomplete")
    residual = suites - set(FULL_OWNERS)
    if not residual:
        raise CandidateValidationError("complete-once residual smoke inventory is empty")
    return {"profile": PROFILE, "full": list(FULL_OWNERS), "smoke": sorted(residual)}


def candidate_smoke(
    root: Path, plugins: object, *, event: str, promotion_run: str, phase: str, profile: str,
) -> list[str]:
    """Subtract complete owners only for an explicitly identified candidate dispatch."""
    if (
        profile != PROFILE or event not in {"pull_request", "push", "workflow_dispatch"}
        or phase not in {"", "candidate", "release"}
        or (event != "workflow_dispatch" and (promotion_run or phase == "candidate"))
        or (promotion_run and not re.fullmatch(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,9}", promotion_run))
        or (promotion_run and phase == "")
    ):
        raise CandidateValidationError("invalid complete-once invocation identity")
    if (
        not isinstance(plugins, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name)
            for name in plugins
        ) or len(plugins) != len(set(plugins))
    ):
        raise CandidateValidationError("invalid complete-once requested smoke inventory")
    if event != "workflow_dispatch" or not promotion_run or phase != "candidate":
        return plugins.copy()
    suites = python_suites(root)
    text = regular(root, Path(".github/workflows/ci.yml")).read_text("utf-8")
    plan = complete_once_plan(text, suites)
    if plan is None or set(plugins) != suites - {"agent-worktrees"}:
        raise CandidateValidationError("complete-once discovery input omits or adds candidate coverage")
    return plan["smoke"]
