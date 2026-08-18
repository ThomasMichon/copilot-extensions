"""Tests for project-binstub generation (#25 cross-project WORKTREE_ID)."""

from __future__ import annotations

import platform
from pathlib import Path

from agent_worktrees import installer as inst


def _project_binstub(lb: Path, project: str) -> str:
    name = f"{project}.cmd" if platform.system() == "Windows" else project
    return (lb / name).read_text()


def test_project_binstub_uses_project_flag(monkeypatch, tmp_path: Path):
    """A project binstub names its project via ``--project`` (context otherwise
    resolves from CWD, git-like). It must NOT set an ambient WORKTREE_PROJECT on
    the primary path, nor scrub WORKTREE_ID (identity now comes purely from CWD)."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    content = _project_binstub(lb, "demoproj")
    # Primary path routes through the CLI with an explicit --project.
    assert "--project demoproj" in content
    # No longer scrubs the inherited worktree id -- it is simply ignored.
    assert "WORKTREE_ID" not in content
    assert "APERTURE_WORKTREE_ID" not in content
    # WORKTREE_PROJECT survives ONLY in the recovery (venv-missing) branch,
    # never on the primary CLI path.
    if platform.system() == "Windows":
        assert '"%_PY%" -m agent_worktrees --project demoproj' in content
        assert 'set "WORKTREE_PROJECT=demoproj"' in content  # recovery only
    else:
        assert "-m agent_worktrees --project demoproj" in content
        assert 'export WORKTREE_PROJECT="demoproj"' in content  # recovery only
        # Regression guard (process-storm): the POSIX binstub must resolve the
        # versioned runtime directly (current-version -> versions/<ver>/bin/
        # python), NEVER exec ~/.local/bin/agent-worktrees -- delegating to
        # itself (or the global stub, which delegates to itself) recurses into
        # an unbounded fork/exec storm.
        assert ".local/bin/agent-worktrees" not in content
        assert "current-version" in content
        assert "versions/" in content


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
    assert "current-version" in gcontent and "versions/" in gcontent

    # Per-project stub: resolves the versioned runtime with its OWN --project,
    # never delegating to (and thus recursing through) the global stub.
    pcontent = (lb / "demoproj").read_text()
    assert ".local/bin/agent-worktrees" not in pcontent, "project stub self-references"
    assert "-m agent_worktrees --project demoproj" in pcontent
    assert "current-version" in pcontent and "versions/" in pcontent


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

    for name in ("agent-worktrees.cmd", "demoproj.cmd"):
        content = (lb / name).read_text()
        assert "\\Scripts\\python.exe" in content
        assert "-m agent_worktrees" in content
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

    for name in ("agent-worktrees.cmd", "demoproj.cmd", "demoproj.ps1"):
        content = (lb / name).read_text()
        assert "current-version" in content, f"{name} must resolve via the marker"
        assert "versions" in content
        # The retired junction-target parse must be gone.
        assert "dir /a:l" not in content, f"{name} must not parse a .venv junction"
        assert "\\.venv\\" not in content, f"{name} must not resolve through .venv"


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
    # And it must be the marker-based resolver, not a live-state snapshot.
    if platform.system() == "Windows":
        content = first["demoproj.cmd"].decode()
        assert "current-version" in content
        assert "\\.venv\\" not in content


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
    assert "-m agent_worktrees --project 'demoproj'" in content


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

    # Pre-seed a stale *ours* stub for a project no longer registered.
    for p, c in inst._project_binstub_specs("staleproj"):
        p.write_text(c, newline="")
    # And a foreign stub from another tool (no WORKTREE_PROJECT / --project marker).
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
    # And it resolves the versioned runtime via the marker (static shim identity).
    assert "current-version" in global_stub


def test_deploy_project_binstub_refuses_reserved_name(monkeypatch, tmp_path: Path):
    """The single chokepoint for project-form content refuses the reserved
    runtime name outright, so no caller or deploy ordering can write it."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst._deploy_project_binstub("agent-worktrees") == 0
    for p, _ in inst._project_binstub_specs("agent-worktrees"):
        assert not p.exists()
