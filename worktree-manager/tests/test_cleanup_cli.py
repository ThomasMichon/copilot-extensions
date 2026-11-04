from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import cleanup_cli as cli


@pytest.fixture
def owner(tmp_path, monkeypatch):
    scope = {"component": "manager", "root": str(tmp_path / "root"),
             "bin_dir": str(tmp_path / "bin")}
    findings = [{
        "detector": cli.DETECTOR, "revision": 1,
        "resource": str(tmp_path / "bin" / name), "sha256": "a" * 64,
        "status": "candidate", "reason": "exact archived command",
    } for name in ("worktree-manager", "worktree-manager.cmd")]
    actions = []

    def apply(root, bin_dir, finding):
        actions.append(finding)
        return {"resource": finding["resource"], "status": "applied", "reason": "confirmed"}

    adapter = SimpleNamespace(inspect=lambda root, bin_dir: findings, apply=apply)
    monkeypatch.setattr(cli, "_scope", lambda root: scope)
    monkeypatch.setattr(cli, "_owner", lambda: adapter)
    return scope, findings, actions, adapter


def saved_report(tmp_path, scope):
    report = cli.inspect(scope)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report


def test_inspect_is_report_only_and_explicitly_partial(owner, capsys):
    assert cli.main(["inspect", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert len(report["findings"]) == 2
    assert not report["coverage_complete"]
    assert report["coverage"]["registrations"] == {"supported": False}
    assert not owner[2]


def test_apply_passes_only_explicitly_selected_observation(owner, tmp_path, capsys):
    scope, findings, actions, _ = owner
    path, report = saved_report(tmp_path, scope)
    assert cli.main(["apply", "--report", str(path), "--select",
                     report["findings"][1]["id"], "--json"]) == 0
    assert actions == [findings[1]]
    assert json.loads(capsys.readouterr().out)["settled"]


@pytest.mark.parametrize("change", ["scope", "digest", "detector", "revision", "status", "id-type", "resource", "sha-type", "status-type"])
def test_invalid_report_is_refused_before_owner_action(owner, tmp_path, capsys, change):
    scope, _, actions, _ = owner
    path, report = saved_report(tmp_path, scope)
    selected = report["findings"][0]["id"]
    if change == "scope":
        report["scope"] = {**scope, "root": str(tmp_path / "foreign")}
    elif change == "id-type":
        report["findings"][0]["id"] = []
    else:
        finding = report["findings"][0]
        key, value = {
            "digest": ("sha256", "b" * 64), "detector": ("detector", "unknown"),
            "revision": ("revision", True), "status": ("status", "preserved"),
            "resource": ("resource", []), "sha-type": ("sha256", None),
            "status-type": ("status", []),
        }[change]
        finding[key] = value
        if change != "digest":
            finding["id"] = cli._finding_id({k: v for k, v in finding.items() if k != "id"})
            selected = finding["id"]
    path.write_text(json.dumps(report), encoding="utf-8")
    assert cli.main(["apply", "--report", str(path), "--select", selected]) == 2
    assert "refused" in capsys.readouterr().err
    assert not actions


@pytest.mark.parametrize("selected", [[], ["unknown"], ["same", "same"]])
def test_apply_selection_is_required_and_strict(owner, tmp_path, selected):
    scope, _, actions, _ = owner
    path, _ = saved_report(tmp_path, scope)
    argv = ["apply", "--report", str(path)]
    for identity in selected:
        argv.extend(["--select", identity])
    if not selected:
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
    else:
        assert cli.main(argv) == 2
    assert not actions


@pytest.mark.parametrize("content", ['{"schema":1,"schema":2}', '{"schema":NaN}'])
def test_noncanonical_report_json_is_refused(owner, tmp_path, content, capsys):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    assert cli.main(["apply", "--report", str(path), "--select", "unknown"]) == 2
    assert "refused" in capsys.readouterr().err
    assert not owner[2]


@pytest.mark.parametrize("status", ["preserved", "failed", "unknown"])
def test_unsettled_owner_outcome_is_not_success(owner, tmp_path, capsys, status):
    scope, _, _, adapter = owner
    path, report = saved_report(tmp_path, scope)
    adapter.apply = lambda root, bin_dir, finding: {
        "resource": finding["resource"], "status": status, "reason": "contents changed",
    }
    assert cli.main(["apply", "--report", str(path), "--select",
                     report["findings"][0]["id"], "--json"]) == 1
    assert not json.loads(capsys.readouterr().out)["settled"]


def test_wrong_resource_owner_outcome_is_not_success(owner, tmp_path, capsys):
    scope, _, _, adapter = owner
    path, report = saved_report(tmp_path, scope)
    adapter.apply = lambda *args: {"status": "applied", "resource": "different"}
    assert cli.main(["apply", "--report", str(path), "--select",
                     report["findings"][0]["id"], "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["actions"][0]["status"] == "failed"
    assert not result["settled"]


def test_inventory_error_remains_visible(owner, capsys):
    def unavailable(*args):
        raise OSError("census unavailable")

    owner[3].inspect = unavailable
    assert cli.main(["inspect", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"][0]["reason"] == "census unavailable"
    assert not report["coverage"]["commands"]["complete"]


def test_real_cli_capability_is_read_only_and_runtime_selected():
    result = subprocess.run(
        [sys.executable, "-m", "worktree_manager", "cleanup", "capabilities", "--json"],
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    contract = json.loads(result.stdout)
    assert contract["schema"] == cli.CONTRACT_SCHEMA
    assert contract["version"] == 1
    assert contract["update_prerequisite"] is False
    assert contract["components"]["manager"]["daemons"]["supported"] is False


def test_cli_rejects_unknown_syntax_before_inventory(owner):
    with pytest.raises(SystemExit) as exc:
        cli.main(["inspect", "--force-all"])
    assert exc.value.code == 2
    assert not owner[2]


def test_family_dispatch_is_component_bound_and_batches_native_selection(owner, tmp_path, monkeypatch):
    scope, _, _, _ = owner
    scope["component"] = "agent-worktrees"
    findings = [{"detector": "worktrees-monitor-control-v1", "revision": 1,
                 "resource": f"status-monitor:{pid}:birth", "status": "candidate",
                 "sha256": "a" * 64, "reason": "native-observed"} for pid in (1, 2)]
    batches = []
    adapter = SimpleNamespace(
        inspect=lambda *_: findings,
        apply_many=lambda _root, _bins, selected: batches.append(selected) or [
            {"resource": item["resource"], "status": "pending", "reason": "code hold retained"}
            for item in selected
        ],
    )
    monkeypatch.setattr(cli, "_families", lambda _: {"daemons": adapter})
    path, report = saved_report(tmp_path, scope)
    selected = cli._read_report(path, scope, [item["id"] for item in report["findings"]])
    result = cli.apply_selected(scope, selected)
    assert batches == [findings]
    assert not result["settled"] and all(item["status"] == "pending" for item in result["actions"])


def test_foreign_component_detector_is_rejected_before_provider_request(owner, tmp_path):
    scope, _, actions, _ = owner
    path, report = saved_report(tmp_path, scope)
    finding = report["findings"][0]
    finding["detector"] = "worktrees-monitor-control-v1"
    finding["id"] = cli._finding_id({key: value for key, value in finding.items() if key != "id"})
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="does not belong"):
        cli._read_report(path, scope, [finding["id"]])
    assert not actions

def test_apply_retains_prior_outcomes_when_later_individual_owner_raises(monkeypatch):
    from types import SimpleNamespace
    from worktree_manager import cleanup_cli as cli

    def apply(root, bin_dir, finding):
        if finding["resource"] == "second":
            raise ValueError("owner observation changed")
        return {"resource": finding["resource"], "status": "applied", "reason": "confirmed"}
    monkeypatch.setattr(cli, "_families", lambda scope: {"commands": SimpleNamespace(apply=apply)})
    findings = [{"detector": cli.DETECTOR, "resource": name} for name in ("first", "second", "third")]
    result = cli.apply_selected({"component": "manager"}, findings)
    assert result["settled"] is False
    assert [(item["resource"], item["status"]) for item in result["actions"]] == [
        ("first", "applied"), ("second", "failed"), ("third", "applied"),
    ]
    assert result["actions"][1]["reason"] == "owner observation changed"
