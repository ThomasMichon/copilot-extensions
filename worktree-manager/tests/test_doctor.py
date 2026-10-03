from __future__ import annotations

import json
from types import SimpleNamespace

from worktree_manager import __main__ as wm


def _status(ok: bool, *, name: str = "git") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        present=ok,
        satisfied=ok,
        optional=False,
        version="1.0.0" if ok else None,
        min_required="1.0.0",
        path="/usr/bin/git" if ok else None,
        notes=None,
    )


def _core() -> SimpleNamespace:
    return SimpleNamespace(
        state="installed",
        runtime_dir="/tmp/runtime",
        runtime_present=True,
        venv_present=True,
        binstub="/tmp/bin/agent-worktrees",
        installed=True,
    )


def _self() -> SimpleNamespace:
    return SimpleNamespace(
        installed_version="1.2.3",
        binstub="/tmp/bin/worktree-manager",
        root="/tmp/wtm",
    )


def _cov(*, source_kind="checkout", uncovered=(), phantom=(), gaps=()):
    from worktree_manager.model import Coverage

    return Coverage(
        source_kind=source_kind,
        uncovered=tuple(uncovered),
        phantom=tuple(phantom),
        published_prereq_gaps=tuple(gaps),
    )


def test_doctor_json_includes_daemon_health(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    monkeypatch.setattr(doctor_cli, "detect_baseline", lambda: [_status(True)])
    monkeypatch.setattr(doctor_cli, "core_status", _core)
    monkeypatch.setattr(doctor_cli, "self_status", _self)
    monkeypatch.setattr(doctor_cli, "coverage", lambda: _cov())
    monkeypatch.setattr(source_config, "configured_source", lambda: ("", ""))
    monkeypatch.setattr(source_config, "resolved_repo", lambda: "repo")
    monkeypatch.setattr(source_config, "resolved_ref", lambda: "dev")
    monkeypatch.setattr(
        doctor_cli.daemon_health,
        "doctor_report",
        lambda *, apply: {
            "mode": "apply" if apply else "report",
            "findings": [],
            "counts": {"total": 0},
        },
    )

    assert wm.main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["daemon_health"]["mode"] == "report"
    assert payload["daemon_health"]["counts"]["total"] == 0
    assert payload["plugin_alignment"] == {
        "source_kind": "checkout",
        "ok": True,
        "uncovered": [],
        "phantom": [],
        "published_prereq_gaps": [],
    }


def test_doctor_apply_renders_daemon_actions(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    monkeypatch.setattr(doctor_cli, "detect_baseline", lambda: [_status(True)])
    monkeypatch.setattr(doctor_cli, "core_status", _core)
    monkeypatch.setattr(doctor_cli, "self_status", _self)
    monkeypatch.setattr(doctor_cli, "coverage", lambda: _cov())
    monkeypatch.setattr(source_config, "configured_source", lambda: ("", ""))
    monkeypatch.setattr(source_config, "resolved_repo", lambda: "repo")
    monkeypatch.setattr(source_config, "resolved_ref", lambda: "dev")
    monkeypatch.setattr(
        doctor_cli.daemon_health,
        "doctor_report",
        lambda *, apply: {
            "mode": "apply" if apply else "report",
            "findings": [
                {
                    "kind": "duplicate_resident",
                    "summary": "more than one live daemon matches the resident active slot",
                    "targets": [{"pid": 202, "start_time": "dup"}],
                }
            ],
            "before": {
                "findings": [
                    {
                        "kind": "duplicate_resident",
                        "summary": "more than one live daemon matches the resident active slot",
                        "targets": [{"pid": 202, "start_time": "dup"}],
                    }
                ]
            },
            "after": {"findings": []},
            "remaining_findings": [],
            "actions": [
                {
                    "kind": "duplicate_resident",
                    "pid": 202,
                    "termination": {"killed": True, "method": "fake"},
                }
            ],
        },
    )

    assert wm.main(["doctor", "--apply-daemon-health"]) == 0
    out = capsys.readouterr().out
    assert "mux-daemon health (fix)" in out
    assert "duplicate_resident: pid 202 -> terminated (fake)" in out


def _patch_common(monkeypatch, doctor_cli, source_config, *, cov):
    monkeypatch.setattr(doctor_cli, "detect_baseline", lambda: [_status(True)])
    monkeypatch.setattr(doctor_cli, "core_status", _core)
    monkeypatch.setattr(doctor_cli, "self_status", _self)
    monkeypatch.setattr(doctor_cli, "coverage", lambda: cov)
    monkeypatch.setattr(source_config, "configured_source", lambda: ("", ""))
    monkeypatch.setattr(source_config, "resolved_repo", lambda: "repo")
    monkeypatch.setattr(source_config, "resolved_ref", lambda: "dev")
    monkeypatch.setattr(
        doctor_cli.daemon_health,
        "doctor_report",
        lambda *, apply: {"mode": "apply" if apply else "report", "findings": []},
    )


def test_doctor_reports_clean_plugin_alignment(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    _patch_common(monkeypatch, doctor_cli, source_config, cov=_cov())

    assert wm.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "plugin alignment:" in out
    assert "every discovered plugin has an authored catalog entry; no drift" in out


def test_doctor_reports_uncovered_without_claiming_no_drift(monkeypatch, capsys):
    """Uncovered-only coverage is non-blocking (inferred defaults), but must not
    be rendered as a plain 'no drift' success -- it's still listed as missing
    catalog knowledge."""
    from worktree_manager import doctor_cli, source_config

    _patch_common(
        monkeypatch, doctor_cli, source_config,
        cov=_cov(uncovered=["brand-new-plugin"]),
    )

    assert wm.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "discovered but not in the authored catalog" in out
    assert "brand-new-plugin" in out
    assert "no errors (uncovered plugins are handled by inference)" in out
    assert "no drift" not in out


def test_doctor_fails_on_phantom_plugin_alignment(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    _patch_common(
        monkeypatch, doctor_cli, source_config,
        cov=_cov(phantom=["agent-bridge"]),
    )

    assert wm.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "phantom/renamed" in out
    assert "agent-bridge" in out

    payload_rc = wm.main(["doctor", "--json"])
    assert payload_rc == 0  # json mode has never gated on doctor findings
    payload = json.loads(capsys.readouterr().out)
    assert payload["plugin_alignment"]["ok"] is False
    assert payload["plugin_alignment"]["phantom"] == ["agent-bridge"]


def test_doctor_fails_on_published_prereq_gap(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    _patch_common(
        monkeypatch, doctor_cli, source_config,
        cov=_cov(gaps=[("some-plugin", "node")]),
    )

    assert wm.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "a plugin publishes a prereq the catalog does not carry" in out
    assert "some-plugin: node" in out

    payload_rc = wm.main(["doctor", "--json"])
    assert payload_rc == 0  # json mode still never gates on doctor findings
    payload = json.loads(capsys.readouterr().out)
    assert payload["plugin_alignment"]["ok"] is False
    assert payload["plugin_alignment"]["published_prereq_gaps"] == [["some-plugin", "node"]]


def test_doctor_remote_discovery_qualifies_success_as_membership_only(monkeypatch, capsys):
    """Remote discovery (no local checkout) cannot run the published-prereq
    check at all (model.coverage() needs ``find_repo_root()`` to find one) --
    a clean report in that mode must say so rather than imply full
    validation."""
    from worktree_manager import doctor_cli, source_config

    _patch_common(
        monkeypatch, doctor_cli, source_config,
        cov=_cov(source_kind="remote"),
    )

    assert wm.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "no drift" in out
    assert "published-prerequisite checks need a local checkout and were skipped" in out


def test_doctor_unreachable_marketplace_does_not_fail_alignment(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    _patch_common(
        monkeypatch, doctor_cli, source_config,
        cov=_cov(source_kind="none", phantom=["would-be-phantom-if-confirmed"]),
    )

    assert wm.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "no marketplace reachable" in out
