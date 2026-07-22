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
"""

from __future__ import annotations

from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parent.parent / "scripts" / "install.sh"


def _text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_install_sh_exists():
    assert INSTALL_SH.is_file(), f"missing {INSTALL_SH}"


def test_supervisor_unit_name_and_launcher_defined():
    text = _text()
    assert 'SUPERVISOR_UNIT="agent-dispatch-supervisor.service"' in text
    assert "SUPERVISOR_LAUNCHER=" in text
    assert "_install_supervisor_service()" in text


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
    """`_install_supervisor_service` must gate `enable`/`restart` behind
    `_supervisor_labels_configured`, and disable/stop otherwise."""
    text = _text()
    assert "_supervisor_labels_configured" in text
    # The enable path is guarded by the label check.
    idx = text.index("_install_supervisor_service()")
    body = text[idx:]
    guard = body.index("if _supervisor_labels_configured; then")
    enable = body.index('systemctl --user enable "$SUPERVISOR_UNIT"')
    disable = body.index('systemctl --user disable "$SUPERVISOR_UNIT"')
    # enable comes inside the positive branch (after the guard);
    # a disable/stop lives in the else branch (after enable).
    assert guard < enable < disable, (
        "enable must be gated by _supervisor_labels_configured, with "
        "disable/stop in the inert (no-label) branch"
    )


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
