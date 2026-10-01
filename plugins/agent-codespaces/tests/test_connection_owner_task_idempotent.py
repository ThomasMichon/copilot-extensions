"""Regression: Sync-ConnectionOwnerService must not rewrite an already-
correct scheduled task on every routine run.

`Register-ScheduledTask -Force` requires the same Task Scheduler ACL write
permission regardless of whether the new definition actually differs from
what is already registered. Calling it unconditionally on every install/
update run means a routine, nothing-changed run fails with Access Denied
on any non-elevated host whose task ACL was ever touched by an elevated
run -- forever, since there is no way to make the values "different enough"
to succeed. The task's action already points at a stable binstub + a
stable resolved host binary (pwsh/powershell.exe), so it is meant to be
byte-identical across routine updates; the fix is to compare before writing
and skip the elevation-requiring call entirely when nothing changed
(mirrors agent-vault's Register-AgentVaultTask and agent-index's
Test-TaskCurrent / Register-UserModeTask, both established exemplars of
this pattern -- see docs/patterns/service-lifecycle-supervision.md).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import textwrap
import os
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"
INSTALL_SH = PLUGIN / "scripts" / "install.sh"


def _sync_connection_owner_service_body() -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    rest = text.split("function Sync-ConnectionOwnerService", 1)[1]
    # Closed by a `}` at column 0, the file's top-level function-closing
    # convention (matches agent-logger's test_install_binstub.py style).
    return rest.split("\n}\n", 1)[0]


def _ps1_function_body(name: str) -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    rest = text.split(f"function {name}", 1)[1]
    return rest.split("\n}\n", 1)[0]


def _sh_function_body(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    rest = text.split(f"{name}() {{", 1)[1]
    return rest.split("\n}\n", 1)[0]


def _sh_function_definition(name: str) -> str:
    return f"{name}() {{" + _sh_function_body(name) + "\n}\n"


def test_connection_owner_service_skips_reregister_when_action_unchanged() -> None:
    body = _sync_connection_owner_service_body()

    assert "Get-ScheduledTask -TaskName $OwnerTaskName -ErrorAction SilentlyContinue" in body
    assert "$existingAction.Execute -eq $action.Execute" in body
    assert "already correct" in body
    # The unconditional-write call must now be reached only on the genuine-
    # deviation branch, not called bare at the top of the function.
    assert body.count("Register-ScheduledTask -TaskName $OwnerTaskName") == 1


def test_connection_owner_service_still_registers_on_real_deviation() -> None:
    # The actual write path must still exist (not deleted outright) -- this
    # guards against a fix that silently no-ops registration forever instead
    # of correctly gating it.
    body = _sync_connection_owner_service_body()
    assert "Register-ScheduledTask -TaskName $OwnerTaskName -Action $action -Trigger $trigger" in body
    assert "-Force -ErrorAction Stop" in body


def test_connection_owner_service_query_failure_preserves_existing_task() -> None:
    config = _ps1_function_body("Get-ConnectionOwnerConfig")
    sync = _sync_connection_owner_service_body()

    assert "$result = @{ Known = $false; Enabled = $false; Interval = 15.0 }" in config
    assert "$result.Known = $true" in config
    assert "if (-not $co.Known)" in sync
    assert "leaving any existing scheduled task unchanged" in sync
    assert sync.index("if (-not $co.Known)") < sync.index("if (-not $co.Enabled)")
    assert sync.index("if (-not $co.Enabled)") < sync.index("Unregister-ConnectionOwnerService")


def test_connection_owner_service_posix_unknown_preserves_existing_unit() -> None:
    enabled = _sh_function_body("_owner_enabled")
    sync = _sh_function_body("_sync_owner_service")

    assert 'print("enabled" if json.load(sys.stdin).get("enabled") else "disabled")' in enabled
    assert 'print("unknown")' in enabled
    assert '|| echo "unknown"' not in enabled
    assert 'json="$(PYTHONUTF8=1 "$LINK_PYTHON" -m agent_codespaces owner --status' in enabled
    assert 'if [[ "$owner_state" == "unknown" ]]' in sync
    assert "leaving any existing systemd unit unchanged" in sync
    assert 'if [[ "$owner_state" == "disabled" ]]' in sync
    assert sync.index('if [[ "$owner_state" == "unknown" ]]') < sync.index('if [[ "$owner_state" == "disabled" ]]')
    assert sync.index('if [[ "$owner_state" == "disabled" ]]') < sync.index("_remove_owner_service")
    assert 'cat > "$unit_dir/$SYSTEMD_OWNER_UNIT"' in sync


def test_connection_owner_posix_status_query_executes_under_strict_mode(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")
    probe = tmp_path / "probe.sh"
    probe.write_text("#!/usr/bin/env bash\nprintf ok\n", encoding="utf-8")
    probe.chmod(0o755)
    probe_result = subprocess.run(
        [bash, str(probe)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )
    if probe_result.returncode != 0 or probe_result.stdout != "ok":
        pytest.skip("bash cannot execute this platform's temporary scripts")
    stub_python = tmp_path / "python-stub.sh"
    stub_python.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "agent_codespaces" && "${{3:-}}" == "owner" && "${{4:-}}" == "--status" ]]; then
                case "${{OWNER_CASE}}" in
                    fail)
                        exit 9
                        ;;
                    disabled)
                        printf '%s\\n' '{{"enabled": false}}'
                        ;;
                    enabled)
                        printf '%s\\n' '{{"enabled": true}}'
                        ;;
                    json_then_fail)
                        printf '%s\\n' '{{"enabled": false}}'
                        exit 7
                        ;;
                    *)
                        exit 64
                        ;;
                esac
                exit 0
            fi
            exec {shlex.quote(sys.executable)} "$@"
            """
        ),
        encoding="utf-8",
    )
    stub_python.chmod(0o755)
    runner = tmp_path / "run-owner-enabled.sh"
    runner.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            LINK_PYTHON="$1"
            {_sh_function_definition("_owner_enabled")}
            _owner_enabled
            """
        ),
        encoding="utf-8",
    )
    runner.chmod(0o755)
    expected = {
        "fail": "unknown",
        "disabled": "disabled",
        "enabled": "enabled",
        "json_then_fail": "unknown",
    }
    for case, output in expected.items():
        result = subprocess.run(
            [bash, str(runner), str(stub_python)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "OWNER_CASE": case},
        )
        assert result.stdout == f"{output}\n"
        assert result.stderr == ""
