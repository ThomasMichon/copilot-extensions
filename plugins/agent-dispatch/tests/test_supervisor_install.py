"""Guard the safety-critical shape of the embody-supervisor installer (#2869).

`install.sh` grows a *second* systemd user unit, `agent-dispatch-supervisor.service`,
that runs `agent-dispatch supervise` as a serve loop. Because it runs with
``--all-repos``, the **label opt-in is the only thing standing between the
supervisor and embodying every queued task** (handoffs, interactive
worktree-pinned tasks, ...). That makes a handful of invariants load-bearing:

  1. the supervise invocation is scoped ``--all-repos`` (never a bare short
     ``--repo`` form, which silently filters every task out);
  2. the generated launcher **refuses to run** when no opt-in label is set;
  3. the installer enables/starts the unit **only** when a label is configured
     (``_supervisor_labels_configured``); with none set it is left inert;
  4. the shipped ``supervisor.env`` defaults to an **empty** label list, so a
     fresh install never auto-embodies anything.

These tests read ``install.sh`` as text and assert the safe shape so a refactor
cannot silently remove the guard.

The Windows installer (``install.ps1``) carries the *same* supervisor as a
Scheduled Task (``agent-dispatch-supervisor``) -- cross-platform-parity -- so the
same invariants are guarded there too (see ``TestWindowsSupervisorInstall``).
"""

from __future__ import annotations

from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parent.parent / "scripts" / "install.sh"
INSTALL_PS1 = Path(__file__).resolve().parent.parent / "scripts" / "install.ps1"


def _text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _ps1_text() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def test_install_sh_exists():
    assert INSTALL_SH.is_file(), f"missing {INSTALL_SH}"


def test_supervisor_unit_name_and_launcher_defined():
    text = _text()
    assert 'SUPERVISOR_UNIT="agent-dispatch-supervisor.service"' in text
    assert "SUPERVISOR_LAUNCHER=" in text
    assert "_install_supervisor_service()" in text


def test_supervisor_unit_and_launcher_put_local_bin_on_path():
    """The supervisor unit + launcher must place ~/.local/bin (and ~/.bun/bin)
    on PATH so embody spawns can find agent-worktrees/copilot -- a systemd
    --user unit's default PATH omits them (copilot-extensions#89)."""
    text = _text()
    # A PATH is defined and baked into the unit's [Service] block...
    assert "SUPERVISOR_PATH=" in text
    assert "Environment=PATH=$SUPERVISOR_PATH" in text
    assert ".local/bin" in text and ".bun/bin" in text
    # ...before the EnvironmentFile, so supervisor.env can still override it.
    svc = text.index("Environment=PATH=$SUPERVISOR_PATH")
    envfile = text.index("EnvironmentFile=-$env_file")
    assert svc < envfile, (
        "Environment=PATH must precede EnvironmentFile so supervisor.env can "
        "override PATH"
    )
    # ...and the launcher prepends them too (covers a hand-run launcher).
    assert 'export PATH="\\$HOME/.local/bin:\\$HOME/.bun/bin:\\$PATH"' in text


def test_supervise_invocation_is_all_repos_scoped():
    """The launcher must invoke ``supervise --all-repos`` -- never a bare
    short ``--repo`` form that silently filters every task out (the lane gotcha).
    """
    text = _text()
    assert "supervise --all-repos" in text, (
        "the supervisor launcher must run `supervise --all-repos` so it does "
        "not silently filter every task out (the lane-scoping gotcha)"
    )


def test_launcher_refuses_label_less_run():
    """The generated launcher must hard-refuse to run with no opt-in label --
    a label-less supervisor would embody EVERY queued task."""
    text = _text()
    assert "have_label=0" in text and 'have_label=1' in text
    assert 'if [[ "\\$have_label" -eq 0 ]]; then' in text, (
        "the launcher must guard on an empty label set"
    )
    assert "exit 78" in text, (
        "the launcher must exit non-zero (EX_CONFIG) rather than embody "
        "everything when no opt-in label is configured"
    )


def test_service_enabled_only_when_labels_configured():
    """`_install_supervisor_unit` must gate `enable`/`restart` behind
    `_supervisor_labels_configured <env-file>` (or MODE=serve, which self-gates),
    and disable/stop otherwise."""
    text = _text()
    assert "_supervisor_labels_configured" in text
    idx = text.index("_install_supervisor_unit()")
    body = text[idx:]
    # The enable/restart is gated by the label check (MODE=serve short-circuits it,
    # since the master daemon self-gates -- only labeled units run). Match the label
    # guard substring (present in both the legacy and serve-aware conditions).
    guard = body.index('_supervisor_labels_configured "$env_file"; then')
    enable = body.index('systemctl --user enable "$unit"')
    disable = body.index('systemctl --user disable "$unit"')
    assert guard < enable < disable, (
        "enable must be gated by _supervisor_labels_configured for that env "
        "file, with disable/stop in the inert (no-label) branch"
    )
    # The label gate must still be present for the LEGACY (non-serve) path -- the
    # only thing standing between the direct supervisor and embodying everything.
    assert '_supervisor_labels_configured "$env_file"' in body


def test_serve_mode_runs_master_daemon_and_self_gates():
    """MODE=serve switches the launcher to the master daemon and enables the unit
    unconditionally (the daemon self-gates: only labeled declarations/profiles run)."""
    text = _text()
    # The launcher execs the master daemon in serve mode.
    assert "supervise serve --legacy-env" in text
    assert 'mode="\\${AGENT_DISPATCH_SUPERVISE_MODE:-}"' in text
    # _install_supervisor_unit enables unconditionally when MODE=serve.
    idx = text.index("_install_supervisor_unit()")
    body = text[idx:]
    assert '[[ "$mode" == "serve" ]] || _supervisor_labels_configured' in body, (
        "serve mode must bypass the label gate (the daemon self-gates)"
    )
    # In serve mode the installer retires per-profile units instead of creating them.
    assert "_retire_supervisor_profile_units" in text


def test_shipped_env_defaults_to_no_labels():
    """The generated supervisor.env must ship an EMPTY label list so a fresh
    install is inert (embodies nothing) until an operator opts in."""
    text = _text()
    assert "AGENT_DISPATCH_SUPERVISE_LABELS=\n" in text, (
        "supervisor.env must default AGENT_DISPATCH_SUPERVISE_LABELS to empty"
    )


def test_supervisor_gated_off_on_wsl_and_client_hosts():
    """The supervisor must not install on a WSL guest / client-only host."""
    text = _text()
    idx = text.index("_install_supervisor_service()")
    body = text[idx : text.index("\n}\n", idx)]
    assert "_is_wsl" in body and 'NO_SERVICE' in body, (
        "the supervisor install must skip WSL / client-only hosts"
    )
    assert "NO_SUPERVISOR" in body, "must honor --no-supervisor"
    assert "_remove_all_supervisor_units" in body, (
        "client-only / WSL / --no-supervisor must remove primary and profile supervisors"
    )


def test_supervisor_profile_directory_referenced():
    text = _text()
    assert 'SUPERVISOR_PROFILE_DIR="$INSTALL_DIR/supervisors"' in text
    assert 'mkdir -p "$UNIT_DIR" "$SUPERVISOR_PROFILE_DIR"' in text


def test_profile_units_are_named_from_safe_profile_stems_and_share_launcher():
    text = _text()
    assert '[[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]' in text
    assert "printf 'agent-dispatch-supervisor-%s.service'" in text
    assert "EnvironmentFile=-$env_file" in text
    assert "ExecStart=$SUPERVISOR_LAUNCHER" in text
    assert '_install_supervisor_unit "$SUPERVISOR_UNIT" "$SUPERVISOR_ENV_FILE"' in text


def test_profile_label_gating_uses_profile_env_file():
    text = _text()
    idx = text.index("_install_supervisor_unit()")
    body = text[idx:]
    assert 'if _supervisor_labels_configured "$env_file"; then' in body
    assert 'systemctl --user enable "$unit"' in body
    assert 'systemctl --user disable "$unit"' in body


def test_profile_reconcile_removes_orphan_units():
    text = _text()
    idx = text.index("_reconcile_supervisor_profiles()")
    body = text[idx:]
    assert '"$UNIT_DIR"/agent-dispatch-supervisor-*.service' in body
    assert 'env_file="$SUPERVISOR_PROFILE_DIR/$name.env"' in body
    assert '[[ ! -f "$env_file" ]]' in body
    assert '_remove_supervisor_unit "$unit"' in body


def test_primary_supervisor_unit_and_env_remain_legacy_names():
    text = _text()
    assert 'SUPERVISOR_UNIT="agent-dispatch-supervisor.service"' in text
    assert 'SUPERVISOR_ENV_FILE="$INSTALL_DIR/supervisor.env"' in text
    assert '_install_supervisor_unit "$SUPERVISOR_UNIT" "$SUPERVISOR_ENV_FILE"' in text


# -- Windows (install.ps1) parity --------------------------------------------


class TestWindowsSupervisorInstall:
    """The Windows installer carries the same label-gated supervisor as a
    Scheduled Task (``agent-dispatch-supervisor``). Same invariants, PowerShell."""

    def test_install_ps1_exists(self):
        assert INSTALL_PS1.is_file(), f"missing {INSTALL_PS1}"

    def test_supervisor_task_name_and_functions_defined(self):
        text = _ps1_text()
        assert "$SupervisorTaskName = 'agent-dispatch-supervisor'" in text
        assert "function Install-SupervisorTask" in text
        assert "function Remove-SupervisorTask" in text
        assert "function Test-SupervisorLabelsConfigured" in text

    def test_supervise_invocation_is_all_repos_scoped(self):
        text = _ps1_text()
        assert "'supervise', '--all-repos'" in text, (
            "the Windows supervisor launcher must run `supervise --all-repos` so "
            "it does not silently filter every task out (the lane gotcha)"
        )

    def test_launcher_refuses_label_less_run(self):
        text = _ps1_text()
        # The launcher guards on an empty label set and exits EX_CONFIG (78).
        assert "if (-not `$haveLabel)" in text
        assert "exit 78" in text, (
            "the Windows launcher must exit non-zero (EX_CONFIG) rather than "
            "embody everything when no opt-in label is configured"
        )

    def test_task_enabled_only_when_labels_configured(self):
        text = _ps1_text()
        idx = text.index("function Install-SupervisorTaskInstance")
        body = text[idx:]
        gate = body.index("if (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)")
        start = body.index("Start-ScheduledTask -TaskName $Name")
        disable = body.index("Disable-ScheduledTask -TaskName $Name")
        # enable/start in the positive branch (after the gate); disable in the
        # inert (no-label) else branch (after start).
        assert gate < start < disable, (
            "enable/start must be gated by Test-SupervisorLabelsConfigured for "
            "that env file, with Disable-ScheduledTask in the inert branch"
        )

    def test_shipped_env_defaults_to_no_labels(self):
        text = _ps1_text()
        assert "AGENT_DISPATCH_SUPERVISE_LABELS=\n" in text, (
            "supervisor.env must default AGENT_DISPATCH_SUPERVISE_LABELS to empty"
        )

    def test_supervisor_gated_off_on_client_hosts(self):
        text = _ps1_text()
        idx = text.index("function Install-SupervisorTask {")
        body = text[idx : text.index("\n}\n", idx)]
        assert "$NoSupervisor" in body and "$NoService" in body, (
            "the supervisor install must skip client-only / -NoSupervisor hosts"
        )
        assert "Remove-AllSupervisorTasks" in body, (
            "client-only / -NoSupervisor must remove primary and profile supervisors"
        )

    def test_supervisor_wired_into_actions(self):
        text = _ps1_text()
        # install + update call Install-SupervisorTask; uninstall removes all supervisors.
        assert text.count("Install-SupervisorTask") >= 3  # def + install + update
        assert "Remove-AllSupervisorTasks" in text

    def test_supervisor_profile_directory_referenced(self):
        text = _ps1_text()
        assert "$SupervisorProfileDir = Join-Path $InstallDir 'supervisors'" in text
        assert "Get-SupervisorProfileFiles" in text

    def test_profile_tasks_are_named_from_safe_profile_stems_and_share_launcher(self):
        text = _ps1_text()
        assert "return ($Name -match '^[A-Za-z0-9_-]+$')" in text
        assert 'return "$SupervisorTaskName-$ProfileName"' in text
        assert "Write-SupervisorLauncher" in text
        assert '-File `"$Launcher`" -EnvFile `"$EnvFile`"' in text

    def test_profile_label_gating_uses_profile_env_file(self):
        text = _ps1_text()
        idx = text.index("function Install-SupervisorTaskInstance")
        body = text[idx:]
        assert "if (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)" in body
        assert "Enable-ScheduledTask -TaskName $Name" in body
        assert "Disable-ScheduledTask -TaskName $Name" in body

    def test_profile_reconcile_removes_orphan_tasks(self):
        text = _ps1_text()
        idx = text.index("function Remove-OrphanSupervisorProfiles")
        body = text[idx:]
        assert 'Get-ScheduledTask -TaskName "$SupervisorTaskName-*"' in body
        assert "Get-SupervisorProfileEnvFile -ProfileName $profile" in body
        assert "(-not (Test-Path $envFile))" in body
        assert "Remove-SupervisorTask -Name $name" in body

    def test_primary_supervisor_task_and_env_remain_legacy_names(self):
        text = _ps1_text()
        assert "$SupervisorTaskName = 'agent-dispatch-supervisor'" in text
        assert "Join-Path $InstallDir 'supervisor.env'" in text
        assert "Install-SupervisorTaskInstance -Name $SupervisorTaskName -EnvFile $envFile" in text

    def test_launchers_survive_a_locked_log(self):
        """A busy/locked ``*-service.log`` must never block startup.

        Regression: the generated launchers set ``$ErrorActionPreference = 'Stop'``
        and wrote a banner to a fixed ``serve-service.log`` / ``supervise-service.log``
        with ``Out-File``. When another process held that log (observed on
        Anomalous-Potato), the banner write threw and killed the launch before the
        coordinator/supervisor ever started. Both launchers must instead resolve a
        WRITABLE log -- the canonical file, else a version+pid-aware fallback -- and
        never let a banner-write failure be fatal.
        """
        text = _ps1_text()
        # Both launchers define + use the writable-log resolver.
        assert text.count("function Resolve-WritableLog") == 2, (
            "both the coordinator and supervisor launchers must resolve a writable "
            "log so a locked log can't block startup"
        )
        assert "Resolve-WritableLog (Join-Path `$PSScriptRoot 'serve-service.log')" in text
        assert "Resolve-WritableLog (Join-Path `$PSScriptRoot 'supervise-service.log')" in text
        # The fallback is version- and pid-aware (never contends).
        assert "-`$ver-`$PID.log" in text, (
            "the fallback log path must be version+pid aware so it never contends"
        )
        # The old fatal pattern -- a bare banner Out-File to a fixed path -- is gone.
        assert "'serve-service.log'\n" not in text
        assert (
            "\"[`$(Get-Date -Format o)] agent-dispatch coordinator launch "
            "(host=`$pinned port=`$portShown)\" |\n    Out-File" not in text
        ), "the coordinator banner write must be wrapped, not a bare fatal Out-File"
