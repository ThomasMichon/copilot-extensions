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


def test_terminal_scripts_exist():
    assert _SESSION_OPTS.is_file(), "per-session options script must ship"
    assert _KEYBINDS.is_file(), "opt-in keybind script must ship"
    # The legacy global config must be gone.
    assert not (_TERMINAL / "tmux.conf").exists(), "global tmux.conf must not ship"


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


# --- Status bar must read a cache file, not spawn the CLI on the render path ---

_STATUS_WRITER = _TERMINAL / "status-writer.sh"


def test_status_writer_ships():
    assert _STATUS_WRITER.is_file(), "background status writer must ship"


def test_status_bar_reads_cache_file_not_cli():
    """The worktree-id path must read a cache file with a bare `cat`, never
    invoke the heavy Python CLI on the mux render path (the perf regression)."""
    text = _SESSION_OPTS.read_text()
    # Reader is a bare cat of the per-worktree cache file.
    assert "#(cat " in text, "status bar must read the cache file via cat"
    assert "run/status" in text, "cache lives under the run/status dir"
    # The id-bearing branch must NOT shell out to agent-worktrees. Only the
    # id-less base-session fallback may, so scope the check to the wid branch.
    wid_branch = text.split('if [ -n "$wid" ]; then', 1)[1].split("else", 1)[0]
    assert "#(agent-worktrees" not in wid_branch, (
        "worktree sessions must not invoke the CLI on the render path"
    )


def test_writer_is_single_instance_and_self_terminating():
    text = _STATUS_WRITER.read_text()
    assert "flock -n" in text, "writer must guard against duplicate instances"
    assert "tmux has-session" in text, "writer must self-terminate with the session"
    assert "--path" in text, "writer must target the worktree by path"


def test_launcher_spawns_status_writer_with_worktree_id():
    text = _LAUNCHER.read_text()
    assert "_aw_spawn_status_writer" in text, "launcher must spawn the writer"
    assert "status-writer.sh" in text
    # The worktree id must be threaded into the per-session apply.
    assert 'aw_apply_tmux_session_options "$1" "${WORKTREE_ID:-}"' in text


def test_installer_deploys_status_writer():
    text = _INSTALL.read_text()
    assert "status-writer.sh" in text, "installer must deploy the writer"
