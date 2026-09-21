"""Decoupling invariants: the mux launch scripts must not own global mux config.

Migrated from plugins/agent-worktrees/tests/test_terminal_decoupling.py as part
of the Phase 3b Sub-slice 2a Step 2 cutover (efforts/active/worktree-manager-
control-plane/phase-3b-mux-relocation.md): Worktree Manager's bin/ is now the
sole copy of the interactive mux launch scripts (flat, not under a separate
terminal/ subdirectory), so their regression coverage lives here instead of in
agent-worktrees. The installer-specific assertions from the original file are
dropped: Worktree Manager has no per-script install.ps1/install.sh deploy loop
to check -- self_install.py's ``_copy_payload`` copies this whole ``bin/``
directory verbatim (see ``test_self_install.py``).

These are file-level regression guards for the move from a deployed global
``~/.tmux.conf`` to per-session ``tmux set -t`` configuration (issue: relinquish
global tmux/psmux config; apply per-session, opt-in keybinds). They assert the
shape of the shell sources rather than runtime behavior, so a future change that
re-introduces global-config ownership trips a test.
"""

from __future__ import annotations

from pathlib import Path

_BIN = Path(__file__).resolve().parents[1] / "bin"
_SESSION_OPTS = _BIN / "session-options.sh"
_KEYBINDS = _BIN / "apply-mux-keybinds.sh"
_LAUNCHER = _BIN / "launch-session.sh"

# psmux (Windows) counterparts -- the same decoupling, ported to PowerShell.
_SESSION_OPTS_PS = _BIN / "session-options.ps1"
_KEYBINDS_PS = _BIN / "apply-mux-keybinds.ps1"
_LAUNCHER_PS = _BIN / "launch-session.ps1"


def test_terminal_scripts_exist():
    assert _SESSION_OPTS.is_file(), "per-session options script must ship"
    assert _KEYBINDS.is_file(), "opt-in keybind script must ship"
    # The legacy global config must be gone.
    assert not (_BIN / "tmux.conf").exists(), "global tmux.conf must not ship"
    assert not (_BIN / "psmux.conf").exists(), "global psmux.conf must not ship"


def test_session_options_are_session_scoped():
    text = _SESSION_OPTS.read_text(encoding="utf-8")
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
    text = _KEYBINDS.read_text(encoding="utf-8")
    # The things that cannot be session-scoped live here, and only here.
    assert "escape-time" in text
    assert "unbind-key -a -T root" in text
    # ...and they must NOT appear in any EXECUTABLE line of the per-session
    # script (comments may reference them to explain why they're excluded).
    code = [
        ln for ln in _SESSION_OPTS.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not any("escape-time" in ln for ln in code)
    assert not any("unbind-key" in ln for ln in code)


def test_keybind_script_persists_managed_block():
    text = _KEYBINDS.read_text(encoding="utf-8")
    # The opt-in script (and ONLY it) may touch ~/.tmux.conf, via a marked,
    # idempotently-replaceable managed block so it survives server restarts.
    assert ".tmux.conf" in text
    assert ">>> agent-worktrees mux keybinds" in text
    assert "--no-persist" in text  # escape hatch: tune running server only


def test_launcher_applies_session_options():
    text = _LAUNCHER.read_text(encoding="utf-8")
    assert "session-options.sh" in text, "launcher must source the options script"
    assert "aw_apply_tmux_session_options" in text or "_aw_apply_session_opts" in text


# --- Status bar reads precomputed @vars, not the CLI on the render path ---


def test_status_bar_reads_at_vars_not_cli():
    """The bar must reference precomputed #{@aw_ctx}/#{@aw_seg} session options,
    never invoke the heavy Python CLI (or a cache-file cat) on the render path."""
    text = _SESSION_OPTS.read_text(encoding="utf-8")
    assert "#{@aw_ctx}" in text, "left segment must read the @aw_ctx var"
    assert "#{@aw_seg}" in text, "right segment must read the @aw_seg var"
    assert "#(agent-worktrees" not in text, (
        "worktree sessions must not invoke the CLI on the render path"
    )
    assert "#(cat " not in text, "the cache-file reader is retired"


def test_launcher_spawns_common_status_updater():
    text = _LAUNCHER.read_text(encoding="utf-8")
    assert "_aw_spawn_status_updater" in text, "launcher must spawn the updater"
    assert "status-updater --session" in text
    assert "--mux tmux" in text, "the tmux launcher must target the tmux watcher"
    # The per-session apply is still threaded with the worktree id (call-site
    # compatibility) even though the watcher classifies by path.
    assert 'aw_apply_tmux_session_options "$1" "${WORKTREE_ID:-}"' in text


def test_status_writer_retired():
    assert not (_BIN / "status-writer.sh").exists(), (
        "the bash status-writer is superseded by the common status-updater"
    )


# --- psmux (Windows) decoupling: the same invariants, ported to PowerShell ---


def test_psmux_session_options_are_session_scoped():
    text = _SESSION_OPTS_PS.read_text(encoding="utf-8")
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
    text = _KEYBINDS_PS.read_text(encoding="utf-8")
    # The things that cannot be session-scoped live here, and only here.
    assert "unbind-key -a -T root" in text
    assert "prefix C-b" in text
    # Opt-in, persisted as a marked block in ~/.psmux.conf, with an escape hatch.
    assert ".psmux.conf" in text
    assert ">>> agent-worktrees mux keybinds" in text
    assert "NoPersist" in text


def test_psmux_keybind_trailing_blank_trim_never_infinite_loops_on_a_single_line():
    """``Persist-Block``'s trailing-blank-line trim used
    ``$lines[0..($lines.Count - 2)]`` unconditionally. When exactly one
    trailing blank line remains (``$lines.Count -eq 1``), PowerShell's
    ``0..-1`` range yields *two* elements (indices ``0`` and ``-1``, both the
    same lone element) instead of an empty array, so ``$lines`` never shrinks
    and the trim loop hangs forever -- a real, reproducible infinite loop on
    a config with exactly one blank line before the managed block."""
    text = _KEYBINDS_PS.read_text(encoding="utf-8")
    assert "if ($lines.Count -eq 1) { $lines = @() }" in text, (
        "the single-remaining-blank-line case must be special-cased before "
        "slicing with $lines.Count - 2, or the trim loop can hang forever"
    )


def test_psmux_launcher_applies_session_options():
    text = _LAUNCHER_PS.read_text(encoding="utf-8")
    assert "session-options.ps1" in text, "launcher must dot-source the options script"
    assert "Set-AwPsmuxSessionOptions" in text or "Set-AwSessionOptionsSafe" in text
