"""Tests for the versioned self-install (Phase 2/3 — same convention as the core).

Asserts the shared versioning artifacts: a plain-text ``current-version`` marker,
an immutable ``versions/<ver>/`` slot, and a ``~/.local/bin`` binstub — plus
idempotent, version-gated behavior. No real venv is built (uv is not invoked);
the fast materialize+marker+binstub path is exercised against a synthetic payload
and a synthetic HOME/root.
"""

from __future__ import annotations

from pathlib import Path

import worktree_manager.self_install as si
from worktree_manager.self_install import (
    current_version,
    needs_install,
    payload_version,
    self_install,
    status,
    version_slot,
)
from worktree_manager.__main__ import main


def _fake_payload(tmp: Path, version: str) -> Path:
    pd = tmp / "payload"
    pkg = pd / "src" / "worktree_manager"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pd / "pyproject.toml").write_text("[project]\nname='x'\n")
    return pd


def _patch_local_bin(monkeypatch, tmp: Path) -> Path:
    lb = tmp / ".local" / "bin"
    monkeypatch.setattr(si, "local_bin", lambda: lb)
    return lb


def test_payload_version_reads_init(tmp_path):
    pd = _fake_payload(tmp_path, "9.9.9-dev1")
    assert payload_version(pd) == "9.9.9-dev1"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    res = self_install(pd, root=root, dry_run=True)
    assert res.action == "planned"
    assert res.version == "1.2.3"
    assert current_version(root) is None
    assert not (root / "current-version").exists()


def test_apply_installs_marker_slot_and_binstub(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    # marker file (plain text, names the active version)
    assert (root / "current-version").read_text().strip() == "1.2.3"
    assert current_version(root) == "1.2.3"
    # immutable version slot with the payload copied in
    slot = version_slot("1.2.3", root)
    assert slot.is_dir()
    assert (slot / "src" / "worktree_manager" / "__init__.py").exists()
    # binstub deployed to ~/.local/bin
    stub = lb / "worktree-manager"
    assert stub.exists()
    body = stub.read_text()
    assert "current-version" in body and "worktree-manager" in body


def test_apply_is_idempotent_and_version_gated(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    assert needs_install("1.2.3", root) is False
    again = self_install(pd, root=root, dry_run=False)
    assert again.action == "already-current"


def test_stale_legacy_binstub_content_forces_redeploy(tmp_path, monkeypatch):
    """Regression: a version-current marker + slot must not mask a stale or
    legacy binstub. copilot-extensions#2788-adjacent report -- a prior
    cutover left an old/incompatible ``worktree-manager`` file occupying the
    binstub name, which then failed the consuming agent-worktrees seam's
    ``--version`` health probe and silently fell back to the bundled picker.
    Presence-only idempotency checking hid the problem; content must match.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)

    # Simulate a legacy/incompatible binstub clobbering the deployed one --
    # e.g. left over from an ancient pre-versioned install attempt.
    stub = lb / "worktree-manager"
    stub.write_text("#!/usr/bin/env bash\necho legacy stub; exit 1\n")

    # The marker + slot still say "1.2.3 is installed" -- but the binstub on
    # disk no longer matches what this version would deploy.
    assert needs_install("1.2.3", root) is True

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert "legacy stub" not in stub.read_text()
    assert needs_install("1.2.3", root) is False


def test_known_legacy_prerename_binstub_is_recognized_and_cleaned(tmp_path, monkeypatch):
    """The pre-rename ``worktree-manager`` plugin prototype (before it became
    agent-worktrees, commit ab0716e28..6512114be) shipped this exact
    ``~/.local/bin/worktree-manager`` (+ ``.cmd``) content and a non-versioned
    ``~/.worktree-manager/.venv`` + ``.../lib`` runtime -- both colliding with
    this Manager's own binstub name and root. A machine that installed that
    prototype before the rename can still carry these exact artifacts; a real
    self-install must positively recognize and remove them, not just
    overwrite the binstub incidentally.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)

    lb.mkdir(parents=True)
    (lb / "worktree-manager").write_text(si._LEGACY_PRERENAME_SH, encoding="utf-8", newline="")
    (lb / "worktree-manager.cmd").write_text(
        si._LEGACY_PRERENAME_CMD, encoding="utf-8", newline=""
    )
    legacy_venv = root / ".venv" / "bin"
    legacy_venv.mkdir(parents=True)
    (legacy_venv / "python").write_text("#!/usr/bin/env python\n")
    (root / "lib").mkdir(parents=True)

    # Dry-run reports, but never touches, the recognized legacy artifacts.
    planned = si.plan_legacy_cleanup(root)
    assert any("legacy binstub" in p and "worktree-manager" in p for p in planned)
    assert any("legacy binstub" in p and "worktree-manager.cmd" in p for p in planned)
    assert any("legacy root artifact" in p and ".venv" in p for p in planned)
    assert any("legacy root artifact" in p and str(root / "lib") in p for p in planned)
    assert (root / ".venv").exists() and (root / "lib").exists()

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert len(res.cleaned) == 4
    assert not (root / ".venv").exists()
    assert not (root / "lib").exists()
    # The binstub is now this version's own content, not the legacy one.
    assert (lb / "worktree-manager").read_text() != si._LEGACY_PRERENAME_SH


def test_unrecognized_binstub_content_is_never_attributed_as_legacy(tmp_path, monkeypatch):
    """Only the byte-exact known prototype signature is auto-attributed as
    legacy and named in ``cleaned`` -- an operator's own unrelated file at
    the same path is still replaced (the general staleness fix), but never
    mislabeled or specially called out as a *recognized* legacy artifact."""
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    lb.mkdir(parents=True)
    (lb / "worktree-manager").write_text("#!/usr/bin/env bash\necho mine\n")

    assert si.plan_legacy_cleanup(root) == []
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert res.cleaned == ()


def test_new_version_publishes_new_slot(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    self_install(_fake_payload(tmp_path, "1.0.0"), root=root, dry_run=False)
    # bump the payload version and re-install
    pd2 = _fake_payload(tmp_path / "b", "2.0.0")
    res = self_install(pd2, root=root, dry_run=False)
    assert res.action == "installed"
    assert current_version(root) == "2.0.0"
    assert version_slot("1.0.0", root).is_dir()  # old slot immutable, retained
    assert version_slot("2.0.0", root).is_dir()


def test_status_reports_marker_and_binstub(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "3.3.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    assert status(root).installed is False
    self_install(pd, root=root, dry_run=False)
    st = status(root)
    assert st.installed_version == "3.3.3"
    assert st.binstub is not None


def _fake_monorepo_with_pointer(tmp: Path, version: str) -> Path:
    """A synthetic monorepo shape: <tmp>/monorepo/{worktree-manager,libs,tools}/
    -- the payload's ``libs/<lib>`` carries an UN-materialized vendor pointer,
    ``libs/`` (top-level) carries the real canonical content, and ``tools/``
    carries a real copy of ``materialize_main.py`` (self-contained, stdlib
    only) so ``_materialize_payload_pointers`` can dynamically load it.
    Returns the payload dir (``<tmp>/monorepo/worktree-manager``)."""
    mono = tmp / "monorepo"
    pd = _fake_payload(mono, version)
    pd.rename(mono / "worktree-manager")
    pd = mono / "worktree-manager"

    canon = mono / "libs" / "shared-lib"
    (canon / "src" / "shared_lib").mkdir(parents=True)
    (canon / "src" / "shared_lib" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (canon / "pyproject.toml").write_text(
        '[project]\nname = "shared-lib"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )

    copy_dir = pd / "libs" / "shared-lib"
    (copy_dir / "src" / "shared_lib").mkdir(parents=True)
    (copy_dir / "src" / "shared_lib" / "__init__.py").write_text("# stub\n", encoding="utf-8")
    (copy_dir / "VENDOR_POINTER.json").write_text(
        '{"schema": "copilot-extensions.vendor-pointer", "version": 1, '
        '"source": "libs/shared-lib", "kind": "src-passthrough"}\n',
        encoding="utf-8",
    )

    tools_dir = mono / "tools"
    tools_dir.mkdir(parents=True)
    real_tool = Path(__file__).resolve().parents[2] / "tools" / "materialize_main.py"
    (tools_dir / "materialize_main.py").write_text(
        real_tool.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return pd


def test_self_install_materializes_a_vendor_pointer_from_a_live_monorepo(tmp_path, monkeypatch):
    """A self-installed slot has no libs/+plugins/ monorepo ancestor of its
    own, so a src-passthrough pointer's runtime import would be permanently
    broken there -- self_install must expand any pointer copy into real,
    self-contained content BEFORE publishing the slot, using the still-live
    monorepo ancestor available at copy time (see
    ``_materialize_payload_pointers``)."""
    pd = _fake_monorepo_with_pointer(tmp_path, "5.5.5")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    slot = version_slot("5.5.5", root)
    copy_dir = slot / "libs" / "shared-lib"
    assert not (copy_dir / "VENDOR_POINTER.json").exists()
    assert (copy_dir / "src" / "shared_lib" / "__init__.py").read_text() == "value = 1\n"


def test_self_install_refuses_a_symlinked_tools_directory_from_a_git_checkout(
    tmp_path, monkeypatch,
):
    """Round-13 review finding: the tarball fetch path explicitly rejects a
    symlinked tools/ or materialize_main.py, but self_install() -- the
    ONE shared call site both self_update's git-clone AND tarball paths
    route through via _materialize_payload_pointers -- had no equivalent
    check of its own. A git checkout (not just a tarball) can carry a
    symlinked tools/ just as readily, and _load_materialize_main()
    dynamically EXECUTES that file, so a symlink there could run
    arbitrary code from outside the fetched tree. This exercises the
    fix directly against self_install() (the shared boundary), not just
    the tarball-specific fetch path."""
    pd = _fake_monorepo_with_pointer(tmp_path, "9.1.1")
    mono = pd.parent
    real_tools = mono / "tools"
    outside_tools = tmp_path / "outside-tools"
    outside_tools.mkdir()
    (outside_tools / "materialize_main.py").write_text(
        "import os; os.system('echo pwned')\n", encoding="utf-8"
    )
    import shutil as _shutil
    _shutil.rmtree(real_tools)
    real_tools.symlink_to(outside_tools, target_is_directory=True)

    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "unmaterialized vendor pointers" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("9.1.1", root).exists()


def test_self_install_raises_when_pointer_present_without_monorepo_ancestor(tmp_path, monkeypatch):
    """The dependency-free, no-ancestor case: a pointer copy with no
    reachable canonical source would install successfully but be permanently
    unimportable at runtime -- self_install must report an error (rather
    than crash or silently ship a broken payload), and self_update's own
    "error"-action handling then forwards this cleanly (see
    test_self_update_reports_error_and_cleans_up_when_pointer_unresolvable
    in test_e2e_delivery.py / test_update.py)."""
    pd = _fake_payload(tmp_path, "6.6.6")
    copy_dir = pd / "libs" / "shared-lib"
    (copy_dir / "src" / "shared_lib").mkdir(parents=True)
    (copy_dir / "src" / "shared_lib" / "__init__.py").write_text("# stub\n", encoding="utf-8")
    (copy_dir / "VENDOR_POINTER.json").write_text(
        '{"schema": "copilot-extensions.vendor-pointer", "version": 1, '
        '"source": "libs/shared-lib", "kind": "src-passthrough"}\n',
        encoding="utf-8",
    )
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "unmaterialized vendor pointers" in (res.reason or "")
    # Nothing was published -- a rejected install must not leave a broken
    # slot on disk (it would otherwise be mistaken for a valid install by
    # a later needs_install() existence check) or a marker naming it as
    # current.
    assert current_version(root) is None
    assert not version_slot("6.6.6", root).exists()


def test_self_install_refuses_a_symlink_anywhere_in_the_payload(tmp_path, monkeypatch):
    """Round-9 review finding: symlinks=True on the copytree PRESERVES a
    symlink instead of dereferencing it, but preservation alone doesn't
    make it safe to publish -- a symlink anywhere in the payload (not
    just within a vendor-pointer's canonical src/tests) would survive into
    the slot and could resolve outside it at runtime. self_install must
    reject the whole payload rather than let it through, and must not
    leave the partially-copied slot behind."""
    pd = _fake_payload(tmp_path, "7.7.7")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("should never be reachable from the slot\n")
    (pd / "sneaky-link").symlink_to(outside)
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "symlink" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("7.7.7", root).exists()


def test_bin_directory_is_deployed_into_the_slot(tmp_path, monkeypatch):
    """Phase 3b Slice 2 (Mux relocation): the versioned self-install copies the
    WHOLE payload directory (``_copy_payload`` -> ``shutil.copytree``), so a
    sibling ``bin/`` directory of launcher scripts -- like
    ``worktree-manager/bin/launch-session.{sh,ps1,cmd}`` -- deploys to
    ``<slot>/bin/`` with no self-install code change. This proves that
    mechanism generically with a synthetic script, independent of the real
    launcher scripts' content."""
    pd = _fake_payload(tmp_path, "4.4.4")
    (pd / "bin").mkdir()
    (pd / "bin" / "launch-session.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    slot = version_slot("4.4.4", root)
    deployed = slot / "bin" / "launch-session.sh"
    assert deployed.exists()
    assert deployed.read_text() == (pd / "bin" / "launch-session.sh").read_text()


def test_relocated_launchers_resolve_pane_wrappers_from_their_own_bin():
    root = Path(__file__).resolve().parents[1] / "bin"
    sh = (root / "launch-session.sh").read_text(encoding="utf-8")
    ps1 = (root / "launch-session.ps1").read_text(encoding="utf-8")
    assert 'PANE_WRAPPER="$SCRIPT_DIR/pane-wrapper.sh"' in sh
    assert 'dirname -- "${BASH_SOURCE[0]}"' in sh
    assert "$paneWrapper = Join-Path $PSScriptRoot 'pane-wrapper.ps1'" in ps1
    assert 'RUNTIME_DIR="${AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT:-}"' in sh
    assert 'AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR' in sh
    assert '$RuntimeDir = $env:AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT' in ps1


def test_relocated_launchers_resume_existing_ahp_legs_without_ensuring_new_ones():
    root = Path(__file__).resolve().parents[1] / "bin"
    sh = (root / "launch-session.sh").read_text(encoding="utf-8")
    ps1 = (root / "launch-session.ps1").read_text(encoding="utf-8")
    assert "execution-leg get" in sh
    assert "'execution-leg', 'get'" in ps1
    assert "session-backend" not in sh
    assert "session-backend" not in ps1
    assert 'AGENT_WORKTREES_AHP_AUTH_TOKEN="$GH_TOKEN"' not in sh
    assert "$env:AGENT_WORKTREES_AHP_AUTH_TOKEN = $token.Trim()" not in ps1
    assert "launching via the Worktree Manager Picker" in sh
    assert "launching via the Worktree Manager Picker" in ps1


def test_relocated_launcher_ships_the_mux_status_bar_scripts_it_needs():
    """``launch-session.ps1`` dot-sources ``session-options.ps1`` and
    ``psmux-path.ps1`` (and ``session-options.ps1`` in turn resolves
    ``psmux-passthrough.conf``) via ``$PSScriptRoot``-relative paths, so all
    must be deployed as siblings of the relocated launcher. Omitting them
    left Worktree Manager-launched sessions with a silently unconfigured
    psmux status bar (the failure is swallowed, not a launch error) once
    ``launch-session.ps1`` moved out of ``plugins/agent-worktrees/bin/``."""
    root = Path(__file__).resolve().parents[1] / "bin"
    ps1 = (root / "launch-session.ps1").read_text(encoding="utf-8")
    assert "$script:AwSessionOptions = Join-Path $PSScriptRoot 'session-options.ps1'" in ps1
    assert "$pathHelper = Join-Path $PSScriptRoot 'psmux-path.ps1'" in ps1
    for name in (
        "session-options.ps1",
        "session-options.sh",
        "apply-mux-keybinds.ps1",
        "apply-mux-keybinds.sh",
        "psmux-passthrough.conf",
        "psmux-path.ps1",
    ):
        assert (root / name).is_file(), f"missing {name} beside the relocated launcher"
    session_options_ps1 = (root / "session-options.ps1").read_text(encoding="utf-8")
    assert "Join-Path $PSScriptRoot 'psmux-passthrough.conf'" in session_options_ps1
    assert '$env:AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR' in ps1


def test_copied_psmux_path_helper_matches_its_canonical_source():
    """Phase 3b Sub-slice 2a Step 2 completed the mux-launch cutover:
    ``session-options.*``/``apply-mux-keybinds.*``/``psmux-passthrough.conf``
    were deleted from agent-worktrees along with ``launch-session.*``/
    ``pane-wrapper.*`` -- Worktree Manager's ``bin/`` is now their sole copy,
    with independent regression coverage (see ``test_terminal_decoupling.py``,
    ``test_launch_session_unwrap.py``). ``psmux-path.ps1`` is the one
    exception: agent-worktrees keeps its own copy for install-time psmux
    provisioning (``Ensure-Psmux``/``Ensure-PsmuxSshSafe``) independent of the
    launch scripts, so the two copies of that one helper must stay
    byte-identical rather than silently diverging."""
    repo_root = Path(__file__).resolve().parents[2]
    wm_bin = repo_root / "worktree-manager" / "bin"
    aw_scripts = repo_root / "plugins" / "agent-worktrees" / "scripts"
    copy = wm_bin / "psmux-path.ps1"
    canonical = aw_scripts / "psmux-path.ps1"
    assert copy.read_bytes() == canonical.read_bytes(), (
        f"{copy} has drifted from its canonical source {canonical}; "
        "re-sync both copies verbatim"
    )


def test_self_install_command_dry_run(capsys):
    rc = main(["self-install"])
    out = capsys.readouterr().out
    assert "self-install" in out.lower()
    assert "current-version" in out
    assert rc in (0, 1)


def test_doctor_shows_self_section(capsys):
    main(["doctor"])
    assert "worktree-manager (self)" in capsys.readouterr().out


def test_self_install_normalizes_a_malformed_pointer_failure_to_runtimeerror(tmp_path, monkeypatch):
    """Round-9 review finding: a malformed pointer file (bad JSON) can make
    materialize_libs_dir() raise JSONDecodeError instead of returning a
    SKIP line -- self_install() only translates RuntimeError, so this must
    be normalized at the _materialize_payload_pointers boundary rather
    than letting an unexpected exception type escape self_update's own
    best-effort/non-fatal contract."""
    pd = _fake_monorepo_with_pointer(tmp_path, "8.8.8")
    # Corrupt the pointer file with invalid JSON.
    (pd / "libs" / "shared-lib" / "VENDOR_POINTER.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "pointer materialization" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("8.8.8", root).exists()


def test_self_install_normalizes_a_broken_materializer_load_failure(tmp_path, monkeypatch):
    """A later gap in the same class as the malformed-pointer case above:
    the exception-normalization try only wrapped materialize_libs_dir(),
    but _load_materialize_main() -- which dynamically EXECUTES the
    fetched tools/materialize_main.py via exec_module() -- ran BEFORE
    that boundary. A syntax/import/runtime failure in that file (a
    corrupted or incompatible fetched materializer) escaped
    self_install()'s RuntimeError handler entirely, violating
    self_update's best-effort contract and leaving the already-copied
    slot behind."""
    pd = _fake_monorepo_with_pointer(tmp_path, "8.9.9")
    mono = pd.parent
    (mono / "tools" / "materialize_main.py").write_text(
        "this is not valid python syntax ((((\n", encoding="utf-8"
    )
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "could not load" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("8.9.9", root).exists()
