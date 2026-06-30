"""Decoupling invariants: agent-worktrees must not own the global mux config.

These are file-level regression guards for the move from a deployed global
``~/.tmux.conf`` to per-session ``tmux set -t`` configuration (issue: relinquish
global tmux/psmux config; apply per-session, opt-in keybinds). They assert the
shape of the shell sources rather than runtime behavior, so a future change that
re-introduces global-config ownership trips a test.
"""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_TERMINAL = _PLUGIN_ROOT / "terminal"
_SESSION_OPTS = _TERMINAL / "session-options.sh"
_KEYBINDS = _TERMINAL / "apply-mux-keybinds.sh"
_LAUNCHER = _PLUGIN_ROOT / "bin" / "launch-session.sh"
_INSTALL = _PLUGIN_ROOT / "scripts" / "install.sh"

# psmux (Windows) counterparts -- the same decoupling, ported to PowerShell.
_SESSION_OPTS_PS = _TERMINAL / "session-options.ps1"
_KEYBINDS_PS = _TERMINAL / "apply-mux-keybinds.ps1"
_LAUNCHER_PS = _PLUGIN_ROOT / "bin" / "launch-session.ps1"
_INSTALL_PS = _PLUGIN_ROOT / "scripts" / "install.ps1"


def test_terminal_scripts_exist():
    assert _SESSION_OPTS.is_file(), "per-session options script must ship"
    assert _KEYBINDS.is_file(), "opt-in keybind script must ship"
    # The legacy global config must be gone.
    assert not (_TERMINAL / "tmux.conf").exists(), "global tmux.conf must not ship"
    assert not (_TERMINAL / "psmux.conf").exists(), "global psmux.conf must not ship"


def test_session_options_are_session_scoped():
    text = _SESSION_OPTS.read_text()
    assert "aw_apply_tmux_session_options" in text
    # Per-session: every `tmux set` targets a session (-t), never a global -g.
    set_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().startswith("tmux set")
    ]
    assert set_lines, "expected tmux set lines"
    for ln in set_lines:
        assert ' -t "$sess"' in ln, f"option must be session-scoped: {ln}"
        assert " -g " not in ln, f"option must not be global: {ln}"


def test_keybind_script_holds_only_server_global_bits():
    text = _KEYBINDS.read_text()
    # The things that cannot be session-scoped live here, and only here.
    assert "escape-time" in text
    assert "unbind-key -a -T root" in text
    # ...and they must NOT appear in any EXECUTABLE line of the per-session
    # script (comments may reference them to explain why they're excluded).
    code = [
        ln for ln in _SESSION_OPTS.read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not any("escape-time" in ln for ln in code)
    assert not any("unbind-key" in ln for ln in code)


def test_keybind_script_persists_managed_block():
    text = _KEYBINDS.read_text()
    # The opt-in script (and ONLY it) may touch ~/.tmux.conf, via a marked,
    # idempotently-replaceable managed block so it survives server restarts.
    assert ".tmux.conf" in text
    assert ">>> agent-worktrees mux keybinds" in text
    assert "--no-persist" in text  # escape hatch: tune running server only


def test_launcher_applies_session_options():
    text = _LAUNCHER.read_text()
    assert "session-options.sh" in text, "launcher must source the options script"
    assert "aw_apply_tmux_session_options" in text or "_aw_apply_session_opts" in text


def test_installer_does_not_own_global_tmux_conf():
    text = _INSTALL.read_text()
    # No deployment of, or drift-overwrite into, ~/.tmux.conf.
    assert "deploy_tmux_config" not in text
    assert 'cp "$src" "$dst"' not in text or "$HOME/.tmux.conf" not in text
    # Uninstall must not delete the user's global config.
    assert 'rm -f "$HOME/.tmux.conf"' not in text
    # The new terminal scripts must be deployed instead.
    assert "deploy_terminal_scripts" in text
    assert "session-options.sh" in text
    assert "apply-mux-keybinds.sh" in text


# --- Status bar reads precomputed @vars, not the CLI on the render path ---


def test_status_bar_reads_at_vars_not_cli():
    """The bar must reference precomputed #{@aw_ctx}/#{@aw_seg} session options,
    never invoke the heavy Python CLI (or a cache-file cat) on the render path."""
    text = _SESSION_OPTS.read_text()
    assert "#{@aw_ctx}" in text, "left segment must read the @aw_ctx var"
    assert "#{@aw_seg}" in text, "right segment must read the @aw_seg var"
    assert "#(agent-worktrees" not in text, (
        "worktree sessions must not invoke the CLI on the render path"
    )
    assert "#(cat " not in text, "the cache-file reader is retired"


def test_launcher_spawns_common_status_updater():
    text = _LAUNCHER.read_text()
    assert "_aw_spawn_status_updater" in text, "launcher must spawn the updater"
    assert "status-updater --session" in text
    assert "--mux tmux" in text, "the tmux launcher must target the tmux watcher"
    # The per-session apply is still threaded with the worktree id (call-site
    # compatibility) even though the watcher classifies by path.
    assert 'aw_apply_tmux_session_options "$1" "${WORKTREE_ID:-}"' in text


def test_status_writer_retired():
    assert not (_TERMINAL / "status-writer.sh").exists(), (
        "the bash status-writer is superseded by the common status-updater"
    )
    install = _INSTALL.read_text()
    # Dropped from the deploy + uninstall loops (only legacy cleanup may name it).
    assert "for script in session-options.sh apply-mux-keybinds.sh; do" in install
    assert "session-options.sh apply-mux-keybinds.sh status-writer.sh" not in install


# --- psmux (Windows) decoupling: the same invariants, ported to PowerShell ---


def test_psmux_session_options_are_session_scoped():
    text = _SESSION_OPTS_PS.read_text()
    assert "Set-AwPsmuxSessionOptions" in text
    # Per-session: psmux options are stamped with `set-option -t <session>`.
    assert "set-option -t $Session" in text, "options must be session-scoped (-t)"
    # No global stamping anywhere in an executable line.
    code = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not any(" -g " in ln for ln in code), "psmux options must not be global (-g)"
    # The status bar reads precomputed @vars, never the heavy CLI on render.
    assert "#{@aw_ctx}" in text and "#{@aw_seg}" in text
    assert "agent-worktrees status" not in text
    # The server-global keystroke bits must NOT live in the per-session script.
    assert not any("unbind-key" in ln for ln in code)
    assert not any("prefix" in ln for ln in code)


def test_psmux_keybind_script_holds_only_server_global_bits():
    text = _KEYBINDS_PS.read_text()
    # The things that cannot be session-scoped live here, and only here.
    assert "unbind-key -a -T root" in text
    assert "prefix C-b" in text
    # Opt-in, persisted as a marked block in ~/.psmux.conf, with an escape hatch.
    assert ".psmux.conf" in text
    assert ">>> agent-worktrees mux keybinds" in text
    assert "NoPersist" in text


def test_psmux_launcher_applies_session_options():
    text = _LAUNCHER_PS.read_text()
    assert "session-options.ps1" in text, "launcher must dot-source the options script"
    assert "Set-AwPsmuxSessionOptions" in text or "Set-AwSessionOptionsSafe" in text


def test_psmux_installer_does_not_own_global_conf():
    text = _INSTALL_PS.read_text()
    # No deployment of, or drift-overwrite into, ~/.psmux.conf.
    assert "Deploy-PsmuxConfig" not in text
    assert "psmux config drift detected" not in text
    # The new terminal scripts must be deployed instead, and the legacy global
    # config relinquished (header-matched), never blindly redeployed.
    assert "Deploy-TerminalScripts" in text
    assert "session-options.ps1" in text
    assert "apply-mux-keybinds.ps1" in text
    assert "Relinquished legacy psmux config" in text
