"""Guards for the fresh-session self-provisioning bootstrap."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.guard
_SH = shutil.which("sh")
_UV_SETTINGS = """PipInstallSettings { settings: PipSettings {
    index_locations: IndexLocations {
        flat_index: [],
    },
    exclude_newer: ExcludeNewer {
        global: None,
        package: ExcludeNewerPackage({}),
    },
}, }
"""


def _fake_uv_policy_preflight() -> str:
    """Settings queries are read-only; only admitted calls reach fake installs."""
    return f"""
if [[ "${{1:-}}" == pip && "${{2:-}}" == install ]]; then
    for argument in "$@"; do
        if [[ "$argument" == --show-settings ]]; then
            printf '%s\\n' {shlex.quote(_UV_SETTINGS)}
            exit 0
        fi
    done
    saw_cutoff=0
    saw_reinstall=0
    saw_config=0
    pending=''
    for argument in "$@"; do
        if [[ "$pending" == cutoff ]]; then
            [[ "$argument" == *Z ]] || exit 2
            saw_cutoff=1
        elif [[ "$pending" == config ]]; then
            [[ -f "$argument" && "$argument" == */inputs/uv.toml ]] || exit 2
            saw_config=1
        fi
        pending=''
        case "$argument" in
            --exclude-newer) pending=cutoff ;;
            --config-file) pending=config ;;
            --reinstall) saw_reinstall=1 ;;
        esac
    done
    [[ "$saw_cutoff$saw_reinstall$saw_config" == 111 ]] || exit 2
fi
"""


def test_fake_uv_preflight_settings_are_accepted_by_canonical_policy() -> None:
    policy = runpy.run_path(str(PLUGIN / "scripts" / "installer_dependency_policy.py"))
    admitted = policy["cutoff_arguments"](_UV_SETTINGS)
    assert admitted[0] == "--exclude-newer"
    assert admitted[1].endswith("Z")
    assert admitted[2] == "--reinstall"
    preflight = _fake_uv_policy_preflight()
    assert preflight.index("--show-settings") < preflight.index("exit 0")


def _materialize_installed_payload(payload: Path) -> None:
    """Stage the release-owned source graph without granting caller trust."""
    policy = runpy.run_path(str(payload / "scripts" / "installer_dependency_policy.py"))
    source = policy["_toml"](PLUGIN / "pyproject.toml")
    for declaration in source["tool"]["uv"]["sources"].values():
        local = (PLUGIN / declaration["path"]).resolve()
        if local.is_relative_to(PLUGIN.resolve()):
            destination = payload / declaration["path"]
            if not destination.exists():
                shutil.copytree(
                    local, destination,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pytest_cache", "build", "dist", "*.egg-info",
                    ),
                )
    previous_path = sys.path[:]
    try:
        release = runpy.run_path(str(PLUGIN.parents[1] / "tools" / "materialize_main.py"))
        materialized = release["materialize_uv_editable_ref_into"](
            source_consumer_dir=PLUGIN,
            dest_consumer_dir=payload,
            canonical_root=PLUGIN.parents[1],
        )
    finally:
        sys.path[:] = previous_path
    assert not any(message.startswith("SKIP ") for message in materialized), materialized
    project = policy["_toml"](payload / "pyproject.toml")
    manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    assert project["project"]["name"] == manifest["name"] == "agent-worktrees"
    assert project["project"]["version"] == manifest["version"]
    owner_roots = policy["_anchor_roots"]()
    assert owner_roots == (payload.resolve(),)
    for name, declaration in project["tool"]["uv"]["sources"].items():
        assert not declaration.get("editable", False)
        library = (payload / declaration["path"]).resolve()
        assert library.is_relative_to(payload.resolve())
        assert (library / "pyproject.toml").is_file(), name
    policy["_check_project"](payload, owner_roots, set())


def test_installed_bootstrap_payload_has_release_owned_sources(tmp_path: Path) -> None:
    payload = tmp_path / "agent-worktrees"
    for relative in (
        "plugin.json", "pyproject.toml",
        "scripts/installer_dependency_policy.py", "scripts/installer_dependency_config.py",
    ):
        destination = payload / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PLUGIN / relative, destination)
    _materialize_installed_payload(payload)


def test_session_start_prefers_payload_lifecycle_client_with_installed_fallback() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    lifecycle = next(
        entry
        for entry in entries
        if "hook_client.py" in entry.get("bash", "")
    )

    assert 'r="${COPILOT_PLUGIN_ROOT:-}"' in lifecycle["bash"]
    assert 'r="$PWD"' in lifecycle["bash"]
    assert '[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]' in lifecycle["bash"]
    assert 's="$r/scripts/hook_client.py"' in lifecycle["bash"]
    assert '$HOME/.agent-worktrees/bin/hook_client.py' in lifecycle["bash"]
    assert "$env:COPILOT_PLUGIN_ROOT" in lifecycle["powershell"]
    assert "scripts\\hook_client.py" in lifecycle["powershell"]
    assert ".agent-worktrees\\bin\\hook_client.py" in lifecycle["powershell"]
    assert "scripts\\bootstrap-check.ps1" in lifecycle["powershell"]
    assert ".agent-worktrees\\bin\\bootstrap-check.ps1" in lifecycle["powershell"]
    assert "scripts/bootstrap-check.sh" in lifecycle["bash"]
    assert ".agent-worktrees/bin/bootstrap-check.sh" in lifecycle["bash"]

    client = (PLUGIN / "scripts" / "hook_client.py").read_text(encoding="utf-8")
    assert '"bootstrap-check.ps1"' in client
    assert '"bootstrap-check.sh"' in client


def test_bootstrap_stamps_payload_when_runtime_is_unprovisioned() -> None:
    sh = (PLUGIN / "scripts" / "bootstrap-check.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(encoding="utf-8")

    assert 'bash "$_installer" stamp' in sh
    assert "! _aw_provisioned" in sh
    assert "& $installer stamp" in ps1
    assert "& $exe -NoProfile" not in ps1
    assert "--deferred-runtime --run-lifecycle" in ps1
    assert "Test-AwProvisioned" in ps1
    assert "deploy-manifest.json" in sh
    assert "deploy-manifest.json" in ps1


def test_bootstrap_stands_down_for_explicit_installation_context() -> None:
    sh = (PLUGIN / "scripts" / "bootstrap-check.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(encoding="utf-8")

    assert 'if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]' in sh
    assert "if ($env:COPILOT_EXTENSIONS_CONTEXT) { exit 0 }" in ps1
    assert sh.index("COPILOT_EXTENSIONS_CONTEXT") < sh.index('INSTALL_DIR="$HOME/.agent-worktrees"')
    assert ps1.index("COPILOT_EXTENSIONS_CONTEXT") < ps1.index(
        "Join-Path $env:USERPROFILE '.agent-worktrees'"
    )


def test_register_nudge_stands_down_before_legacy_registry_reads() -> None:
    sh = (PLUGIN / "scripts" / "register-nudge.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "register-nudge.ps1").read_text(encoding="utf-8")

    assert sh.index("COPILOT_EXTENSIONS_CONTEXT") < sh.index(
        '$HOME/.agent-worktrees/projects.yaml'
    )
    assert ps1.index("COPILOT_EXTENSIONS_CONTEXT") < ps1.index(
        ".agent-worktrees\\projects.yaml"
    )


def test_windows_binstub_resolves_complete_slots_and_serializes_provision() -> None:
    ps1 = (PLUGIN / "bin" / "agent-worktrees.ps1").read_text(encoding="utf-8")
    cmd = (PLUGIN / "bin" / "agent-worktrees.cmd").read_text(encoding="utf-8")

    assert "resolve-runtime.ps1" in ps1
    assert "System.Threading.Mutex" in ps1
    assert 'agent-worktrees.ps1" %*' in cmd
    assert "%SystemRoot%\\System32\\where.exe" in cmd
    assert "%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" in cmd


def test_posix_binstub_resolves_only_active_or_complete_slots() -> None:
    sh = (PLUGIN / "bin" / "agent-worktrees").read_text(encoding="utf-8")

    assert "resolve-runtime.sh" in sh
    assert '_aw_exec_resolved "$@"' in sh


def _stub_direct_install(home: Path, dir_name: str, sentinel: str) -> Path:
    """Write a stub install.sh under a `_direct` installed-plugins layout
    (``<owner>--<repo>--<subpath-with-dashes>``, no nested agent-worktrees/
    segment) that echoes a caller-chosen sentinel, so a test can prove the
    binstub's own glob resolution located and invoked THIS specific file,
    not merely *some* install.sh."""
    scripts = home / ".copilot" / "installed-plugins" / "_direct" / dir_name / "scripts"
    scripts.mkdir(parents=True)
    install = scripts / "install.sh"
    install.write_text(f"#!/bin/sh\necho {sentinel}\nexit 0\n", encoding="utf-8")
    install.chmod(install.stat().st_mode | stat.S_IEXEC)
    return install


@pytest.mark.skipif(_SH is None, reason="POSIX sh not available")
def test_posix_binstub_self_provisions_from_a_direct_install_layout(tmp_path: Path) -> None:
    """Regression test for a direct (non-marketplace) `copilot plugin
    install <repo>:<path>` install, whose `_direct/<owner>--<repo>--
    <subpath>/` layout has no nested `agent-worktrees/` path segment --
    the marketplace-shaped glob never matches it (copilot-extensions
    issue: agent-worktrees self-provisioning binstub fails on a `_direct`
    install), silently skipping self-provisioning and falling through to a
    guaranteed-to-fail `python -m agent_worktrees` last resort."""
    home = tmp_path / "home"
    home.mkdir()
    _stub_direct_install(
        home, "SomeOwner--some-repo--plugins-agent-worktrees", "DIRECT_INSTALL_SENTINEL"
    )
    # An unrelated direct-installed plugin whose name merely contains the
    # substring "agent-worktrees" (but doesn't end with it) must never be
    # selected instead -- the glob is anchored on the trailing path segment.
    # A DISTINCT sentinel (never asserted true) proves the real installer
    # won, not merely that *some* install.sh ran.
    _stub_direct_install(
        home, "SomeOrg--agent-worktrees-extra-tool--plugins-foo", "DECOY_SENTINEL_MUST_NOT_RUN"
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AGENT_WORKTREES_NO_SELFPROVISION"] = ""
    env.pop("AGENT_RT_ROOT", None)

    proc = subprocess.run(
        ["sh", str(PLUGIN / "bin" / "agent-worktrees"), "--version"],
        capture_output=True,
        text=True,
        env=env,
    )

    # install.sh's own stdout is redirected to the parent's stderr
    # (`bash "$_awinstall" provision >&2`).
    assert "DIRECT_INSTALL_SENTINEL" in proc.stderr, proc.stderr
    assert "DECOY_SENTINEL_MUST_NOT_RUN" not in proc.stderr, proc.stderr


_PWSH = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_windows_binstub_self_provisions_from_a_direct_install_layout(tmp_path: Path) -> None:
    """PowerShell counterpart of the POSIX regression test above: the
    `.ps1` binstub's own `_direct` discovery must select the intended
    installer (ending in `-agent-worktrees`) over an adjacent,
    false-positive-shaped decoy, by actually executing both discovery
    AND the selected install.ps1 -- not merely asserting a regex literal
    is present in the source."""
    home = tmp_path / "home"
    home.mkdir()

    def _stub(dir_name: str, sentinel: str) -> None:
        scripts = home / ".copilot" / "installed-plugins" / "_direct" / dir_name / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "install.ps1").write_text(
            f"Write-Output '{sentinel}'\n", encoding="utf-8"
        )

    _stub("SomeOwner--some-repo--plugins-agent-worktrees", "DIRECT_INSTALL_SENTINEL")
    _stub("SomeOrg--agent-worktrees-extra-tool--plugins-foo", "DECOY_SENTINEL_MUST_NOT_RUN")

    harness = tmp_path / "harness.ps1"
    harness.write_text(
        (PLUGIN / "bin" / "agent-worktrees.ps1")
        .read_text(encoding="utf-8")
        # Exercise only the _direct discovery + invocation, never the real
        # provisioning/mutex/uv machinery below it. `& $_inst` runs the
        # selected install.ps1 directly in the CURRENT PowerShell host --
        # whichever one (pwsh or Windows PowerShell) actually invoked this
        # harness -- rather than hardcoding a `pwsh` child process that
        # would fail on a host where only `powershell.exe` is available.
        .split("if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error", 1)[0]
        + "if ($_inst) { & $_inst } else { Write-Output 'NO_INSTALLER_FOUND' }\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["USERPROFILE"] = str(home)
    env["AGENT_WORKTREES_NO_SELFPROVISION"] = ""

    proc = subprocess.run(
        [_PWSH, "-NoProfile", "-File", str(harness)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert "DIRECT_INSTALL_SENTINEL" in proc.stdout, proc.stderr
    assert "DECOY_SENTINEL_MUST_NOT_RUN" not in proc.stdout, proc.stderr


def test_direct_posix_payload_entrypoints_are_tracked_executable() -> None:
    repo = PLUGIN.parents[1]
    paths = (
        "plugins/agent-worktrees/bin/agent-worktrees",
        "plugins/agent-worktrees/bin/payload/agent-worktrees",
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--stage", "--", *paths],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Git index metadata is unavailable")
    modes = {
        line.split(maxsplit=1)[1].split("\t", maxsplit=1)[1]: line.split()[0]
        for line in result.stdout.splitlines()
    }
    assert modes == {path: "100755" for path in paths}


def test_lean_provision_deploys_runtime_resolvers() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    sh_provision = sh.split("    provision)", 1)[1].split("    install)", 1)[0]
    sh_deploy = sh.split("deploy_runtime_resolvers() {", 1)[1].split(
        "deploy_wrappers() {", 1
    )[0]
    assert "deploy_runtime_resolvers || exit 1" in sh_provision
    assert 'mktemp "$BIN_DIR/$resolver.XXXXXX"' in sh_deploy
    assert 'chmod +x "$tmp"' in sh_deploy
    assert 'mv -f "$tmp" "$BIN_DIR/$resolver"' in sh_deploy

    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    ps1_provision = ps1.split("    'provision' {", 1)[1].split(
        "    'install' {", 1
    )[0]
    ps1_deploy = ps1.split("function Deploy-RuntimeResolvers {", 1)[1].split(
        "function Deploy-Binstub {", 1
    )[0]
    assert "Deploy-RuntimeResolvers" in ps1_provision
    assert "Copy-Item $src $tmp -Force" in ps1_deploy
    assert "Move-Item $tmp $dst -Force" in ps1_deploy


def test_posix_context_install_bootstraps_uv_before_runtime_build() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    install = sh.split("    install)", 1)[1].split("    uninstall)", 1)[0]

    assert "if $CONTEXTUAL_INSTALL; then" in install
    assert "_ensure_uv || exit 1" in install
    assert "_ensure_uv_index" in install
    assert install.index("_ensure_uv || exit 1") < install.index(
        "_deploy_venv_and_package"
    )
    update = sh.split("    update)", 1)[1].split("    *)", 1)[0]
    assert "_ensure_uv || exit 1" in update
    assert "_ensure_uv_index" in update

    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    ps1_update = ps1.split("    'update' {", 1)[1]
    assert "if (-not (Ensure-Uv)) { exit 1 }" in ps1_update
    assert "Ensure-UvIndex" in ps1_update


def test_context_install_revalidates_generations_before_cutover() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    for generation in (
        "activationGeneration",
        "namespaceGeneration",
        "installGeneration",
    ):
        assert generation in sh
        assert generation in ps1
    assert "context_governance_unchanged" in sh
    assert "Test-ContextGovernanceUnchanged" in ps1
    assert sh.index("context_governance_unchanged") < sh.index(
        "_versioned_activate || exit 1"
    )
    assert ps1.index("Test-ContextGovernanceUnchanged") < ps1.index(
        "Invoke-VersionedActivate"
    )
    assert ps1.index("Set-Location -LiteralPath $env:USERPROFILE") < ps1.index(
        "$validatedJson = & $hostExe"
    )
    assert sh.index('cd "$HOME"') < sh.index("__aw_validated_context=")


def test_windows_context_install_uses_shallow_staging_root() -> None:
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    context = ps1.split("if ($ContextualInstall) {", 1)[1].split(
        "# === install-contract:v4 self-stage", 1
    )[0]

    assert "[IO.Path]::GetTempPath()" in context
    assert "'copilot-extensions-install'" in context
    assert "'agent-worktrees'" in context
    assert "Join-Path $InstallDir '.install-stage'" not in context


def test_windows_context_stage_preserves_argument_boundaries() -> None:
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    context = ps1.split("if ($ContextualInstall) {", 1)[1].split(
        "# === install-contract:v4 self-stage", 1
    )[0]

    assert "[Diagnostics.ProcessStartInfo]::new()" in context
    assert "ArgumentList.Add([string]$argument)" in context
    assert "ConvertTo-NativeArgument ([string]$_)" in context
    assert "Start-Process -FilePath $hostExe" not in context
    assert "$contextStart.WorkingDirectory = $env:USERPROFILE" in context
    assert "$env:COPILOT_PLUGIN_INSTALL_STAGED -ne 'context-install'" in context
    assert "-match '/\\.copilot/installed-plugins/'" in context


def test_windows_context_stage_preserves_default_for_invalid_deadline() -> None:
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    context = ps1.split("if ($ContextualInstall) {", 1)[1].split(
        "# === install-contract:v4 self-stage", 1
    )[0]

    assert "$parsedContextDeadline = 0" in context
    assert "[ref]$parsedContextDeadline" in context
    assert "$parsedContextDeadlineOk = [int]::TryParse" in context
    assert "$contextDeadline = $parsedContextDeadline" in context
    assert "[ref]$contextDeadline" not in context


def test_posix_context_stage_reaps_child_group_before_exit() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    context = sh.split(
        "# A structured caller supplies both the validated context", 1
    )[1].split("# === install-contract:v4 self-stage", 1)[0]

    assert "__aw_stop_context_child()" in context
    assert 'kill -- -"$__aw_child"' in context
    assert 'wait "$__aw_child"' in context
    assert "trap '__aw_stop_context_child 143' TERM" in context
    assert 'exec bash -c "$__aw_watchdog"' in context
    assert 'cd "$__aw_staged_payload"' not in context
    assert '${COPILOT_PLUGIN_INSTALL_STAGED:-}" != context-install' in context


def test_installers_preserve_activation_during_inventory_bootstrap() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "-m agent_worktrees.activation_preservation" in sh
    assert "-m agent_worktrees.activation_preservation" in ps1
    assert 'resolve_executable_command_path copilot' in sh
    assert "--copilot-command-json" in ps1
    assert '"$(command -v copilot)"' not in sh
    assert "(Get-Command copilot).Source" not in ps1
    assert "copilot plugin install agent-worktrees@copilot-extensions" not in sh
    assert "copilot plugin install agent-worktrees@copilot-extensions" not in ps1


def test_generated_project_binstubs_use_payload_dispatchers() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh_binstub = sh.split("deploy_binstub() {", 1)[1].split(
        "deploy_global_config()", 1
    )[0]
    ps1_binstub = ps1.split("function Deploy-Binstub {", 1)[1].split(
        "function Deploy-GlobalBinstub {", 1
    )[0]

    assert "bin/payload/agent-worktrees" in sh_binstub
    assert "resolve-runtime.sh" not in sh_binstub
    assert "bin\\payload\\agent-worktrees" in ps1_binstub
    assert "resolve-runtime.ps1" not in ps1_binstub
    assert "last-known-good" not in sh_binstub


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd regression")
def test_windows_binstub_survives_overlong_path(tmp_path: Path) -> None:
    cmd = tmp_path / "agent-worktrees.cmd"
    shutil.copyfile(PLUGIN / "bin" / "agent-worktrees.cmd", cmd)
    (tmp_path / "agent-worktrees.ps1").write_text(
        "Write-Output ($args -join '|')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = ";".join([r"C:\missing"] * 1000)

    proc = subprocess.run(
        [os.environ["ComSpec"], "/d", "/c", str(cmd), "path-overflow"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "path-overflow"


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd regression")
def test_windows_binstub_discovers_pwsh_by_absolute_path(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is unavailable")
    cmd = tmp_path / "agent-worktrees.cmd"
    shutil.copyfile(PLUGIN / "bin" / "agent-worktrees.cmd", cmd)
    (tmp_path / "agent-worktrees.ps1").write_text(
        "Write-Output ($args -join '|')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = str(Path(pwsh).parent)

    proc = subprocess.run(
        [os.environ["ComSpec"], "/d", "/c", str(cmd), "pwsh-discovery"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "pwsh-discovery"


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd regression")
def test_windows_stamp_reuses_immutable_version_snapshot() -> None:
    installer = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    stamp = installer.split("function Invoke-Stamp", 1)[1].split(
        "switch ($Action)", 1
    )[0]

    assert "if (-not (Test-Path $snapDir))" in stamp
    assert "Snapshot already stamped" in stamp
    assert "Remove-Item $snapDir" not in stamp


def _posix_installer_payload(home: Path) -> Path:
    payload = (
        home
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
        / "agent-worktrees"
    )
    shutil.copytree(
        PLUGIN,
        payload,
        ignore=shutil.ignore_patterns(
            "src",
            "libs",
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
            ".test-venvs",
        ),
    )
    # Do not fingerprint the growing runtime sources for a fake package install.
    assert not (payload / "src").exists()
    _materialize_installed_payload(payload)
    for library in (payload / "libs").iterdir():
        source = library / "src"
        if source.is_dir():
            shutil.rmtree(source)
            source.mkdir()
            (source / "fixture.py").write_text("", encoding="utf-8")
    return payload


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer integration")
# Real lifecycle admission, staging, activation and two launcher invocations
# retain the existing item budget; fake uv does not build the runtime package.
@pytest.mark.timeout(90)
def test_posix_lean_provision_installs_resolver_and_launchers_reenter_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    payload = _posix_installer_payload(home)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\nset -eu\n" + _fake_uv_policy_preflight() + """case "${1:-}" in
  venv)
    target="$2"
    mkdir -p "$target/bin" "$target/fake-site/agent_worktrees"
    cat > "$target/bin/python" <<'PY'
#!/bin/sh
set -eu
slot_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
_arg1="${1:-}"
[ "$_arg1" = "-I" ] && shift
if [ "${1:-}" = "-c" ]; then
  case "${2:-}" in
    *"import agent_worktrees, os"*)
      printf '%s\n' "$slot_dir/fake-site/agent_worktrees"
      exit 0
      ;;
    *"import agent_worktrees"*) exit 0 ;;
  esac
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "agent_worktrees" ]; then
  shift 2
  printf 'fake-agent-worktrees %s\n' "$*"
  exit 0
fi
exec "$REAL_PYTHON" "$@"
PY
    chmod +x "$target/bin/python"
    ;;
  pip) ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    monkeypatch.setenv("AGENT_WORKTREES_INSTALL_DEADLINE_SEC", "0")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "REAL_PYTHON": sys.executable,
            "AGENT_WORKTREES_INSTALL_DEADLINE_SEC": "20",
            "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "20",
        }
    )
    assert env["AGENT_WORKTREES_INSTALL_DEADLINE_SEC"] == "20"
    provision = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "provision"],
        cwd=home,
        env=env,
        text=True,
    )
    assert provision.returncode == 0, provision.stdout + provision.stderr

    runtime_bin = home / ".agent-worktrees" / "bin"
    resolver = runtime_bin / "resolve-runtime.sh"
    assert resolver.read_bytes() == (payload / "scripts" / resolver.name).read_bytes()
    assert resolver.stat().st_uid == os.getuid()
    assert resolver.stat().st_mode & stat.S_IXUSR

    launchers = [
        home / ".local" / "bin" / "agent-worktrees",
        payload / "bin" / "payload" / "agent-worktrees",
    ]
    for launcher in launchers:
        launched = subprocess.run(
            [str(launcher), "--version"],
            cwd=home,
            env={**env, "AGENT_WORKTREES_NO_SELFPROVISION": "1"},
            capture_output=True,
            text=True,
        )
        assert launched.returncode == 0, launched.stderr
        assert launched.stdout.strip() == "fake-agent-worktrees --version"


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer integration")
def test_posix_health_gate_rejects_namespace_package_slot(
    tmp_path: Path,
) -> None:
    _assert_posix_namespace_slot_rejected(tmp_path, stall_health_gate=False)


def _assert_posix_namespace_slot_rejected(
    tmp_path: Path, *, stall_health_gate: bool,
) -> None:
    """A partial ``uv pip install`` can leave ``agent_worktrees`` on disk as an
    empty directory -- no ``__init__.py``, no ``__main__.py``, no dist-info.
    Python still imports that as a PEP 420 *namespace* package with no error,
    so a health gate that only runs ``import agent_worktrees`` falsely passes
    and the broken slot gets marked complete and activated (the exact bug
    this regression guards). The fixed gate imports ``agent_worktrees.__main__``
    instead, which requires a real ``__main__.py`` and its full transitive
    import chain to resolve, so it must fail loudly here and the slot must
    never be activated.
    """
    home = tmp_path / "home"
    payload = _posix_installer_payload(home)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\nset -eu\n" + _fake_uv_policy_preflight() + """case "${1:-}" in
  venv)
    target="$2"
    # Simulate the partial install: the package directory exists (so it is
    # importable as a namespace package) but is otherwise empty -- no
    # __init__.py, no __main__.py, no dist-info -- exactly what an
    # interrupted `uv pip install` of the multi-package project left behind.
    mkdir -p "$target/bin" "$target/fake-site/agent_worktrees"
    cat > "$target/bin/python" <<'PY'
#!/bin/sh
set -eu
slot_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
_arg1="${1:-}"
[ "$_arg1" = "-I" ] && shift
if [ "${1:-}" = "-c" ]; then
  if [ "${NAMESPACE_HEALTH_STALL:-}" = "1" ]; then
    case "$2" in *"import agent_worktrees.__main__"*) sleep 60 ;; esac
  fi
  # Exclude the runner venv's real package, which would beat a namespace.
  exec "$REAL_PYTHON" -I -S -c \
    'import sys; sys.path.insert(0, sys.argv[1]); exec(sys.argv[2])' \
    "$slot_dir/fake-site" "$2"
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "agent_worktrees" ]; then
  echo "should never run: broken slot was activated" >&2
  exit 1
fi
exec "$REAL_PYTHON" "$@"
PY
    chmod +x "$target/bin/python"
    "$target/bin/python" -I -c \
      "import agent_worktrees; assert agent_worktrees.__file__ is None"
    printf 'namespace-importable\\n' > "$NAMESPACE_PROOF"
    ;;
  pip) ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "REAL_PYTHON": sys.executable,
            "AGENT_WORKTREES_INSTALL_DEADLINE_SEC": "10",
            "NAMESPACE_HEALTH_STALL": "1" if stall_health_gate else "0",
            "NAMESPACE_PROOF": str(tmp_path / "namespace-proof"),
        }
    )
    provision = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "provision"],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert provision.returncode != 0
    if stall_health_gate:
        assert provision.returncode == 124, provision.stderr
        assert list((home / ".agent-worktrees" / ".install-stage").glob("*/.watchdog-fired"))
    else:
        assert "failed its health gate" in provision.stderr
        assert "No module named 'agent_worktrees.__main__'" in provision.stderr

    # The broken slot must never be marked complete or activated: no
    # current-version marker, and no last-known-good pointing at it.
    install_dir = home / ".agent-worktrees"
    assert not (install_dir / "current-version").exists()
    assert not (install_dir / "last-known-good").exists()
    assert (tmp_path / "namespace-proof").read_text().strip() == "namespace-importable"
    slots = list((install_dir / "versions").iterdir())
    if not stall_health_gate:
        # Normal abort reclaims the owned incomplete candidate. Prove the
        # namespace import above while the constructor still owns that slot.
        assert slots == []
        return
    assert len(slots) == 1
    slot = slots[0]
    assert not (slot / ".install-complete.json").exists()
    namespace = subprocess.run(
        [
            str(slot / "bin" / "python"),
            "-I",
            "-c",
            "import agent_worktrees; assert agent_worktrees.__file__ is None",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert namespace.returncode == 0, namespace.stderr
