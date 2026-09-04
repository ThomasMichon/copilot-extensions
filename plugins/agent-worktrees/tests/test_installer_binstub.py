"""Tests for project-binstub generation (#25 cross-project WORKTREE_ID)."""

from __future__ import annotations

import json
import platform
import shlex
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_worktrees import installer as inst

PLUGIN = Path(__file__).resolve().parents[1]


def _project_binstub(lb: Path, project: str) -> str:
    name = f"{project}.cmd" if platform.system() == "Windows" else project
    return (lb / name).read_text()


def test_platform_installers_honor_structured_runtime_root():
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "--install-dir" in posix
    assert "CONTEXTUAL_INSTALL=true" in posix
    assert "does not match validated installation context" in posix
    assert "structured installation context does not support action" in posix
    assert "__aw_child" in posix
    assert "__aw_watcher" in posix
    assert 'ok "Context runtime updated at $INSTALL_DIR"' in posix

    assert "[string]$InstallDir" in powershell
    assert "$ContextualInstall = [bool]$env:COPILOT_EXTENSIONS_CONTEXT" in powershell
    assert "-InstallDir does not match validated installation context." in powershell
    assert "Structured installation context does not support action" in powershell
    assert "$contextChild.WaitForExit" in powershell
    assert "taskkill.exe /PID $contextChild.Id /T /F" in powershell
    assert 'Write-ServiceOk "Context runtime updated at $InstallDir"' in powershell


def test_project_binstub_uses_project_flag(monkeypatch, tmp_path: Path):
    """A project binstub names its project via ``--project`` (context otherwise
    resolves from CWD, git-like). It must not set ambient identity variables."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    content = _project_binstub(lb, "demoproj")
    # Primary path routes through the CLI with an explicit --project.
    assert "--project demoproj" in content
    # No longer scrubs the inherited worktree id -- it is simply ignored.
    assert "WORKTREE_ID" not in content
    assert "APERTURE_WORKTREE_ID" not in content
    assert "WORKTREE_PROJECT" not in content
    assert "agent-worktrees project binstub" in content
    if platform.system() == "Windows":
        assert "bin\\payload\\agent-worktrees.cmd" in content
        assert "--project demoproj" in content
        assert "launch-session.cmd\" --project demoproj" in content
    else:
        assert "bin/payload/agent-worktrees" in content
        assert "--project demoproj" in content
        assert "launch-session.sh\" --project demoproj" in content
        assert ".local/bin/agent-worktrees" not in content


def test_global_stub_does_not_clear_worktree_id(monkeypatch, tmp_path: Path):
    """The global `agent-worktrees` stub is the 'inherit my worktree' path and
    must NOT blank WORKTREE_ID (only project binstubs do)."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    name = "agent-worktrees.cmd" if platform.system() == "Windows" else "agent-worktrees"
    global_stub = (lb / name).read_text()
    assert "WORKTREE_ID" not in global_stub


def test_posix_stubs_never_self_reference(monkeypatch, tmp_path: Path):
    """Regression guard for the fork/exec storm: NO POSIX binstub -- neither the
    global ``agent-worktrees`` stub nor a per-project stub -- may exec
    ``~/.local/bin/agent-worktrees``. The global stub doing so exec'd *itself*
    unboundedly; a per-project stub doing so delegated to the (self-exec'ing)
    global stub. Both must instead resolve the versioned runtime directly and
    run ``-m agent_worktrees``."""
    if platform.system() == "Windows":
        import pytest
        pytest.skip("POSIX-only binstub content")
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    # Global stub (project-agnostic, marker-based resolver): no --project, but
    # must resolve the versioned runtime and never exec itself.
    gcontent = (lb / "agent-worktrees").read_text()
    assert ".local/bin/agent-worktrees" not in gcontent, "global stub self-references"
    assert "-m agent_worktrees" in gcontent
    assert "bin/resolve-runtime.sh" in gcontent
    assert "_aw_exec_resolved" in gcontent

    # Per-project stub: resolves the versioned runtime with its OWN --project,
    # never delegating to (and thus recursing through) the global stub.
    pcontent = (lb / "demoproj").read_text()
    assert ".local/bin/agent-worktrees" not in pcontent, "project stub self-references"
    assert "bin/payload/agent-worktrees" in pcontent
    assert "--project demoproj" in pcontent


def test_windows_binstubs_avoid_unsigned_trampoline(monkeypatch, tmp_path: Path):
    """Smart App Control hard-blocks the unsigned uv console-script trampoline
    (`agent-worktrees.exe`). On Windows the binstubs must launch via the venv's
    signed python.exe with `-m agent_worktrees`, never the .exe trampoline."""
    if platform.system() != "Windows":
        import pytest
        pytest.skip("Windows-only binstub content")
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    global_cmd = (lb / "agent-worktrees.cmd").read_text()
    assert "agent-worktrees.ps1" in global_cmd
    assert "agent-worktrees.exe" not in global_cmd
    global_ps1 = (PLUGIN / "bin" / "agent-worktrees.ps1").read_text()
    assert "resolve-runtime.ps1" in global_ps1
    assert "-m agent_worktrees" in global_ps1
    assert "agent-worktrees.exe" not in global_ps1

    for name in ("demoproj.cmd", "demoproj.ps1"):
        content = (lb / name).read_text()
        assert "bin\\payload\\agent-worktrees" in content
        assert "AGENT_WORKTREES_LAUNCH_ID" in content
        assert "picker-launches.jsonl" in content
        assert "binstub_start" in content
        assert "timestamp_local" not in content
        assert "launch-session" in content
        assert "--project" in content
        assert "agent-worktrees.exe" not in content


def test_windows_binstubs_resolve_via_current_version_marker(monkeypatch, tmp_path: Path):
    """The Windows binstubs resolve the runtime SOLELY via the junction-free
    ``current-version`` marker -> ``versions\\<ver>\\Scripts\\python.exe`` (#1106).
    They must NOT parse/traverse a ``.venv`` reparse point (the old ``dir /a:l``
    junction-target parse is retired -- it broke on ``\\??\\`` targets and drifted,
    dotfiles #1089)."""
    if platform.system() != "Windows":
        import pytest
        pytest.skip("Windows-only binstub content")
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    global_cmd = (lb / "agent-worktrees.cmd").read_text()
    assert "agent-worktrees.ps1" in global_cmd
    global_ps1 = (PLUGIN / "bin" / "agent-worktrees.ps1").read_text()
    assert "current-version" in global_ps1
    assert "resolve-runtime.ps1" in global_ps1
    # The retired junction-target parse must be gone.
    assert "dir /a:l" not in global_ps1
    assert "\\.venv\\" not in global_ps1
    for name in ("demoproj.cmd", "demoproj.ps1"):
        content = (lb / name).read_text()
        assert "bin\\payload\\agent-worktrees" in content


def test_binstub_content_is_independent_of_live_install_state(monkeypatch, tmp_path: Path):
    """Binstub generation must be hermetic (#349).

    The emitted launcher is a *static* template whose runtime slot is resolved at
    **execution** time (via the junction-free ``current-version`` marker), so its
    content must never vary with the machine's live ``~/.agent-worktrees`` state.
    The historical bug: ``deploy_binstubs`` snapshotted the live install dir /
    ``.venv`` / marker at **generation** time, so two runs during an in-flight
    ``update`` (versioned-runtime GC + ``.venv`` repointing mid-swap) emitted
    *different* content for the same file -- coupling the tests to churning
    machine state and making them flaky.

    Regression guard: generate the binstubs twice with every live-runtime
    accessor (``install_dir`` / ``venv_dir`` / ``bin_dir``) monkeypatched at two
    *different* bogus roots. A hermetic generator ignores them entirely, so the
    output must be byte-identical across both runs. If a future change ever reads
    live install state back into the generated content, the two roots diverge and
    this test fails."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(
        inst,
        "_receipt_path",
        lambda project: tmp_path / "receipts" / f"{project}.json",
    )

    def _generate_under(install_root: Path) -> dict[str, bytes]:
        # Point every live-runtime accessor at a distinct, bogus root. A hermetic
        # generator must not consult any of them when building binstub content.
        monkeypatch.setattr(inst, "install_dir", lambda: install_root)
        monkeypatch.setattr(inst, "venv_dir", lambda: install_root / ".venv")
        monkeypatch.setattr(inst, "bin_dir", lambda: install_root / "bin")
        assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True
        return {
            name.name: name.read_bytes()
            for name, _ in inst._project_binstub_specs("demoproj")
        }

    first = _generate_under(tmp_path / "install-A")
    second = _generate_under(tmp_path / "install-B")

    assert first == second, "binstub content must not depend on live install state"
    assert b"bin/payload/agent-worktrees" in next(iter(first.values())).replace(
        b"\\", b"/"
    )


def test_deploy_binstubs_writes_ps1_on_windows(monkeypatch, tmp_path: Path):
    """On Windows ``register``/``deploy_binstubs`` must emit the ``.ps1`` primary
    (pwsh prefers it), not just the ``.cmd`` fallback -- the omission was the
    root cause of the example-ai-hub launcher misbehaving."""
    if platform.system() != "Windows":
        import pytest
        pytest.skip("Windows-only .ps1 primary")
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    ps1 = lb / "demoproj.ps1"
    assert ps1.exists()
    content = ps1.read_text()
    assert "bin\\payload\\agent-worktrees.ps1" in content
    assert "--project 'demoproj'" in content


def test_posix_binstub_launch_trace_uses_portable_date(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")

    specs = inst._project_binstub_specs("demoproj", repo_dir=PLUGIN)

    assert len(specs) == 1
    content = specs[0][1]
    assert "picker-launches.jsonl" in content
    assert "$RANDOM-$(date +%s)" in content
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in content
    assert "if [[ $# -eq 0 ]]" in content
    assert "launch-session.sh" in content
    assert "%N" not in content


def test_wsl_binstub_refuses_legacy_launcher():
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert "grep -q -- 'elif \\[\\[ \"`$arg\" == \"--project\" \\]\\]'" in install_ps1
    assert "agent-worktrees in WSL is too old for explicit project routing." in install_ps1


def _reg(monkeypatch, names: list[str]) -> None:
    monkeypatch.setattr(
        inst, "read_projects_registry",
        lambda: {"projects": {n: {} for n in names}},
    )


def test_reconcile_adds_registered_and_removes_stale(monkeypatch, tmp_path: Path):
    """Reconcile deploys a complete set for every registered project and removes
    signature-matched stubs for deregistered ones."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    _reg(monkeypatch, ["keepproj"])

    # Pre-seed a stale receipt-owned stub for a project no longer registered.
    inst._deploy_project_binstub("staleproj")
    # And a foreign stub from another tool (no project-addressed payload marker).
    foreign = lb / ("othertool.cmd" if platform.system() == "Windows" else "othertool")
    foreign.write_text("@echo off\r\necho not ours\r\n", newline="")

    result = inst.reconcile_binstubs()

    # Registered project deployed (all platform files present).
    for p, _ in inst._project_binstub_specs("keepproj"):
        assert p.exists()
    # Stale ours removed.
    for p, _ in inst._project_binstub_specs("staleproj"):
        assert not p.exists()
    # Foreign spared.
    assert foreign.exists()
    assert "keepproj" in result["registered"]
    assert any("staleproj" in r for r in result["removed"])


def test_reconcile_migrates_matching_legacy_unreceipted_stub(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, ["demo"])
    specs = inst._project_binstub_specs("demo")
    if platform.system() == "Windows":
        for target, _content in specs:
            legacy = (
                '@echo off\r\nset "WORKTREE_PROJECT=demo"\r\n'
                'call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd"\r\n'
                '"C:\\payload\\bin\\payload\\agent-worktrees.cmd" '
                '--project demo %*\r\n'
                if target.suffix.lower() == ".cmd"
                else "$env:WORKTREE_PROJECT = 'demo'\n"
                "& \"$HOME\\.agent-worktrees\\bin\\launch-session.ps1\"\n"
                "& 'C:\\payload\\bin\\payload\\agent-worktrees.ps1' "
                "--project demo @args\n"
            )
            target.write_text(legacy, encoding="utf-8", newline="")
    else:
        target = specs[0][0]
        target.write_text(
            '#!/usr/bin/env bash\nWORKTREE_PROJECT=demo\n'
            '"$HOME/.agent-worktrees/bin/launch-session.sh"\n'
            'exec /payload/bin/payload/agent-worktrees --project demo "$@"\n',
            encoding="utf-8",
        )

    result = inst.reconcile_binstubs()

    assert result["migrated"] == ["demo"]
    assert inst._read_receipt("demo") is not None
    for target, _content in specs:
        deployed = target.read_text(encoding="utf-8")
        assert "agent-worktrees project binstub" in deployed
        assert "WORKTREE_PROJECT" not in deployed


def test_reconcile_preserves_legacy_stub_for_different_project(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, ["demo"])
    target = inst._project_binstub_specs("demo")[0][0]
    target.write_text(
        'WORKTREE_PROJECT=another-project\n.agent-worktrees\n',
        encoding="utf-8",
    )

    result = inst.reconcile_binstubs()

    assert result["migrated"] == []
    assert result["preserved"] == ["demo"]
    assert target.read_text(encoding="utf-8").startswith(
        "WORKTREE_PROJECT=another-project"
    )
    assert inst._read_receipt("demo") is None


def test_reconcile_preserves_unreceipted_explicit_project_wrapper(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, ["demo"])
    target = inst._project_binstub_specs("demo")[0][0]
    wrapper = "python -m agent_worktrees --project demo status\n"
    target.write_text(wrapper, encoding="utf-8")

    result = inst.reconcile_binstubs()

    assert result["migrated"] == []
    assert result["preserved"] == ["demo"]
    assert target.read_text(encoding="utf-8") == wrapper
    assert inst._read_receipt("demo") is None


def test_reconcile_preserves_custom_ambient_project_wrapper(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, ["demo"])
    target = inst._project_binstub_specs("demo")[0][0]
    wrapper = (
        "WORKTREE_PROJECT=demo\n"
        'exec "$HOME/.agent-worktrees/bin/launch-session.sh"\n'
    )
    target.write_text(wrapper, encoding="utf-8")

    result = inst.reconcile_binstubs()

    assert result["migrated"] == []
    assert result["preserved"] == ["demo"]
    assert target.read_text(encoding="utf-8") == wrapper
    assert inst._read_receipt("demo") is None


def test_reconcile_never_touches_reserved_global_name(monkeypatch, tmp_path: Path):
    """A project accidentally registered as ``agent-worktrees`` (e.g. install run
    from the plugin checkout) must never be deployed as a project stub -- that
    would clobber the global launcher."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    _reg(monkeypatch, ["agent-worktrees", "realproj"])

    inst.reconcile_binstubs()

    # Real project deployed; reserved name NOT written as a project stub.
    for p, _ in inst._project_binstub_specs("realproj"):
        assert p.exists()
    for p, _ in inst._project_binstub_specs("agent-worktrees"):
        assert not p.exists()


def test_deploy_binstubs_reserved_project_keeps_global_shim(monkeypatch, tmp_path: Path):
    """Direct-deploy regression (the fork-storm artifact): when ``deploy_binstubs``
    is called with ``project='agent-worktrees'`` -- e.g. the installer inferring
    the project from a dir literally named ``agent-worktrees`` -- the global
    ``agent-worktrees`` stub must remain the project-agnostic marker resolver,
    NOT the self-``--project`` project form. The project-form is what mis-scoped
    the bare global command (and, pre-#708, exec'd itself into a storm)."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="agent-worktrees") is True

    name = "agent-worktrees.cmd" if platform.system() == "Windows" else "agent-worktrees"
    global_stub = (lb / name).read_text()
    # The global shim never names a project -- that is the whole point of the
    # reserved name. A `--project agent-worktrees` here is the clobber bug.
    assert "--project agent-worktrees" not in global_stub
    # And its static PowerShell target resolves the versioned runtime via the
    # marker (the .cmd file is only the shell-selection shim).
    resolver_stub = (
        (PLUGIN / "bin" / "agent-worktrees.ps1").read_text()
        if platform.system() == "Windows"
        else global_stub
    )
    assert "current-version" in resolver_stub


def test_deploy_project_binstub_refuses_reserved_name(monkeypatch, tmp_path: Path):
    """The single chokepoint for project-form content refuses the reserved
    runtime name outright, so no caller or deploy ordering can write it."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst._deploy_project_binstub("agent-worktrees") == 0
    for p, _ in inst._project_binstub_specs("agent-worktrees"):
        assert not p.exists()


def test_project_binstub_writes_attributable_receipt(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    monkeypatch.setattr(
        inst,
        "_binstub_owner",
        lambda repo_dir=None: {
            "marketplace": "example",
            "plugin": "agent-worktrees",
            "payload_root": "/payload/example/agent-worktrees",
            "repository": "https://example.invalid/tools.git",
            "plugin_version": "1.0.0",
        },
    )

    assert inst._deploy_project_binstub("demo") > 0
    receipt = inst._read_receipt("demo")
    assert receipt is not None
    assert receipt["owner"]["marketplace"] == "example"
    assert receipt["project"]["name"] == "demo"
    assert receipt["runtime"]["resolver"] == "payload-local"
    assert set(receipt["stubs"]) == {
        path.name for path, _ in inst._project_binstub_specs("demo")
    }


def test_project_binstub_rejects_ownership_transfer(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    owner = {
        "marketplace": "one",
        "plugin": "agent-worktrees",
        "payload_root": "/payload/one/agent-worktrees",
        "repository": "https://example.invalid/tools.git",
        "plugin_version": "1.0.0",
    }
    monkeypatch.setattr(inst, "_binstub_owner", lambda repo_dir=None: dict(owner))
    inst._deploy_project_binstub("demo")
    before = {path: path.read_bytes() for path, _ in inst._project_binstub_specs("demo")}

    owner["marketplace"] = "two"
    owner["payload_root"] = "/payload/two/agent-worktrees"
    with pytest.raises(inst.BinstubOwnershipError, match="ownership transfer"):
        inst._deploy_project_binstub("demo")
    assert {path: path.read_bytes() for path in before} == before


def test_project_binstub_preserves_unreceipted_foreign_file(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    lb.mkdir()
    target = inst._project_binstub_specs("demo")[0][0]
    target.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")

    with pytest.raises(inst.BinstubOwnershipError, match="unreceipted"):
        inst._deploy_project_binstub("demo")
    assert target.read_text(encoding="utf-8") == "#!/bin/sh\necho foreign\n"


def test_project_binstub_requires_transfer_for_exact_legacy_signature(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    lb.mkdir()
    target = inst._project_binstub_specs("demo")[0][0]
    target.write_text(
        "#!/bin/sh\nexec python -m agent_worktrees --project demo \"$@\"\n",
        encoding="utf-8",
    )

    with pytest.raises(inst.BinstubOwnershipError, match="unreceipted"):
        inst._deploy_project_binstub("demo")
    assert "python -m agent_worktrees" in target.read_text(encoding="utf-8")

    _reg(monkeypatch, ["demo"])
    inst.transfer_project_binstub("demo")
    assert inst._read_receipt("demo") is not None
    content = target.read_text(encoding="utf-8").replace("\\", "/")
    assert "bin/payload/agent-worktrees" in content


def test_legacy_environment_binstub_remains_detectable_for_migration():
    content = (
        "#!/usr/bin/env bash\n"
        "export WORKTREE_PROJECT=demo\n"
        "exec \"$HOME/.agent-worktrees/bin/launch-session.sh\"\n"
    )
    assert inst._is_project_binstub(content)


def test_registration_preflight_rejects_transfer_before_registry_mutation(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    owner = {
        "marketplace": "one",
        "plugin": "agent-worktrees",
        "payload_root": "/payload/one/agent-worktrees",
        "repository": "https://example.invalid/tools.git",
        "plugin_version": "1.0.0",
    }
    monkeypatch.setattr(inst, "_binstub_owner", lambda repo_dir=None: dict(owner))
    inst._deploy_project_binstub("demo", repo_dir=tmp_path)
    owner["marketplace"] = "two"
    owner["payload_root"] = "/payload/two/agent-worktrees"
    mutated = False

    with pytest.raises(inst.BinstubOwnershipError, match="ownership transfer"):
        with inst.project_binstub_registration(
            "demo", repo_dir=tmp_path
        ) as registration:
            mutated = True
            registration.commit()

    assert mutated is False


def test_registration_preserves_unreceipted_command_without_aborting(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    lb.mkdir()
    target = inst._project_binstub_specs("demo", repo_dir=tmp_path)[0][0]
    target.write_text("#!/bin/sh\necho legacy\n", encoding="utf-8")
    mutated = False

    with inst.project_binstub_registration("demo", repo_dir=tmp_path):
        mutated = True

    assert mutated is True
    assert target.read_text(encoding="utf-8") == "#!/bin/sh\necho legacy\n"
    assert inst._read_receipt("demo") is None


def test_registration_serializes_shared_registries_before_project(
    monkeypatch, tmp_path: Path
):
    events: list[str] = []

    @contextmanager
    def _lock(name: str):
        events.append(f"acquire:{name}")
        try:
            yield
        finally:
            events.append(f"release:{name}")

    monkeypatch.setattr(inst, "_binstub_lock", _lock)
    monkeypatch.setattr(
        inst,
        "_project_binstub_context",
        lambda *args, **kwargs: events.append("preflight"),
    )
    monkeypatch.setattr(
        inst,
        "_deploy_project_binstub_unlocked",
        lambda *args, **kwargs: events.append("publish"),
    )

    with inst.project_binstub_registration(
        "demo", repo_dir=tmp_path
    ) as registration:
        events.append("registry-write")
        registration.commit()

    assert events == [
        "acquire:__registries__",
        "acquire:demo",
        "preflight",
        "registry-write",
        "publish",
        "release:demo",
        "release:__registries__",
    ]


def test_registration_without_commit_does_not_publish(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")
    monkeypatch.setattr(inst, "install_dir", lambda: tmp_path / "runtime")

    with inst.project_binstub_registration("demo", repo_dir=tmp_path):
        pass

    assert not inst._project_binstub_specs("demo", repo_dir=tmp_path)[0][0].exists()
    assert inst._read_receipt("demo") is None


def test_reconcile_preserves_modified_receipt_owned_stub(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, [])
    inst._deploy_project_binstub("demo")
    target = inst._project_binstub_specs("demo")[0][0]
    target.write_text("modified\n", encoding="utf-8")

    result = inst.reconcile_binstubs()

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "modified\n"
    expected_removed = [
        str(path) for path, _ in inst._project_binstub_specs("demo")[1:]
    ]
    assert set(result["removed"]) == set(expected_removed)


def test_explicit_transfer_replaces_other_owner(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, ["demo"])
    owner = {
        "marketplace": "one",
        "plugin": "agent-worktrees",
        "payload_root": "/payload/one/agent-worktrees",
        "repository": "https://example.invalid/tools.git",
        "plugin_version": "1.0.0",
    }
    monkeypatch.setattr(inst, "_binstub_owner", lambda repo_dir=None: dict(owner))
    inst._deploy_project_binstub("demo")
    owner["marketplace"] = "two"
    owner["payload_root"] = "/payload/two/agent-worktrees"

    inst.transfer_project_binstub("demo")

    receipt = inst._read_receipt("demo")
    assert receipt is not None
    assert receipt["owner"]["marketplace"] == "two"


def test_reconcile_continues_past_foreign_owner(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    owner = {
        "marketplace": "one",
        "plugin": "agent-worktrees",
        "payload_root": "/payload/one/agent-worktrees",
        "repository": "https://example.invalid/tools.git",
        "plugin_version": "1.0.0",
    }
    monkeypatch.setattr(inst, "_binstub_owner", lambda repo_dir=None: dict(owner))
    _reg(monkeypatch, ["foreign", "local"])
    inst._deploy_project_binstub("foreign")
    owner["marketplace"] = "two"
    owner["payload_root"] = "/payload/two/agent-worktrees"

    result = inst.reconcile_binstubs()

    assert "foreign" in result["preserved"]
    assert inst._project_binstub_specs("local")[0][0].exists()


def test_explicit_payload_root_precedes_stale_manifest(monkeypatch, tmp_path: Path):
    current = tmp_path / "current"
    stale = tmp_path / "stale"
    for root in (current, stale):
        root.mkdir()
        (root / "plugin.json").write_text(
            '{"name":"agent-worktrees"}\n', encoding="utf-8"
        )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"source": {"path": str(stale)}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(inst, "install_dir", lambda: runtime)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(current))

    assert inst._payload_root() == current.resolve()


def test_project_binstub_escapes_payload_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")
    payload = tmp_path / "payload with 'quote' and $dollar"
    monkeypatch.setattr(inst, "_payload_root", lambda repo_dir=None: payload)

    if platform.system() == "Windows":
        specs = dict(inst._project_binstub_specs("demo"))
        ps1 = specs[tmp_path / "bin" / "demo.ps1"]
        assert str(payload).replace("'", "''") in ps1
    else:
        content = inst._project_binstub_specs("demo")[0][1]
        assert shlex.quote(str(payload / "bin/payload/agent-worktrees")) in content


def test_payload_shims_propagate_ownership_root() -> None:
    posix = (PLUGIN / "bin" / "payload" / "agent-worktrees").read_text(
        encoding="utf-8"
    )
    powershell = (
        PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"
    ).read_text(encoding="utf-8")
    assert 'export AGENT_WORKTREES_PAYLOAD_ROOT="$_payload_root"' in posix
    assert "$env:AGENT_WORKTREES_PAYLOAD_ROOT = $_payloadRoot" in powershell
    assert "[Environment]::GetEnvironmentVariable" in powershell
    assert "Remove-Item Env:AGENT_WORKTREES_PAYLOAD_ROOT" in powershell


def test_project_binstub_rejects_invalid_command_name(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")
    monkeypatch.setattr(inst, "install_dir", lambda: tmp_path / "runtime")
    with pytest.raises(ValueError, match="project command name"):
        inst._deploy_project_binstub("bad & name")


def test_reconcile_preserves_malformed_stale_receipt(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, [])
    inst._deploy_project_binstub("demo")
    inst._receipt_path("demo").write_text("{bad", encoding="utf-8")

    result = inst.reconcile_binstubs()

    assert "demo" in result["preserved"]
    assert inst._project_binstub_specs("demo")[0][0].exists()


def test_explicit_transfer_recovers_malformed_receipt(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, ["demo"])
    inst._deploy_project_binstub("demo")
    inst._receipt_path("demo").write_text("{bad", encoding="utf-8")

    inst.transfer_project_binstub("demo")

    assert inst._read_receipt("demo") is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [("owner", []), ("project", "bad"), ("stubs", [])],
)
def test_reconcile_preserves_structurally_invalid_receipt(
    monkeypatch, tmp_path: Path, field: str, value: object
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, [])
    inst._deploy_project_binstub("demo")
    receipt = json.loads(inst._receipt_path("demo").read_text(encoding="utf-8"))
    receipt[field] = value
    inst._receipt_path("demo").write_text(
        json.dumps(receipt), encoding="utf-8"
    )

    result = inst.reconcile_binstubs()

    assert "demo" in result["preserved"]
    assert inst._project_binstub_specs("demo")[0][0].exists()


def test_native_reconcilers_pin_stable_staged_origin() -> None:
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert '${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}' in posix
    assert "$env:COPILOT_PLUGIN_STAGED_FROM" in powershell
    assert '"path": "$stable_plugin"' in posix
    assert "$pluginPath = if ($env:COPILOT_PLUGIN_STAGED_FROM)" in powershell
    assert "Project registration failed (exit $LASTEXITCODE)" in powershell
    assert (
        'AGENT_WORKTREES_PAYLOAD_ROOT="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"'
        in posix
    )
    assert "$env:AGENT_WORKTREES_PAYLOAD_ROOT = if " in powershell
    assert 'reconcile-binstubs --remove "$PROJECT_NAME"' in posix
    assert "reconcile-binstubs --remove $ProjectName" in powershell


def test_receipt_hashes_the_bytes_left_on_disk(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    monkeypatch.setattr(inst.platform, "system", lambda: "Windows")
    lb.mkdir()
    for path, content in inst._project_binstub_specs("Demo"):
        path.write_text(
            content.replace("\r\n", "\n"),
            encoding="utf-8",
            newline="",
        )
    _reg(monkeypatch, ["Demo"])

    inst.transfer_project_binstub("Demo")

    receipt = inst._read_receipt("demo")
    assert receipt is not None
    for path, _content in inst._project_binstub_specs("Demo"):
        assert receipt["stubs"][path.name.casefold()] == inst._file_sha256(path)


def test_windows_receipt_hash_lookup_is_case_insensitive(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(inst.platform, "system", lambda: "Windows")
    path = tmp_path / "Demo.CMD"
    path.write_bytes(b"launcher\r\n")
    receipt = {"stubs": {"demo.cmd": inst._file_sha256(path)}}

    assert inst._stub_hash(receipt, path) == inst._file_sha256(path)


def test_project_identity_canonicalizes_registered_anchor(
    monkeypatch, tmp_path: Path
):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    class _Entry:
        remote = ""

        @staticmethod
        def local_path():
            return str(linked)

    monkeypatch.setattr("agent_worktrees.repos.find_repo", lambda _name: _Entry())

    assert inst._project_identity("demo") == inst._project_identity(
        "demo", repo_dir=real
    )


def test_remove_project_binstub_requires_owner_and_exact_hash(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    inst._deploy_project_binstub("demo")
    paths = [path for path, _ in inst._project_binstub_specs("demo")]

    removed = inst.remove_project_binstub("demo")

    assert set(removed) == set(paths)
    assert not inst._receipt_path("demo").exists()
    assert not any(path.exists() for path in paths)


def test_remove_project_binstub_preserves_modified_file(
    monkeypatch, tmp_path: Path
):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    inst._deploy_project_binstub("demo")
    target = inst._project_binstub_specs("demo")[0][0]
    target.write_text("modified\n", encoding="utf-8")

    with pytest.raises(inst.BinstubOwnershipError, match="modified"):
        inst.remove_project_binstub("demo")
    assert target.exists()
    assert inst._receipt_path("demo").exists()


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX dotted name")
def test_reconcile_discovers_dotted_project_name(monkeypatch, tmp_path: Path):
    lb = tmp_path / "bin"
    root = tmp_path / "runtime"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    monkeypatch.setattr(inst, "install_dir", lambda: root)
    _reg(monkeypatch, [])
    inst._deploy_project_binstub("example.tools")
    target = inst._project_binstub_specs("example.tools")[0][0]

    result = inst.reconcile_binstubs()

    assert str(target) in result["removed"]
    assert not target.exists()
