"""Tests for the trusted self-hosted CI workflow guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

MODULE_PATH = Path(__file__).with_name("check-trusted-ci.py")
SPEC = importlib.util.spec_from_file_location("check_trusted_ci", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _workflow(tmp_path: Path, transform: Callable[[str], str]) -> Path:
    text = MODULE.WORKFLOW.read_text(encoding="utf-8")
    path = tmp_path / "trusted-ci.yml"
    path.write_text(transform(text), encoding="utf-8")
    (tmp_path / "ci.yml").write_text(
        (MODULE.WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return path


def test_repository_workflow_is_valid() -> None:
    assert MODULE.validate_workflow() == []


def test_default_labels_are_not_accepted(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "runs-on: copilot-extensions-ci",
            "runs-on: [self-hosted, Linux, X64]",
        ),
    )
    assert "self-hosted job does not match the reviewed contract" in (
        MODULE.validate_workflow(path)
    )


def test_mutable_checkout_ref_is_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "${{ github.event.pull_request.head.sha }}",
            "${{ github.event.pull_request.head.ref }}",
        ),
    )
    errors = MODULE.validate_workflow(path)
    assert "self-hosted job does not match the reviewed contract" in errors
    assert "mutable pull-request head refs are forbidden" in errors


def test_activation_latch_is_required(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "vars.TRUSTED_SELF_HOSTED_CI == 'enabled'",
            "true",
        ),
    )
    errors = MODULE.validate_workflow(path)
    assert "authorization job does not match the reviewed contract" in errors
    assert "self-hosted job does not match the reviewed contract" in errors


def test_unconditional_authorization_is_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            'core.setOutput("trusted", authorTrusted && senderTrusted);',
            'core.setOutput("trusted", true);',
        ),
    )
    assert "authorization job does not match the reviewed contract" in (
        MODULE.validate_workflow(path)
    )


def test_extra_trusted_permission_is_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "permissions.push === true;",
            "permissions.push === true || permissions.pull === true;",
        ),
    )
    assert "authorization job does not match the reviewed contract" in (
        MODULE.validate_workflow(path)
    )


def test_authorization_output_must_come_from_permission_step(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "${{ steps.permission.outputs.trusted }}",
            "${{ github.actor != '' }}",
        ),
    )
    assert "authorization job does not match the reviewed contract" in (
        MODULE.validate_workflow(path)
    )


def test_trusted_condition_must_be_exact(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "vars.TRUSTED_SELF_HOSTED_CI == 'enabled'",
            "vars.TRUSTED_SELF_HOSTED_CI == 'enabled' || github.actor == 'attacker'",
            1,
        ),
    )
    assert "authorization job does not match the reviewed contract" in (
        MODULE.validate_workflow(path)
    )


def test_additional_self_hosted_job_is_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text
        + """
  bypass:
    runs-on: copilot-extensions-ci
    steps:
      - run: echo bypass
""",
    )
    errors = MODULE.validate_workflow(path)
    assert "workflow must contain exactly authorize and agent-bridge jobs" in errors
    assert "trusted-ci.yml:bypass: unauthorized self-hosted runner route" in errors


def test_actions_must_be_commit_pinned(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/setup-python@v5",
        ),
    )
    assert "all actions must be pinned to full commit SHAs" in (
        MODULE.validate_workflow(path)
    )


def test_event_cannot_change_to_pull_request(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace("pull_request_target:", "pull_request:"),
    )
    assert "workflow trigger does not match the reviewed pull_request_target contract" in (
        MODULE.validate_workflow(path)
    )


def test_self_hosted_job_must_depend_on_authorization(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace("needs: authorize", "needs: []"),
    )
    assert "self-hosted job does not match the reviewed contract" in (
        MODULE.validate_workflow(path)
    )


def test_secret_reference_is_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "run: python tools/run-plugin-tests.py agent-bridge",
            "env:\n          TOKEN: ${{ secrets.EXAMPLE }}\n"
            "        run: python tools/run-plugin-tests.py agent-bridge",
        ),
    )
    assert "self-hosted workflow must not expose repository secrets or token" in (
        MODULE.validate_workflow(path)
    )


def test_sibling_workflow_self_hosted_routes_are_rejected(tmp_path: Path) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    sibling = tmp_path / "other.yml"
    cases = (
        "Copilot-Extensions-CI",
        "Linux",
        "X64",
        "ubuntu-private-runner",
        "${{ matrix.runner }}",
        "{group: default, labels: [copilot-extensions-ci]}",
    )
    for index, runs_on in enumerate(cases):
        sibling.write_text(
            f"""
name: Other
on: pull_request
jobs:
  bypass{index}:
    runs-on: {runs_on}
    steps:
      - run: echo bypass
""",
            encoding="utf-8",
        )
        errors = MODULE.validate_workflow(trusted)
        assert any(
            "unauthorized self-hosted runner route" in error
            or "dynamic runner route is forbidden" in error
            for error in errors
        )


def test_sibling_reusable_workflow_route_is_rejected(tmp_path: Path) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    (tmp_path / "other.yml").write_text(
        """
name: Other
on: pull_request
jobs:
  bypass:
    uses: example/repository/.github/workflows/reusable.yml@main
""",
        encoding="utf-8",
    )
    assert "other.yml:bypass: reusable workflow route is forbidden" in (
        MODULE.validate_workflow(trusted)
    )


def test_compact_token_and_indexed_secret_references_are_rejected(
    tmp_path: Path,
) -> None:
    token_path = _workflow(
        tmp_path,
        lambda text: text
        + "\n# ${{github.token}}\n# ${{ github['token'] }}\n"
        "# ${{ secrets['EXAMPLE'] }}\n",
    )
    assert "self-hosted workflow must not expose repository secrets or token" in (
        MODULE.validate_workflow(token_path)
    )


def test_ci_must_invoke_contract_guard(tmp_path: Path) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    (tmp_path / "ci.yml").write_text(
        "name: CI\non: pull_request\njobs: {}\n",
        encoding="utf-8",
    )
    assert "ci.yml must invoke the ungated trusted CI guard and tests" in (
        MODULE.validate_workflow(trusted)
    )


def test_extra_top_level_execution_controls_are_rejected(tmp_path: Path) -> None:
    path = _workflow(
        tmp_path,
        lambda text: text.replace(
            "concurrency:",
            "env:\n  X: ${{ toJSON(secrets) }}\ndefaults:\n"
            "  run:\n    shell: bash\nconcurrency:",
        ),
    )
    errors = MODULE.validate_workflow(path)
    assert "workflow top-level keys do not match the reviewed contract" in errors
    assert "self-hosted workflow must not expose repository secrets or token" in errors


def test_ci_guard_step_cannot_be_conditioned_or_commented_out(
    tmp_path: Path,
) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    ci_path = tmp_path / "ci.yml"
    original = ci_path.read_text(encoding="utf-8")
    ci_path.write_text(
        original.replace(
            "      - name: Trusted self-hosted CI contract",
            "      - name: Trusted self-hosted CI contract\n        if: false",
        ),
        encoding="utf-8",
    )
    assert "ci.yml must invoke the ungated trusted CI guard and tests" in (
        MODULE.validate_workflow(trusted)
    )

    ci_path.write_text(
        original.replace(
            "      - name: Trusted self-hosted CI contract\n"
            "        run: >-\n"
            "          python tools/check-trusted-ci.py &&\n"
            "          python -m pytest -q tools/test_check_trusted_ci.py",
            "      # python tools/check-trusted-ci.py && "
            "python -m pytest -q tools/test_check_trusted_ci.py",
        ),
        encoding="utf-8",
    )
    assert "ci.yml must invoke the ungated trusted CI guard and tests" in (
        MODULE.validate_workflow(trusted)
    )


def test_ci_must_keep_pull_request_trigger(tmp_path: Path) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    ci_path = tmp_path / "ci.yml"
    ci_path.write_text(
        ci_path.read_text(encoding="utf-8").replace("  pull_request:\n", ""),
        encoding="utf-8",
    )
    assert "ci.yml must run on every pull_request without filters" in (
        MODULE.validate_workflow(trusted)
    )


def test_ci_pull_request_trigger_cannot_be_filtered(tmp_path: Path) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    ci_path = tmp_path / "ci.yml"
    ci_path.write_text(
        ci_path.read_text(encoding="utf-8").replace(
            "  pull_request:\n",
            "  pull_request:\n    paths: [docs/**]\n",
        ),
        encoding="utf-8",
    )
    assert "ci.yml must run on every pull_request without filters" in (
        MODULE.validate_workflow(trusted)
    )


def test_ci_checks_job_cannot_be_gated_or_dependent(tmp_path: Path) -> None:
    for injection in ("    if: false\n", "    needs: full\n"):
        trusted = _workflow(tmp_path, lambda text: text)
        ci_path = tmp_path / "ci.yml"
        ci_path.write_text(
            ci_path.read_text(encoding="utf-8").replace(
                "  checks:\n",
                "  checks:\n" + injection,
            ),
            encoding="utf-8",
        )
        assert "ci.yml checks job must be ungated and dependency-free" in (
            MODULE.validate_workflow(trusted)
        )


def test_ci_guard_step_cannot_continue_on_error(tmp_path: Path) -> None:
    trusted = _workflow(tmp_path, lambda text: text)
    ci_path = tmp_path / "ci.yml"
    ci_path.write_text(
        ci_path.read_text(encoding="utf-8").replace(
            "      - name: Trusted self-hosted CI contract",
            "      - name: Trusted self-hosted CI contract\n"
            "        continue-on-error: true",
        ),
        encoding="utf-8",
    )
    assert "ci.yml must invoke the ungated trusted CI guard and tests" in (
        MODULE.validate_workflow(trusted)
    )
