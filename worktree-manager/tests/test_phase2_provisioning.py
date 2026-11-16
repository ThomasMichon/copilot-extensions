"""Phase 2 tests: prerequisite detection, provisioning plan, and core install.

No test installs production software or changes the machine. Apply controls
use isolated fake providers; the consumer control provisions only a synthetic
executable beneath the test's temporary directory.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

from worktree_manager.catalog import Catalog, Prereq
from worktree_manager.core_install import (
    CoreInstallResult,
    core_status,
    install_command,
    install_core,
)
from worktree_manager.prereqs import (
    PrereqStatus,
    _ge,
    detect_baseline,
    detect_prereq,
    missing,
)
from worktree_manager.provision import (
    ProvisionAction, ProvisionResult, apply, plan, recipe_for, restart_needed,
)
from worktree_manager.__main__ import main
from worktree_manager import agent_plugin_runtime
from worktree_manager import repository_bootstrap
from _installation_context_fixtures import patch_profile


@pytest.fixture(autouse=True)
def isolated_core_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    patch_profile(monkeypatch, agent_plugin_runtime, tmp_path)


# ── detection ───────────────────────────────────────────────────────────────

def test_version_compare() -> None:
    assert _ge("2.43.0", "2.15")
    assert _ge("3.10.4", "3.10")
    assert not _ge("3.9.1", "3.10")
    assert _ge("anything", "")  # unparseable minimum never blocks


def test_detect_prereq_present_tool() -> None:
    # git is present in CI and locally; detection should find it with a version.
    st = detect_prereq(Prereq(name="git", min="2.15"))
    if st.present:
        assert st.path
        assert st.satisfied


def test_detect_prereq_absent_tool() -> None:
    st = detect_prereq(Prereq(name="definitely-not-a-real-tool-xyz"))
    assert st.present is False
    assert st.satisfied is False


@pytest.mark.parametrize(
    "returncode,output,satisfied",
    [(0, "unrecognized version", False), (1, "Python 3.11.9", False),
     (0, "Python 3.9.5", False), (0, "Python 3.11.9", True)],
)
def test_python_prerequisite_requires_a_successful_supported_version(
    monkeypatch: pytest.MonkeyPatch, returncode: int, output: str, satisfied: bool,
) -> None:
    import subprocess

    from worktree_manager import prereqs

    monkeypatch.setattr(prereqs, "_which", lambda name: ("python3", "selected-python"))
    monkeypatch.setitem(prereqs._EXE_ALIASES, "python3", ())
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, output, ""),
    )
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert status.present
    assert status.path == "selected-python"
    assert status.satisfied is satisfied
    assert PrereqStatus("fixture-tool", present=True, min_required="1.0").satisfied


@pytest.mark.parametrize("first_version", ["3.9.5", None])
@pytest.mark.parametrize("same_alias", [False, True])
def test_python_prerequisite_continues_to_supported_path_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, first_version: str | None, same_alias: bool,
) -> None:
    import os

    from worktree_manager import prereqs

    directories = [str(tmp_path / "older Python"), str(tmp_path / "supported Python")]
    first, supported = [str(Path(directory) / "python.exe") for directory in directories]
    monkeypatch.setenv("PATH", os.pathsep.join(directories))
    mapping = {
        ("python3", None): first,
        ("python3", directories[0]): first,
        ("python3" if same_alias else "python", directories[1]): supported,
    }
    monkeypatch.setattr(
        shutil, "which",
        lambda name, path=None: mapping.get((Path(name).stem.lower(), path)),
    )
    probes = []

    def probe(path: str) -> str | None:
        probes.append(path)
        return {first: first_version, supported: "3.11.9"}[path]

    monkeypatch.setattr(prereqs, "_probe_version", probe)
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert status.satisfied
    assert status.path == supported
    assert probes == [first, supported]
    assert os.environ["PATH"] == os.pathsep.join(directories)


def test_python_prerequisite_stops_at_supported_first_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from worktree_manager import prereqs

    def which(name: str, path: str | None=None) -> str:
        assert path is None
        return "selected-python"

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(prereqs, "_probe_version", lambda path: "3.11.9")
    assert detect_prereq(Prereq("python3", min="3.10")).path == "selected-python"


def test_python_prerequisite_without_candidates_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from worktree_manager import prereqs

    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        prereqs, "_probe_version", lambda path: pytest.fail("no executable to probe"),
    )
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert not status.present
    assert not status.satisfied
    assert status.path is None


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
def test_python_prerequisite_empty_path_entry_searches_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import os

    from worktree_manager import prereqs

    older = tmp_path / "older Python"
    current = tmp_path / "current"
    older.mkdir()
    current.mkdir()
    filename = "python3.exe" if os.name == "nt" else "python3"
    for directory in (older, current):
        executable = directory / filename
        executable.write_bytes(b"synthetic version-probe fixture\n")
        executable.chmod(0o700)
    monkeypatch.chdir(current)
    monkeypatch.setenv("PATH", str(older) + os.pathsep)
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    monkeypatch.setitem(prereqs._EXE_ALIASES, "python3", (filename,))
    monkeypatch.setattr(
        prereqs, "_probe_version",
        lambda path: "3.9.5" if Path(path).samefile(older / filename) else "3.11.9",
    )
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert status.satisfied
    assert status.path is not None
    assert Path(status.path).samefile(current / filename)


@pytest.mark.parametrize("supported_available", [False, True])
@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_python_prerequisite_windowsapps_alias_is_windows_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supported_available: bool, platform: str,
) -> None:
    import os

    from worktree_manager import prereqs

    monkeypatch.setattr(prereqs, "current_os", lambda: platform)
    alias_dir = str(tmp_path / "WindowsApps")
    supported_dir = str(tmp_path / "supported Python")
    alias = str(Path(alias_dir) / "python.exe")
    supported = str(Path(supported_dir) / "python.exe")
    monkeypatch.setenv("PATH", os.pathsep.join([alias_dir, supported_dir]))
    mapping = {
        ("python3", None): alias,
        ("python3", alias_dir): alias,
        ("python", supported_dir): supported if supported_available else None,
    }
    monkeypatch.setattr(
        shutil, "which",
        lambda name, path=None: mapping.get((Path(name).stem.lower(), path)),
    )
    probes = []

    def probe(path: str) -> str | None:
        if platform == "windows":
            assert path != alias
        probes.append(path)
        return "3.11.9"

    monkeypatch.setattr(prereqs, "_probe_version", probe)
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert status.satisfied is (supported_available or platform != "windows")
    assert status.path == (
        supported if platform == "windows" and supported_available else alias
    )
    assert probes == (
        ([supported] if supported_available else []) if platform == "windows" else [alias]
    )


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize(
    "supported_suffix,older_location", [(".exe", "current"), (".cmd", "current"), (".cmd", "path")],
)
def test_python_prerequisite_windows_candidates_cannot_hide_supported_path_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, supported_suffix: str, older_location: str,
) -> None:
    import os

    from worktree_manager import prereqs

    if os.name != "nt":
        pytest.skip("native Windows implicit current-directory search")
    current = tmp_path / "current"
    supported = tmp_path / "supported Python"
    current.mkdir()
    supported.mkdir()
    older = current if older_location == "current" else supported
    for executable in (older / "python.exe", supported / ("python" + supported_suffix)):
        executable.write_bytes(b"synthetic version-probe fixture\n")
    monkeypatch.chdir(current)
    monkeypatch.setenv("PATH", str(supported))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;")
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setitem(prereqs._EXE_ALIASES, "python3", ("python",))
    monkeypatch.setattr(
        prereqs, "_probe_version",
        lambda path: "3.9.5" if Path(path).samefile(older / "python.exe") else "3.11.9",
    )
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert status.satisfied
    assert status.path is not None
    assert Path(status.path).samefile(supported / ("python" + supported_suffix))


@pytest.mark.parametrize(
    "returncode,output,satisfied",
    [(0, "unknown version", False), (1, "PowerShell 7.4.0", False),
     (0, "PowerShell 6.2.7", False), (0, "PowerShell 7.4.0", True)],
)
def test_pwsh_prerequisite_requires_a_successful_supported_version(
    monkeypatch: pytest.MonkeyPatch, returncode: int, output: str, satisfied: bool,
) -> None:
    import subprocess

    from worktree_manager import prereqs

    monkeypatch.setattr(prereqs, "_which", lambda name: ("pwsh", "selected-pwsh"))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, output, ""),
    )
    assert detect_prereq(Prereq("pwsh", min="7.0")).satisfied is satisfied


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_python_prerequisite_probes_the_exact_discovered_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from worktree_manager import prereqs

    monkeypatch.setattr(
        prereqs, "_which",
        lambda name: ("unavailable-python-alias-for-test", sys.executable),
    )
    status = detect_prereq(Prereq("python3", min="3.10"))
    assert status.path == sys.executable
    assert status.version == ".".join(str(part) for part in sys.version_info[:3])
    assert status.satisfied


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.parametrize("selection", ["unusable", "supported", "supported-later"])
def test_setup_qualifies_actual_python_before_core_and_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], selection: str,
) -> None:
    import os
    import sys

    from worktree_manager import prereqs, setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus
    from worktree_manager.provision import ProvisionResult

    python = Path(sys.executable)
    usable = selection != "unusable"
    if selection != "supported":
        python = tmp_path / ("unusable-python.exe" if os.name == "nt" else "unusable-python")
        python.write_bytes(b"not an executable\n")
        python.chmod(0o700)
    candidates: tuple[str, ...] = (str(python),)
    if selection == "supported-later":
        candidates += (sys.executable,)
    monkeypatch.setitem(prereqs._EXE_ALIASES, "python3", candidates)
    monkeypatch.setattr(
        cli, "detect_baseline",
        lambda: [detect_prereq(Prereq("python3", min="3.10"))],
    )
    provisioned = []

    def provision(actions: list[ProvisionAction], **kwargs: Any) -> list[ProvisionResult]:
        provisioned.extend(actions)
        return [
            ProvisionResult(action, False, skipped_reason="fixture does not install")
            for action in actions
        ]

    monkeypatch.setattr(cli, "provision_apply", provision)
    monkeypatch.setattr(
        cli, "core_status",
        lambda: CoreStatus("installed", True, True, None, "fixture-core"),
    )
    monkeypatch.setattr(cli, "install_command", lambda: None)
    calls = []

    def install(**kwargs: Any) -> CoreInstallResult:
        calls.append("core")
        return CoreInstallResult(None, False, reason="already-installed")

    def repository(args: Any) -> int:
        calls.append("repository")
        return 0

    monkeypatch.setattr(cli, "install_core", install)
    monkeypatch.setattr(repository_bootstrap, "run", repository)
    assert main(["setup", "--apply", "--repository", "https://example.invalid/repo"]) == (
        0 if usable else 1
    )
    assert calls == (["core", "repository"] if usable else [])
    assert bool(provisioned) is not usable
    output = capsys.readouterr().out
    if not usable:
        assert "prerequisites still needed: python3" in output
        assert "core installation was not attempted" in output


def test_optional_absent_is_satisfied() -> None:
    st = PrereqStatus(name="psmux", present=False, optional=True)
    assert st.satisfied is True


def test_detect_baseline_covers_catalog() -> None:
    statuses = detect_baseline()
    names = {s.name for s in statuses}
    assert {"git", "python3", "uv"} <= names


@pytest.mark.parametrize("version", ["3.9.5", "3.11.9", None])
def test_baseline_qualifies_prepared_python_without_ambient_fallback(monkeypatch: pytest.MonkeyPatch, version: str | None) -> None:
    import sys
    from worktree_manager import prereqs

    monkeypatch.setattr(
        prereqs, "_which", lambda name: pytest.fail("must not rediscover PATH Python"),
    )
    probes = []

    def probe(path: str) -> str | None:
        probes.append(path)
        return version

    monkeypatch.setattr(prereqs, "_probe_version", probe)
    status, = detect_baseline(Catalog(1, (Prereq("python3", min="3.11"),), ()))
    assert status.path == getattr(sys, "_base_executable", sys.executable)
    assert status.satisfied is (version == "3.11.9")
    assert probes == [getattr(sys, "_base_executable", sys.executable)]


@pytest.mark.parametrize("os_", ["windows", "macos", "linux"])
@pytest.mark.parametrize("pwsh_present", [False, True])
def test_setup_requires_pwsh_only_for_the_windows_core(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], os_: str, pwsh_present: bool,
) -> None:
    from worktree_manager import prereqs, provision, setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus

    monkeypatch.setattr(prereqs, "current_os", lambda: os_)
    monkeypatch.setattr(provision, "current_os", lambda: os_)
    monkeypatch.setattr(
        prereqs, "_which",
        lambda name: (name, None if name == "pwsh" and not pwsh_present else f"fixture-{name}"),
    )
    monkeypatch.setattr(prereqs, "_probe_version", lambda path: "99.0")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: pytest.fail("system-package guidance must not execute an installer"),
    )
    monkeypatch.setattr(
        cli, "core_status",
        lambda: CoreStatus("installed", True, True, None, "fixture-core"),
    )
    monkeypatch.setattr(cli, "install_command", lambda: ["fixture-native-installer"])
    calls = []

    def install(**kwargs: Any) -> CoreInstallResult:
        calls.append("core")
        return CoreInstallResult(None, False, reason="already-installed")

    def repository(args: Any) -> int:
        calls.append("repository")
        return 0

    monkeypatch.setattr(cli, "install_core", install)
    monkeypatch.setattr(repository_bootstrap, "run", repository)
    blocked = os_ == "windows" and not pwsh_present
    assert main(["setup", "--apply", "--repository", "https://example.invalid/repo"]) == (
        1 if blocked else 0
    )
    assert calls == ([] if blocked else ["core", "repository"])
    output = capsys.readouterr().out
    if blocked:
        assert "prerequisites still needed: pwsh" in output
        assert "Microsoft.PowerShell" in output
        assert "fresh terminal" in output
        assert "core installation was not attempted" in output


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
def test_native_setup_missing_pwsh_stops_before_installers_and_repository(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    from agent_procutil import no_window_kwargs

    entered = tmp_path / "owner-entered"
    driver = """
import dataclasses,pathlib,sys
from worktree_manager import prereqs,provision,setup_cli as cli
from worktree_manager.core_install import CoreStatus
catalog = prereqs.load_catalog()
selected = tuple(p for p in catalog.baseline_prereqs if p.name == 'pwsh')
assert selected, 'Windows core host must be declared in the real catalog'
prereqs.load_catalog = lambda: dataclasses.replace(catalog, baseline_prereqs=selected)
prereqs.current_os = lambda: 'windows'
provision.current_os = lambda: 'windows'
def forbidden(*args, **kwargs):
    pathlib.Path(sys.argv[1]).touch()
    raise AssertionError('missing host reached an installer or repository')
provision.subprocess.run = forbidden
cli.install_core = forbidden
cli.repository_bootstrap.run = forbidden
cli.core_status = lambda: CoreStatus('absent', False, False, None, 'fixture-core')
raise SystemExit(cli.cmd_setup(['--apply', '--repository', 'https://example.invalid/repo'], banner='Fixture'))
"""
    environment = {
        **os.environ, "PATH": str(tmp_path), "AGENT_HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path), "HOME": str(tmp_path),
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    result = subprocess.run(
        [sys.executable, "-c", driver, str(entered)], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "prerequisites still needed: pwsh" in result.stdout
    assert "Microsoft.PowerShell" in result.stdout
    assert not entered.exists()


def test_missing_filters_unsatisfied() -> None:
    statuses = [
        PrereqStatus(name="git", present=True, version="2.55", min_required="2.15"),
        PrereqStatus(name="uv", present=False),
        PrereqStatus(name="psmux", present=False, optional=True),
    ]
    names = {s.name for s in missing(statuses)}
    assert names == {"uv"}  # satisfied git out; optional-absent psmux out


# ── provisioning plan ───────────────────────────────────────────────────────

def test_recipe_shapes() -> None:
    uv = recipe_for("uv", "linux")
    assert uv is not None and uv.auto and uv.changes_path
    git = recipe_for("git", "windows")
    assert git is not None and git.manual and not git.auto
    py = recipe_for("python3", "macos")
    assert py is not None and py.requires == "uv"


def test_plan_orders_dependencies_first() -> None:
    gaps = [
        PrereqStatus(name="python3", present=False, min_required="3.10"),
        PrereqStatus(name="uv", present=False),
    ]
    actions = plan(gaps, os_="linux")
    names = [a.name for a in actions]
    assert names.index("uv") < names.index("python3")


def test_apply_dry_run_runs_nothing() -> None:
    gaps = [PrereqStatus(name="uv", present=False)]
    actions = plan(gaps, os_="linux")
    results = apply(actions, dry_run=True)
    assert all(not r.ran for r in results)
    assert all(r.skipped_reason == "dry-run" for r in results if r.action.auto)
    assert restart_needed(results) is False  # nothing actually ran


def test_manual_action_never_runs_even_when_applied() -> None:
    gaps = [PrereqStatus(name="git", present=False)]
    actions = plan(gaps, os_="linux")
    # dry_run=False, but a manual recipe must still not execute.
    results = apply(actions, dry_run=False)
    assert all(not r.ran for r in results)
    assert results[0].skipped_reason == "manual"


# ── core install driver ─────────────────────────────────────────────────────

def test_core_status_absent(tmp_path: Path) -> None:
    st = core_status(home=tmp_path)
    assert st.state == "absent"
    assert not st.installed


def test_core_status_partial(tmp_path: Path) -> None:
    (tmp_path / ".agent-worktrees" / ".venv").mkdir(parents=True)
    st = core_status(home=tmp_path)
    assert st.state == "partial"  # runtime+venv but no binstub


@pytest.mark.parametrize("layout", [".venv", "versions"])
def test_core_status_rejects_empty_runtime_artifacts(tmp_path: Path, layout: str) -> None:
    (tmp_path / ".agent-worktrees" / layout).mkdir(parents=True)
    localbin = tmp_path / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "agent-worktrees").write_text("#!stub\n")
    st = core_status(home=tmp_path)
    assert st.state == "partial"
    assert not st.installed


def _selected_core(home: Path) -> tuple[Path, Path]:
    import json
    import os

    root = home / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "service": "agent-worktrees", "source": {"plugin": "agent-worktrees"},
    }), encoding="utf-8")
    return root, python


def test_core_status_uses_selected_runtime_without_requiring_global_stub(tmp_path: Path) -> None:
    root, _ = _selected_core(tmp_path)
    st = core_status(home=tmp_path)
    assert st.installed
    assert st.runtime_dir == str(root)
    assert st.binstub is None


@pytest.mark.parametrize("marker", ["current-version", "last-known-good"])
@pytest.mark.parametrize("content", [b"\xff", b"1.2.3\x00"])
def test_core_status_malformed_markers_keep_native_fallback(tmp_path: Path, marker: str, content: bytes) -> None:
    root, _ = _selected_core(tmp_path)
    (root / marker).write_bytes(content)
    assert core_status(home=tmp_path).installed


@pytest.mark.parametrize("refusal", ["context", "policy"])
def test_core_status_does_not_override_native_selection_refusal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, refusal: str) -> None:
    _selected_core(tmp_path)
    localbin = tmp_path / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "agent-worktrees").write_text("#!leftover-stub\n")
    if refusal == "context":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "missing-context.json"))
    else:
        policy = tmp_path / ".copilot-extensions" / "installation-mode.json"
        policy.parent.mkdir(parents=True)
        policy.write_text("invalid json", encoding="utf-8")
    assert not core_status(home=tmp_path).installed


def test_core_status_refuses_damaged_selected_runtime(tmp_path: Path) -> None:
    _, python = _selected_core(tmp_path)
    python.unlink()
    (tmp_path / ".agent-worktrees" / ".venv").mkdir(parents=True)
    localbin = tmp_path / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "agent-worktrees").write_text("#!stub\n")
    st = core_status(home=tmp_path)
    assert st.state == "partial"
    assert not st.installed


def test_core_status_uses_canonical_context_selection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from worktree_manager import agent_plugin_runtime, core_install

    selected = tmp_path / "selected-cell" / "agent-worktrees"
    selected.mkdir(parents=True)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "ordinary-state"))
    calls = []

    def resolve(plugin: str, **kwargs: Any) -> Path:
        calls.append((plugin, kwargs))
        return selected / "versions" / "1.2.3"

    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_slot", resolve)
    st = core_install.core_status()
    assert st.installed
    assert st.runtime_dir == str(selected)
    assert calls == [("agent-worktrees", {})]


def test_install_command_from_checkout() -> None:
    cmd = install_command()
    # Inside a checkout the real installer resolves; otherwise None (no checkout).
    if cmd is not None:
        assert "install" in cmd
        assert any("install.ps1" in c or "install.sh" in c for c in cmd)


def test_install_core_is_noop_when_installed(tmp_path: Path) -> None:
    _selected_core(tmp_path)
    res = install_core(dry_run=True, home=tmp_path)
    assert res.ran is False
    assert res.reason == "already-installed"


def test_install_core_dry_run_plans_but_does_not_run(tmp_path: Path) -> None:
    res = install_core(dry_run=True, home=tmp_path)
    assert res.ran is False
    # Either a planned command (checkout present) or a clear no-installer reason.
    assert res.reason in ("dry-run",) or "no-installer" in (res.reason or "")


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
def test_native_setup_never_admits_repository_after_artifact_only_core_install(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    from agent_procutil import no_window_kwargs

    home = tmp_path / "home"
    (home / ".agent-worktrees" / "versions").mkdir(parents=True)
    localbin = home / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "agent-worktrees").write_text("#!leftover-stub\n")
    entered = tmp_path / "repository-entered"
    driver = """
import pathlib,sys
import pytest
sys.path.insert(0, sys.argv[2])
from _installation_context_fixtures import patch_profile
from worktree_manager import agent_plugin_runtime, setup_cli as cli
from worktree_manager.core_install import CoreInstallResult
cli.detect_baseline = lambda: []
cli.provision_plan = lambda gaps: []
cli.provision_apply = lambda *a, **k: []
cli.install_command = lambda: ['synthetic-owner']
cli.install_core = lambda **k: CoreInstallResult(['synthetic-owner'], True, 0)
def repository(args):
    pathlib.Path(sys.argv[1]).touch()
    return 0
cli.repository_bootstrap.run = repository
with pytest.MonkeyPatch.context() as patch:
    patch_profile(patch, agent_plugin_runtime, pathlib.Path(sys.argv[3]))
    raise SystemExit(cli.cmd_setup(['--apply', '--repository', 'https://example.invalid/repo'], banner='Fixture'))
"""
    environment = {**os.environ, "AGENT_HOME": str(home), "USERPROFILE": str(home), "HOME": str(home)}
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    result = subprocess.run(
        [sys.executable, "-c", driver, str(entered), str(Path(__file__).parent), str(home)], env=environment,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "fresh core readiness is required" in result.stdout
    assert not entered.exists()


# ── command surface ─────────────────────────────────────────────────────────

def test_doctor_command_runs(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from worktree_manager import mux_daemon_cutover

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mux_daemon_cutover, "_iter_mux_daemon_pids", lambda: set(),
    )
    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert "doctor" in out.lower()
    assert "agent-worktrees core" in out
    assert rc in (0, 1)


def test_setup_command_dry_run_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["setup"])
    out = capsys.readouterr().out
    assert "plan" in out.lower()
    assert "nothing was changed" in out.lower()
    assert rc in (0, 1)


def _setup_providers(monkeypatch: pytest.MonkeyPatch, *, before: list[PrereqStatus], after: list[PrereqStatus], results: list[ProvisionResult], core_result: CoreInstallResult | None=None) -> list[dict[str, Any]]:
    from worktree_manager import setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus

    probes = iter((before, after))
    monkeypatch.setattr(cli, "detect_baseline", lambda: next(probes))
    monkeypatch.setattr(cli, "provision_apply", lambda *a, **kw: results)
    status = CoreStatus("installed", True, True, "stub", "runtime")
    monkeypatch.setattr(cli, "core_status", lambda: status)
    monkeypatch.setattr(cli, "install_command", lambda: ["owned-installer"])
    calls = []

    def install(**kwargs: Any) -> CoreInstallResult:
        calls.append(kwargs)
        return core_result or CoreInstallResult(None, False, reason="already-installed")

    monkeypatch.setattr(cli, "install_core", install)
    return calls


def test_setup_apply_uses_post_provision_readiness(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus("uv", False)
    action = plan([gap])[0]
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[PrereqStatus("uv", True)],
        results=[ProvisionResult(action, True, returncode=0)],
    )
    assert main(["setup", "--apply"]) == 0
    assert len(calls) == 1
    assert "RESTART" not in capsys.readouterr().out


@pytest.mark.parametrize("returncode", [1, 3010, None])
def test_setup_failed_prerequisite_never_runs_core(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], returncode: int | None) -> None:
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus("uv", False)
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[PrereqStatus("uv", True)],
        results=[ProvisionResult(plan([gap])[0], True, returncode=returncode)],
    )
    assert main(["setup", "--apply"]) == 1
    assert not calls
    assert "prerequisite uv failed" in capsys.readouterr().out


def test_setup_unstarted_prerequisite_reports_reason(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus("uv", False)
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[gap],
        results=[ProvisionResult(plan([gap])[0], False, skipped_reason="access denied")],
    )
    assert main(["setup", "--apply"]) == 1
    assert not calls
    assert "access denied" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["uv", "git"])
def test_setup_unresolved_prerequisite_blocks_core(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str) -> None:
    from worktree_manager.provision import ProvisionResult

    gap = PrereqStatus(name, False)
    action = plan([gap])[0]
    result = (
        ProvisionResult(action, False, skipped_reason="manual") if action.manual
        else ProvisionResult(action, True, returncode=0)
    )
    calls = _setup_providers(
        monkeypatch, before=[gap], after=[gap], results=[result],
    )
    assert main(["setup", "--apply"]) == 1
    assert not calls
    out = capsys.readouterr().out
    assert f"prerequisites still needed: {name}" in out
    assert ("RESTART" in out) == result.ran


@pytest.mark.parametrize(
    "ran,returncode,reason",
    [(True, 7, None), (False, None, "no-installer"), (False, None, None)],
)
def test_setup_core_failure_is_not_hidden_by_installed_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ran: bool, returncode: int | None, reason: str | None,
) -> None:
    from worktree_manager.core_install import CoreInstallResult

    calls = _setup_providers(
        monkeypatch, before=[], after=[], results=[],
        core_result=CoreInstallResult(["owned-installer"], ran, returncode, reason),
    )
    assert main(["setup", "--apply"]) == 1
    assert len(calls) == 1
    assert "core install failed" in capsys.readouterr().out


@pytest.mark.parametrize("installed", [True, False])
def test_setup_first_core_install_checks_postcondition(monkeypatch: pytest.MonkeyPatch, installed: bool) -> None:
    from worktree_manager import setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus

    calls = _setup_providers(
        monkeypatch, before=[], after=[], results=[],
        core_result=CoreInstallResult(["owned-installer"], True, 0),
    )
    absent = CoreStatus("absent", False, False, None, "runtime")
    ready = CoreStatus("installed", True, True, "stub", "runtime")
    states = iter((absent, ready if installed else absent))
    monkeypatch.setattr(cli, "core_status", lambda: next(states))
    assert main(["setup", "--apply"]) == (0 if installed else 1)
    assert len(calls) == 1


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
def test_setup_refreshes_actual_tool_after_synthetic_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    import os
    import shlex
    import subprocess
    import sys

    from worktree_manager import prereqs, setup_cli as cli
    from worktree_manager.core_install import CoreInstallResult, CoreStatus
    from worktree_manager.provision import ProvisionAction

    windows = os.name == "nt"
    tool = tmp_path / ("fixture-tool.cmd" if windows else "fixture-tool")
    content = "@echo 1.0.0\n" if windows else "#!/bin/sh\necho 1.0.0\n"
    script = (
        "from pathlib import Path;"
        f"p=Path({str(tool)!r});p.write_text({content!r});p.chmod(0o700)"
    )
    args = [sys.executable, "-c", script]
    command = subprocess.list2cmdline(args) if windows else shlex.join(args)
    action = ProvisionAction(
        "fixture-tool", "script", command, False, None, False, None,
    )
    monkeypatch.setitem(prereqs._EXE_ALIASES, "fixture-tool", (str(tool),))
    monkeypatch.setattr(
        cli, "detect_baseline",
        lambda: [detect_prereq(Prereq("fixture-tool", min="1.0"))],
    )
    monkeypatch.setattr(cli, "provision_plan", lambda gaps: [action] if gaps else [])
    monkeypatch.setattr(
        cli, "core_status",
        lambda: CoreStatus("installed", True, True, "stub", "runtime"),
    )
    monkeypatch.setattr(cli, "install_command", lambda: None)
    monkeypatch.setattr(
        cli, "install_core",
        lambda **kw: CoreInstallResult(None, False, reason="already-installed"),
    )
    assert not tool.exists()
    assert main(["setup", "--apply"]) == 0
    assert tool.is_file()
    assert prereqs.detect_prereq(Prereq("fixture-tool")).version == "1.0.0"
    assert "RESTART" not in capsys.readouterr().out
    assert main(["setup", "--apply"]) == 0
