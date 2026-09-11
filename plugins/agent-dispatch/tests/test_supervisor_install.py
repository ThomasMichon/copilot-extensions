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
    assert 'SERVICE_SUFFIX=""' in text
    assert 'SUPERVISOR_UNIT_BASE="agent-dispatch-supervisor${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"' in text
    assert 'SUPERVISOR_UNIT="$SUPERVISOR_UNIT_BASE.service"' in text
    assert "SUPERVISOR_LAUNCHER=" in text
    assert "_install_supervisor_service()" in text


def test_shell_installer_scopes_service_identities_by_install_dir():
    text = _text()
    assert 'LEGACY_INSTALL_DIR="$HOME/.agent-dispatch"' in text
    assert 'install_cmp="$(printf \'%s\' "$INSTALL_DIR"' in text
    assert 'sha256sum | awk \'{print substr($1,1,12)}\'' in text
    assert 'SYSTEMD_UNIT="agent-dispatch${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}.service"' in text


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
    assert 'supervise serve --legacy-env --interval "\\$interval"' in text
    assert 'mode="\\${AGENT_DISPATCH_SUPERVISE_MODE:-}"' in text
    # _install_supervisor_unit enables unconditionally when MODE=serve.
    idx = text.index("_install_supervisor_unit()")
    body = text[idx:]
    assert '[[ "$mode" == "serve" ]] || _supervisor_labels_configured' in body, (
        "serve mode must bypass the label gate (the daemon self-gates)"
    )
    # In serve mode the installer retires per-profile units instead of creating them.
    assert "_retire_supervisor_profile_units" in text


def test_serve_mode_supports_explicit_machine_scope():
    """Both installers' serve launcher must thread an explicit machine scope
    (AGENT_DISPATCH_SUPERVISE_MACHINE -> `--machine`) so a service-context daemon
    can identify itself and correctly scope machine-pinned declarations
    (aperture-labs #5001)."""
    sh = _text()
    assert 'smachine="\\${AGENT_DISPATCH_SUPERVISE_MACHINE:-}"' in sh
    assert '--machine "\\$smachine"' in sh
    # documented in the shipped supervisor.env template
    assert "AGENT_DISPATCH_SUPERVISE_MACHINE=" in sh

    ps1 = _ps1_text()
    assert "'AGENT_DISPATCH_SUPERVISE_MACHINE'" in ps1  # parsed from the env file
    assert "@('--machine', `$sMachine)" in ps1
    assert "AGENT_DISPATCH_SUPERVISE_MACHINE=" in ps1


def test_shipped_env_defaults_to_no_labels():
    """The generated supervisor.env must ship an EMPTY label list so a fresh
    install is inert (embodies nothing) until an operator opts in."""
    text = _text()
    assert "AGENT_DISPATCH_SUPERVISE_LABELS=\n" in text, (
        "supervisor.env must default AGENT_DISPATCH_SUPERVISE_LABELS to empty"
    )


def test_supervisor_gated_off_on_windows_client_and_no_service_hosts():
    """The supervisor installs on a default WSL guest (it owns its own
    coordinator), and is skipped only on a true client-only host (--no-service)
    or a WSL guest opted into Windows-client mode."""
    text = _text()
    idx = text.index("_install_supervisor_service()")
    body = text[idx : text.index("\n}\n", idx)]
    assert "_is_wsl" in body and "_wsl_windows_client" in body and "NO_SERVICE" in body, (
        "the supervisor install must skip --no-service and Windows-client WSL hosts, "
        "but NOT a default WSL guest"
    )
    assert "NO_SUPERVISOR" in body, "must honor --no-supervisor"
    assert "_remove_all_supervisor_units" in body, (
        "client-only / Windows-client WSL / --no-supervisor must remove primary and "
        "profile supervisors"
    )


def test_supervisor_profile_directory_referenced():
    text = _text()
    assert 'SUPERVISOR_PROFILE_DIR="$INSTALL_DIR/supervisors"' in text
    assert 'mkdir -p "$UNIT_DIR" "$SUPERVISOR_PROFILE_DIR"' in text


def test_profile_units_are_named_from_safe_profile_stems_and_share_launcher():
    text = _text()
    assert '[[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]' in text
    assert "printf '%s-%s.service' \"$SUPERVISOR_UNIT_BASE\" \"$name\"" in text
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
    assert '"$UNIT_DIR"/"$SUPERVISOR_UNIT_BASE"-*.service' in body
    assert 'env_file="$SUPERVISOR_PROFILE_DIR/$name.env"' in body
    assert '[[ ! -f "$env_file" ]]' in body
    assert '_remove_supervisor_unit "$unit"' in body


def test_primary_supervisor_unit_and_env_remain_legacy_names():
    text = _text()
    assert 'SERVICE_SUFFIX=""' in text
    assert 'SUPERVISOR_UNIT_BASE="agent-dispatch-supervisor${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"' in text
    assert 'SUPERVISOR_ENV_FILE="$INSTALL_DIR/supervisor.env"' in text
    assert '_install_supervisor_unit "$SUPERVISOR_UNIT" "$SUPERVISOR_ENV_FILE"' in text


def test_shell_units_and_launcher_export_install_dir():
    text = _text()
    assert 'Environment=AGENT_DISPATCH_INSTALL_DIR=$INSTALL_DIR' in text
    assert 'export AGENT_DISPATCH_INSTALL_DIR="$INSTALL_DIR"' in text


# -- Windows (install.ps1) parity --------------------------------------------


class TestWindowsSupervisorInstall:
    """The Windows installer carries the same label-gated supervisor as a
    Scheduled Task (``agent-dispatch-supervisor``). Same invariants, PowerShell."""

    def test_install_ps1_exists(self):
        assert INSTALL_PS1.is_file(), f"missing {INSTALL_PS1}"

    def test_supervisor_task_name_and_functions_defined(self):
        text = _ps1_text()
        assert "$SupervisorTaskName = if ($publishLegacyNames)" in text
        assert "'agent-dispatch-supervisor'" in text
        assert "function Install-SupervisorTask" in text
        assert "function Remove-SupervisorTask" in text
        assert "function Test-SupervisorLabelsConfigured" in text

    def test_windows_installer_scopes_service_identities_by_install_dir(self):
        text = _ps1_text()
        assert "$legacyInstallDir = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.agent-dispatch'))" in text
        assert "$publishLegacyNames = [StringComparer]::OrdinalIgnoreCase.Equals($InstallDir, $legacyInstallDir)" in text
        assert "$TaskName = if ($publishLegacyNames)" in text
        assert "$SupervisorTaskName = if ($publishLegacyNames)" in text
        assert "Substring(0, 12).ToLowerInvariant()" in text

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
        # MODE=serve short-circuits the gate (the master daemon self-gates); match
        # the label-guard substring present in both the legacy and serve conditions.
        gate = body.index("(Test-SupervisorLabelsConfigured -EnvFile $EnvFile)")
        start = body.index("Start-ScheduledTask -TaskName $Name")
        disable = body.index("Disable-ScheduledTask -TaskName $Name")
        # enable/start in the positive branch (after the gate); disable in the
        # inert (no-label) else branch (after start).
        assert gate < start < disable, (
            "enable/start must be gated by Test-SupervisorLabelsConfigured for "
            "that env file, with Disable-ScheduledTask in the inert branch"
        )

    def test_serve_mode_runs_master_daemon_and_self_gates(self):
        """MODE=serve switches the Windows launcher to the master daemon, enables
        the task unconditionally (the daemon self-gates), and retires per-profile
        tasks -- cross-platform parity with the systemd path."""
        text = _ps1_text()
        assert (
            "'supervise', 'serve', '--legacy-env', '--interval', `$interval"
            in text
        )
        assert "function Get-SupervisorMode" in text
        idx = text.index("function Install-SupervisorTaskInstance")
        body = text[idx:]
        assert "$mode -eq 'serve' -or (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)" in body, (
            "serve mode must bypass the label gate (the daemon self-gates)"
        )
        assert "Remove-ProfileSupervisorTasks" in text

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
        assert "(Test-SupervisorLabelsConfigured -EnvFile $EnvFile)" in body
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
        assert "$publishLegacyNames = [StringComparer]::OrdinalIgnoreCase.Equals($InstallDir, $legacyInstallDir)" in text
        assert "$SupervisorTaskName = if ($publishLegacyNames)" in text
        assert "Join-Path $InstallDir 'supervisor.env'" in text
        assert "Install-SupervisorTaskInstance -Name $SupervisorTaskName -EnvFile $envFile" in text

    def test_windows_launchers_export_install_dir(self):
        text = _ps1_text()
        assert "$env:AGENT_DISPATCH_INSTALL_DIR = $InstallDir" in text
        assert "`$env:AGENT_DISPATCH_INSTALL_DIR = '" in text

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

    def test_versioned_activate_gates_migration_stop_on_real_venv_path(self):
        """#689: the legacy-migration force-stop in Invoke-VersionedActivate must
        gate on the ACTUAL `.venv` path, NOT $LinkDir. The versioned refactor
        repointed $LinkDir at the freshly-built versions/<v> slot (always a real,
        non-link dir), so a $LinkDir guard force-stopped the coordinator + supervisor
        on EVERY update, defeating a non-elevated in-place refresh."""
        text = _ps1_text()
        idx = text.index("function Invoke-VersionedActivate")
        body = text[idx : text.index("\n}\n", idx)]
        assert "$legacyVenv = Join-Path $InstallDir '.venv'" in body, (
            "the migration stop must resolve the real .venv path explicitly"
        )
        assert "(Test-Path $legacyVenv) -and -not (Test-VenvIsLink $legacyVenv)" in body, (
            "the migration stop must gate on the real .venv path, not $LinkDir"
        )
        assert "-not (Test-VenvIsLink $LinkDir)" not in body, (
            "the migration stop must NOT test $LinkDir (the always-real slot dir)"
        )

    def test_supervisor_task_refreshed_in_place_without_reregister(self):
        """Register-once model (#689): a non-elevated update must refresh an
        already-registered supervisor task IN PLACE -- no re-register (elevation) --
        by killing the detached daemon and restarting the task onto the new slot."""
        text = _ps1_text()
        assert "function Restart-SupervisorTaskInPlace" in text
        # The install pass retires every detached generation once, then restarts
        # each existing task without killing sibling profiles mid-reconcile.
        assert "function Retire-SupervisorProcesses" in text
        install_idx = text.index("function Install-SupervisorTask {")
        install_body = text[install_idx : text.index("\n}\n", install_idx)]
        assert install_body.index("Invoke-SupervisorsStop") < install_body.index(
            "Retire-SupervisorProcesses"
        )
        assert install_body.index("Retire-SupervisorProcesses") < install_body.index(
            "Install-SupervisorTaskInstance"
        )
        idx = text.index("function Restart-SupervisorTaskInPlace")
        body = text[idx : text.index("\nfunction ", idx + 1)]
        assert "Stop-DispatchProcess -Subcommand supervise" not in body
        assert "Retire-SupervisorProcesses" not in body
        assert "Start-ScheduledTask -TaskName $Name" in body
        # Install-SupervisorTaskInstance short-circuits to the in-place restart
        # whenever the existing task's action already matches the desired one --
        # regardless of the caller's elevation state (#1837): re-registering is
        # reserved for a first install or a genuine task-definition drift.
        inst = text.index("function Install-SupervisorTaskInstance")
        instbody = text[inst:]
        assert "$existingTask = Get-ScheduledTask -TaskName $Name" in instbody
        assert "$matchesDesired = $existingAction -and" in instbody, (
            "an already-registered task with a matching action must be detected "
            "without relying on the caller's elevation state"
        )
        assert "if ($matchesDesired) {" in instbody
        assert "Restart-SupervisorTaskInPlace -Name $Name" in instbody

    def test_supervisor_task_instance_migrates_drifted_definition_when_elevated(self):
        """#1837: an ordinary update (elevated or not) must never force-register
        an already-correct supervisor task; only a genuine drift between the
        registered action and the desired one -- and elevation being available --
        may re-register. A drift without elevation must degrade to an in-place
        restart (stale definition kept) rather than losing the task."""
        text = _ps1_text()
        inst = text.index("function Install-SupervisorTaskInstance")
        instbody = text[inst:]
        matches_idx = instbody.index("if ($matchesDesired) {")
        drift_block = instbody[matches_idx : instbody.index("$trigger = New-ScheduledTaskTrigger -AtLogOn", matches_idx)]
        assert "if (-not (Test-Elevated)) {" in drift_block, (
            "a drifted-but-unelevated definition must degrade to an in-place "
            "restart of the stale task, not silently attempt Register -Force"
        )
        assert drift_block.count("Restart-SupervisorTaskInPlace -Name $Name") == 2

    def test_interactive_update_retires_wrapper_master_and_children_once(self):
        text = _ps1_text()
        helper = text[
            text.index("function Retire-SupervisorProcesses") :
            text.index("\nfunction ", text.index("function Retire-SupervisorProcesses") + 1)
        ]
        assert "_retire-supervisors --install-dir $InstallDir" in helper
        assert "result.retired" in helper

        interactive = text[
            text.index("function Install-SupervisorLogonAutostart") :
            text.index("\nfunction ", text.index("function Install-SupervisorLogonAutostart") + 1)
        ]
        assert "Stop-DispatchProcess -Subcommand supervise" not in interactive
        assert "Retire-SupervisorProcesses" not in interactive

    def test_retirement_uses_current_marker_and_complete_native_fallback(self):
        text = _ps1_text()
        helper = text[
            text.index("function Retire-SupervisorProcesses") :
            text.index("\nfunction Confirm-CoordinatorRunning")
        ]
        assert "current-version" in helper
        assert "function Retire-SupervisorProcessesFallback" in helper
        assert "Get-CimInstance Win32_Process" in helper
        assert "Stop-Process -Id $pid" in helper
        assert "$isRegistrarChild" in helper
        assert "$supervisorRun" in helper
        assert "AGENT_DISPATCH_RUN_DIR" in helper
        assert "$isSupervisor = $underRoot -and (" in helper
        assert "supervise(?=\\s*(?:$|serve(?:\\s|$)|-))" in helper
        assert "$enumerationFailed" in helper
        assert "if ($rc -eq 0 -or -not $enumerationFailed)" in helper
        assert "emitter\\s+serve(?:\\s|$)" in helper
        assert "schedule\\s+serve(?:\\s|$)" in helper
        assert "Sort-Object { $depth[$_] } -Descending" not in helper

    def test_interactive_hosts_get_a_self_restarting_scheduled_task(self):
        """copilot-extensions#2172: an interactive-service-mode host used to run
        the coordinator/supervisor via a bare HKCU Run auto-start, which has NO
        crash-restart policy -- if the watchdog process died mid-session, nothing
        ever brought it back until the next logon. Both must now prefer an
        Interactive-logon Scheduled Task (RestartCount/RestartInterval), only
        degrading to the historical HKCU Run auto-start when elevation is
        unavailable."""
        text = _ps1_text()

        helper_idx = text.index("function Install-InteractiveScheduledTask")
        helper = text[helper_idx : text.index("\nfunction ", helper_idx + 1)]
        # Register-once: an already-registered Interactive task must be cycled in
        # place (no re-register -> no elevation) on a non-elevated call.
        assert "(-not (Test-Elevated))" in helper
        assert "Stop-ScheduledTask -TaskName $Name" in helper
        assert "Start-ScheduledTask -TaskName $Name" in helper
        # A mismatched leftover (e.g. a stale S4U boot task) must NOT be treated
        # as a match -- only a genuine Interactive-logon task short-circuits.
        assert "$existingTask.Principal.LogonType -eq 'Interactive'" in helper
        assert "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)" in helper
        assert "-LogonType Interactive -RunLevel Limited" in helper
        assert "return $false" in helper, (
            "must return $false (not throw) when elevation is unavailable, so "
            "callers can degrade gracefully to the HKCU Run fallback"
        )

        # Install-CoordinatorTask's generated launcher body is itself a
        # here-string containing literal `function ...` text (e.g.
        # Resolve-WritableLog), so bound by the next known top-level section
        # instead of a blind "\nfunction " scan.
        coord_idx = text.index("function Install-CoordinatorTask")
        coord_end = text.index("# -- Embody supervisor Scheduled Task", coord_idx)
        coord_body = text[coord_idx:coord_end]
        interactive_coord = coord_body[coord_body.index("(Get-ServiceMode) -eq 'interactive'") :]
        assert "Install-InteractiveScheduledTask -Name $TaskName" in interactive_coord
        assert "Start-CoordinatorNonElevatedFallback -Launcher $launcher -Primary" in interactive_coord, (
            "must still fall back to the HKCU Run path when the Scheduled Task "
            "path returns $false (elevation unavailable)"
        )

        sup_idx = text.index("function Install-SupervisorTaskInstance")
        sup_end = text.index("function Install-SupervisorTask {", sup_idx)
        sup_body = text[sup_idx:sup_end]
        interactive_sup = sup_body[sup_body.index("(Get-ServiceMode) -eq 'interactive'") :]
        assert "Install-InteractiveScheduledTask -Name $Name" in interactive_sup
        assert "Install-SupervisorLogonAutostart -Name $Name -Launcher $Launcher -EnvFile $EnvFile" in interactive_sup, (
            "must still fall back to the HKCU Run path when the Scheduled Task "
            "path returns $false (elevation unavailable)"
        )
        # The label-less safety gate (no opt-in label -> fully inert) must still
        # be checked before ever attempting the Scheduled Task path.
        assert "mode -eq 'serve' -or (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)" in interactive_sup

    def test_interactive_scheduled_task_degrades_gracefully_without_cmdlets(self):
        """Install-SupervisorTaskInstance's interactive branch calls
        Install-InteractiveScheduledTask directly, without first checking
        $haveSchedMod (unlike Install-CoordinatorTask's call site) -- so the
        helper itself must guard against the ScheduledTasks cmdlets being
        unavailable and return $false rather than throw, or a host without
        that module would crash the install instead of degrading to the
        HKCU Run fallback."""
        text = _ps1_text()
        helper_idx = text.index("function Install-InteractiveScheduledTask")
        helper = text[helper_idx : text.index("\nfunction ", helper_idx + 1)]
        guard_idx = helper.index("if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue))")
        # The guard must be the first executable statement (before any
        # Get-ScheduledTask/etc. call that would themselves throw/misbehave
        # without the module loaded).
        first_get_scheduled_task = helper.index("Get-ScheduledTask -TaskName $Name")
        assert guard_idx < first_get_scheduled_task
        guard_body = helper[guard_idx : helper.index("\n    }", guard_idx)]
        assert "return $false" in guard_body

    def test_interactive_scheduled_task_nostart_never_stops_a_running_task(self):
        """Install-InteractiveScheduledTask's register-once/cycle-in-place fast
        path used to call Stop-ScheduledTask unconditionally, even under
        -NoStart -- so a caller like Install-CoordinatorTask's graceful-cutover
        path (where a NEW coordinator has already been brought up out-of-band
        and stopping this task would race or kill it) would have its running
        task killed anyway. -NoStart must leave an already-running task
        completely untouched."""
        text = _ps1_text()
        helper_idx = text.index("function Install-InteractiveScheduledTask")
        helper = text[helper_idx : text.index("\nfunction ", helper_idx + 1)]
        fast_path_idx = helper.index("if ($matchingType -and (-not (Test-Elevated)))")
        fast_path = helper[fast_path_idx : helper.index("\n    }\n", fast_path_idx)]
        assert "if ($NoStart) {" in fast_path
        no_start_branch = fast_path[
            fast_path.index("if ($NoStart) {") : fast_path.index("} else {")
        ]
        assert "Stop-ScheduledTask" not in no_start_branch
        assert "Start-ScheduledTask" not in no_start_branch
        else_branch = fast_path[fast_path.index("} else {") :]
        assert "Stop-ScheduledTask -TaskName $Name" in else_branch
        assert "Start-ScheduledTask -TaskName $Name" in else_branch
