"""Regression test for pane-wrapper.ps1's flag-stripping argv parser.

Migrated from plugins/agent-worktrees/tests/test_pane_wrapper_argv.py as part
of the Phase 3b Sub-slice 2a Step 2 cutover (efforts/active/worktree-manager-
control-plane/phase-3b-mux-relocation.md): Worktree Manager's bin/ is now the
sole copy of the interactive mux launch scripts, so their regression coverage
lives here instead of in agent-worktrees.

PowerShell's ``$x = if (cond) { @(...) } else { @() }`` (if-as-expression
assignment) silently unwraps a single-element array result to a bare scalar,
even though the branch itself forces array typing with ``@()``. The
wrapper's flag-stripping loop narrows ``$rest`` down to exactly the trailing
payload command once every known ``-AwWt``/``--aw-prompt-b64``/
``--aw-prompt-receipt-b64``/``-AwAhpTokenFile`` pair is stripped -- for the
common single-token payload case (e.g. bare ``copilot``) that narrowing
lands on exactly one remaining element, which used to collapse ``$rest`` to
a scalar string and silently corrupt the eventual ``& $rest[0] @($rest[1..])``
invocation (issue found via live test-drive on the context-handoff-overhaul
effort's Phase 6 mux-primitive validation).

This extracts and runs the real parsing loop (not a paraphrase) via a live
``pwsh`` subprocess, so a regression of the if-as-expression pattern is
caught even though it is invisible to any purely textual/source-based check.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "bin" / "pane-wrapper.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="PowerShell array semantics are Windows-specific here")


def _run_parser(tmp_path: Path, args: list[str]) -> dict:
    """Extract the real flag-stripping loop and report the final ``$rest``."""
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 (pwsh) is required")

    source = WRAPPER.read_text(encoding="utf-8")
    loop = source.split("$rest = @($args)", 1)[1].split(
        "if ($rest.Count -eq 0) { exit 0 }", 1
    )[0]

    script = tmp_path / "parse.ps1"
    args_literal = ",".join(f"'{a}'" for a in args)
    script.write_text(
        "$args = @(" + args_literal + ")\n"
        "$rest = @($args)\n"
        + loop
        + "\n"
        "[pscustomobject]@{\n"
        "    Count = $rest.Count\n"
        "    Type  = $rest.GetType().Name\n"
        "    Items = @($rest)\n"
        "} | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NoLogo", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_single_token_payload_survives_as_array(tmp_path: Path):
    """The realistic failing shape: one payload token (``copilot``) trailing
    the three known control flags. ``$rest`` must remain an array so
    ``$rest[0]`` is the whole token, not its first character."""
    parsed = _run_parser(
        tmp_path,
        [
            "-AwWt", "some-worktree-id",
            "--aw-prompt-b64", "cHJvbXB0",
            "--aw-prompt-receipt-b64", "cmVjZWlwdA==",
            "copilot",
        ],
    )
    assert parsed["Type"] != "String", (
        "$rest collapsed to a scalar string -- the if-as-expression unwrap regressed"
    )
    assert parsed["Count"] == 1
    items = parsed["Items"] if isinstance(parsed["Items"], list) else [parsed["Items"]]
    assert items == ["copilot"]


def test_multi_token_payload_still_correct(tmp_path: Path):
    """A multi-token payload (already worked before the fix) must still."""
    parsed = _run_parser(
        tmp_path,
        [
            "-AwWt", "some-worktree-id",
            "--aw-prompt-b64", "cHJvbXB0",
            "--aw-prompt-receipt-b64", "cmVjZWlwdA==",
            "copilot", "--allow-all-tools", "-p", "hi",
        ],
    )
    assert parsed["Type"] != "String"
    assert parsed["Count"] == 4
    assert parsed["Items"] == ["copilot", "--allow-all-tools", "-p", "hi"]


def test_no_control_flags_bare_command_unaffected(tmp_path: Path):
    """No ``-AwWt``/prompt flags at all -- the loop never runs; ``$rest``
    should be exactly the original argv, untouched."""
    parsed = _run_parser(tmp_path, ["copilot"])
    assert parsed["Type"] != "String"
    assert parsed["Count"] == 1
    items = parsed["Items"] if isinstance(parsed["Items"], list) else [parsed["Items"]]
    assert items == ["copilot"]
