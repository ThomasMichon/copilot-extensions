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


def test_doctor_json_includes_daemon_health(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    monkeypatch.setattr(doctor_cli, "detect_baseline", lambda: [_status(True)])
    monkeypatch.setattr(doctor_cli, "core_status", _core)
    monkeypatch.setattr(doctor_cli, "self_status", _self)
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


def test_doctor_apply_renders_daemon_actions(monkeypatch, capsys):
    from worktree_manager import doctor_cli, source_config

    monkeypatch.setattr(doctor_cli, "detect_baseline", lambda: [_status(True)])
    monkeypatch.setattr(doctor_cli, "core_status", _core)
    monkeypatch.setattr(doctor_cli, "self_status", _self)
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
