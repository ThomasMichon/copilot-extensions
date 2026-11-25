"""Tests for the `agent-worktrees claims` ledger command (resource-claims)."""

from __future__ import annotations

import argparse
import io
import json
import socket
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from machine_transport import detect_platform

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import claim_providers, claims_cli, claims_owner
from agent_worktrees import activity, peer_launch_adapter, tracking_claims, tracking_lifecycle
from agent_worktrees._peer_launch import ContextRefused
from agent_worktrees import config as cfg
from agent_worktrees import state_root
from agent_worktrees import tracking
from agent_worktrees import worktree_identity


# --- parser + registration --------------------------------------------------

def test_claims_parser_explicit_worktree_id() -> None:
    args = m.build_parser().parse_args(["claims"])
    assert args.command == "claims"
    assert args.target == []
    args2 = m.build_parser().parse_args(
        ["claims", "show", "--worktree-id", "wt-x", "--json"])
    assert args2.target == ["show"] and args2.claim_worktree_ids == ["wt-x"]
    assert args2.json is True


def test_claims_release_parser() -> None:
    args = m.build_parser().parse_args(
        ["claims", "release", "m/p/wt-B", "--remove"])
    assert args.target == ["release", "m/p/wt-B"]
    assert args.remove is True


def test_claims_mirror_status_parser() -> None:
    args = m.build_parser().parse_args(
        ["claims", "mirror-status", "task", "task-1", "--status", "released",
         "--holder", "agent-dispatch"])
    assert args.target == ["mirror-status", "task", "task-1"]
    assert args.status == "released"
    assert args.claim_holder == "agent-dispatch"


def test_claims_registered() -> None:
    assert m.COMMAND_MAP["claims"] is m.cmd_claims
    assert m._WORKTREE_VERBS.get("claims") == "claims"


def test_claims_owner_parser() -> None:
    args = m.build_parser().parse_args(
        ["claims", "owner", "codespace", "cs-one", "--json", "--all-states"])
    assert args.target == ["owner", "codespace", "cs-one"]
    assert args.json is True and args.all_states is True


# --- _dispatch_assigned_tasks degradation -----------------------------------

def test_inbound_unavailable_without_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claim_providers, "discover_claim_providers",
                        lambda *a, **k: ({}, ()))
    res = m._dispatch_assigned_tasks("anomalous-potato", "wt-a", "")
    assert res["available"] is False
    assert "not installed" in res["reason"]


def test_inbound_parses_dispatch_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agent_worktrees import claim_providers as cp
    provider = cp.ClaimProviderManifest(
        namespace="dispatch-task", plugin="agent-dispatch@marketplace",
        plugin_root="/x", status_command=("agent-dispatch",))
    monkeypatch.setattr(claim_providers, "discover_claim_providers",
                        lambda *a, **k: ({"dispatch-task": provider}, ()))

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "assigned": [{"id": "t1", "status": "queued", "title": "A"}],
            "owned": [{"id": "t2", "status": "started", "title": "B"}],
        })
        stderr = ""

    captured: dict[str, Any] = {}

    def _run(provider: claim_providers.ClaimProviderManifest, *, callback_args: tuple[str, ...], legacy_command: tuple[str, ...], timeout: float, cwd: str | None=None) -> _Proc:
        captured["provider"] = provider.plugin
        captured["callback_args"] = callback_args
        captured["legacy_command"] = legacy_command
        captured["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr(claim_providers, "_run_provider_process", _run)
    res = m._dispatch_assigned_tasks("anomalous-potato", "wt-a", str(tmp_path))
    assert res["available"] is True
    assert [t["id"] for t in res["assigned"]] == ["t1"]
    assert [t["id"] for t in res["owned"]] == ["t2"]
    # Regression: agent-dispatch emits JSON by default and rejects a --json flag,
    # so the command must NOT pass one (worktree-status has no --json).
    assert "--json" not in captured["legacy_command"]
    assert captured == {
        "provider": "agent-dispatch@marketplace",
        "callback_args": ("worktree-status", "--machine", "anomalous-potato", "--worktree", "wt-a"),
        "legacy_command": ("agent-dispatch", "worktree-status", "--machine", "anomalous-potato", "--worktree", "wt-a"),
        "cwd": str(tmp_path),
    }


def test_inbound_handles_dispatch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import claim_providers as cp
    provider = cp.ClaimProviderManifest(
        namespace="dispatch-task", plugin="agent-dispatch@marketplace",
        plugin_root="/x", status_command=("agent-dispatch",))
    monkeypatch.setattr(claim_providers, "discover_claim_providers",
                        lambda *a, **k: ({"dispatch-task": provider}, ()))

    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(
        claim_providers,
        "_run_provider_process",
        lambda *a, **k: _Proc(),
    )
    res = m._dispatch_assigned_tasks("anomalous-potato", "wt-a", "")
    assert res["available"] is False and res["reason"] == "boom"


def test_inbound_refuses_unsafe_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine/worktree_id containing a cmd.exe metacharacter must never
    reach the resolved argv (claim-provider-pattern effort review finding)."""
    from agent_worktrees import claim_providers as cp
    provider = cp.ClaimProviderManifest(
        namespace="dispatch-task", plugin="agent-dispatch@marketplace",
        plugin_root="/x", status_command=("agent-dispatch",))
    monkeypatch.setattr(claim_providers, "discover_claim_providers",
                        lambda *a, **k: ({"dispatch-task": provider}, ()))
    called = {"n": 0}
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: called.__setitem__("n", 1))
    res = m._dispatch_assigned_tasks("anomalous-potato&whoami", "wt-a", "")
    assert res["available"] is False
    assert called["n"] == 0


def test_inbound_still_invokes_with_a_namespaced_cell_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit-context calls must rebind through peer-launch and preserve cwd."""
    from agent_worktrees import claim_providers as cp
    provider = cp.ClaimProviderManifest(
        namespace="dispatch-task", plugin="agent-dispatch@marketplace",
        plugin_root="/x", status_command=("agent-dispatch",))
    monkeypatch.setattr(claim_providers, "discover_claim_providers",
                        lambda *a, **k: ({"dispatch-task": provider}, ()))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "agent-worktrees@copilot-extensions")
    monkeypatch.setattr(peer_launch_adapter, "explicit_context", lambda: True)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: pytest.fail("legacy fallback used"))
    captured: dict[str, Any] = {}

    def fake_run(peer: str, *args: Any, **kwargs: Any) -> _Proc:
        captured["peer"] = peer
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        return _Proc()

    class _Proc:
        returncode = 0
        stdout = json.dumps({"assigned": [], "owned": []})
        stderr = ""

    monkeypatch.setattr(peer_launch_adapter, "run", fake_run)
    res = m._dispatch_assigned_tasks("anomalous-potato", "wt-a", "D:\\repo")
    assert res["available"] is True
    assert captured == {
        "peer": "agent-dispatch",
        "args": ("worktree-status", "--machine", "anomalous-potato", "--worktree", "wt-a"),
        "cwd": None,
    }


def test_inbound_refusal_does_not_fallback_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import claim_providers as cp
    provider = cp.ClaimProviderManifest(
        namespace="dispatch-task", plugin="agent-dispatch@marketplace",
        plugin_root="/x", status_command=("agent-dispatch",))
    monkeypatch.setattr(claim_providers, "discover_claim_providers",
                        lambda *a, **k: ({"dispatch-task": provider}, ()))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.setattr(peer_launch_adapter, "explicit_context", lambda: True)
    monkeypatch.setattr(
        peer_launch_adapter,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(ContextRefused("refused")),
    )
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: pytest.fail("legacy fallback used"))
    res = m._dispatch_assigned_tasks("anomalous-potato", "wt-a", "")
    assert res == {"available": False, "reason": "agent-dispatch call failed"}


# --- cmd_claims end-to-end --------------------------------------------------

def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, owner_ref: str | None=None, resources: list[tracking_claims.ResourceClaim] | None=None) -> Path:
    tdir = tmp_path / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    wdir = tmp_path / "wt-A"
    wdir.mkdir(exist_ok=True)
    monkeypatch.setattr(socket, "gethostname", lambda: "anomalous-potato")
    rec = tracking_lifecycle.create_new_record(
        "wt-A", "worktree/wt-A", str(wdir), "test-chamber",
        "anomalous-potato", detect_platform(), tdir, owner_ref=owner_ref,
    )
    if resources:
        for c in resources:
            tracking_claims.add_resource_claim(rec, c, save=False)
        tracking.save_record(rec, tdir / "wt-A.yaml")
    # config + inference stubs
    import types
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine="anomalous-potato"))
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path if name in (None, "test-chamber") else tmp_path / str(name))
    (wdir / ".agent-worktrees").mkdir()
    (wdir / ".agent-worktrees" / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    owner_config = cfg.Config(
        srcroot=str(tmp_path), machine="anomalous-potato", platform=detect_platform(),
        repo_name="test-chamber",
        repos={"test-chamber": cfg.RepoConfig(anchor=str(wdir), worktree_root=str(tmp_path))},
    )
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda name, **kwargs: owner_config,
    )
    monkeypatch.setattr(
        "agent_worktrees.config_authority.load_project_authority_config",
        lambda name, **kwargs: owner_config,
    )
    ready_root = state_root.StateRoot(
        str(tmp_path), "launch_repo", "test-chamber", False, False, True)
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            True, "ready", ready_root),
    )
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, cfg_: wid or "wt-A")
    monkeypatch.setattr(m, "_dispatch_assigned_tasks",
                        lambda machine, wid, cwd: {"available": False,
                                                   "reason": "stubbed"})
    return tdir


def test_claims_json_outbound_and_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    claim = tracking_claims.ResourceClaim(
        kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B",
        created_at="2026-07-31T00:00:00")
    _seed(tmp_path, monkeypatch,
          owner_ref="anomalous-potato/test-chamber/wt-owner#s1",
          resources=[claim])
    rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=True))
    assert rc == 0
    captured = capfd.readouterr()
    out = json.loads(captured.out)
    assert out["worktree_id"] == "wt-A"
    assert out["owner_ref"] == "anomalous-potato/test-chamber/wt-owner#s1"
    assert len(out["outbound"]) == 1
    assert out["outbound"][0]["ref"] == "anomalous-potato/copilot-extensions/wt-B"
    assert out["inbound"]["available"] is False
    assert "Deprecated positional worktree selector" in captured.err


@pytest.mark.parametrize("selection", [[], ["--worktree-id", "wt-A"]])
def test_claims_show_reads_the_full_ledger_without_a_show_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capfd: pytest.CaptureFixture[str], selection: list[str],
) -> None:
    claim = tracking_claims.ResourceClaim(kind="task", ref="task-example", state="at-rest")
    _seed(tmp_path, monkeypatch, owner_ref="host/project/creator", resources=[claim])
    args = m.build_parser().parse_args(["claims", "show", *selection, "--json"])

    assert m.cmd_claims(args) == 0

    captured = capfd.readouterr()
    payload = json.loads(captured.out)
    assert payload["worktree_id"] == "wt-A"
    assert payload["owner_ref"] == "host/project/creator"
    assert payload["outbound"][0]["ref"] == "task-example"
    assert payload["inbound"]["available"] is False
    assert "Deprecated" not in captured.err


@pytest.mark.parametrize("argv", [
    ["claims", "show", "wt-A", "--json"],
    ["claims", "show", "--worktree-id", "wt-A", "--worktree-id", "wt-B", "--json"],
    ["claims", "show", "--worktree-id", "../outside", "--json"],
    ["claims", "show", "--owner-ref", "host/project/wt-A", "--json"],
    ["claims", "wt-A", "--expected-record-nonce", "record-pin", "--json"],
    ["claims", "sweep", "--worktree-id", "wt-A", "--apply", "--json"],
    ["claims", "add", "task", "task-example", "--worktree-id", "wt-A",
     "--owner-ref", "host/project/wt-B", "--json"],
    ["claims", "add", "task", "task-example", "--worktree", "wt-A",
     "--owner-ref", "host/project/wt-B", "--json"],
])
def test_claims_rejects_ambiguous_or_unenforceable_selectors_before_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capfd: pytest.CaptureFixture[str], argv: list[str],
) -> None:
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        claims_cli, "_dispatch_claim",
        lambda *a, **k: pytest.fail("invalid selector reached a claim mutation"),
    )
    monkeypatch.setattr(
        claims_cli, "_claims_sweep",
        lambda *a, **k: pytest.fail("ignored selector widened a sweep"),
    )

    assert m.cmd_claims(m.build_parser().parse_args(argv)) == 2
    assert "error" in json.loads(capfd.readouterr().out)


def test_claims_typo_is_a_command_error_not_a_missing_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    _seed(tmp_path, monkeypatch)
    assert m.cmd_claims(m.build_parser().parse_args(["claims", "shwo", "--json"])) == 2
    error = json.loads(capfd.readouterr().out)["error"]
    assert "unknown claims command" in error
    assert "claims show --worktree-id ID" in error
    assert "worktree not found" not in error


def test_claims_explicit_owner_selection_preserves_the_release_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    tdir = _seed(tmp_path, monkeypatch)
    tracking_lifecycle.create_new_record(
        "wt-B", "worktree/wt-B", str(tmp_path / "wt-B"), "test-chamber",
        "anomalous-potato", detect_platform(), tdir,
    )
    mutations: list[dict[str, Any]] = []

    def dispatch(operation: str, payload: dict[str, Any]) -> dict[str, str]:
        mutations.append({"operation": operation, **payload})
        return {"action": "released"}

    monkeypatch.setattr(claims_cli, "_dispatch_claim", dispatch)
    args = m.build_parser().parse_args(
        ["claims", "release", "show", "--worktree-id", "wt-B", "--json"])
    assert m.cmd_claims(args) == 0
    assert [(row["operation"], row["worktree_id"], row["ref"]) for row in mutations] == [
        ("claim_release", "wt-B", "show"),
    ]
    assert json.loads(capfd.readouterr().out)["worktree_id"] == "wt-B"


def test_claims_transitive_forwards_the_named_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str | None] = []

    def show_transitive(args: argparse.Namespace, wid: str | None) -> int:
        selected.append(wid)
        return 0

    monkeypatch.setattr(claims_cli, "_claims_transitive", show_transitive)
    args = m.build_parser().parse_args(
        ["claims", "transitive", "--worktree-id", "wt-selected", "--json"])
    assert m.cmd_claims(args) == 0
    assert selected == ["wt-selected"]


def test_claims_show_includes_abandoned_resource(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    # Validation Plan "Claim-free": an abandoned claim (reclaimed by the
    # never-wedge sweep) must remain visible in the ledger's audit detail --
    # it is excluded from held-claims counting, not hidden from inspection.
    claim = tracking_claims.ResourceClaim(
        kind="codespace", ref="anomalous-potato/copilot-extensions/cs-orphan",
        created_at="2026-07-31T00:00:00", state="abandoned")
    _seed(tmp_path, monkeypatch, resources=[claim])
    rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert len(out["outbound"]) == 1
    assert out["outbound"][0]["state"] == "abandoned"
    assert out["outbound"][0]["ref"] == (
        "anomalous-potato/copilot-extensions/cs-orphan"
    )


def test_claims_empty_ledger_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(target=[], json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["outbound"] == [] and out["owner_ref"] is None
    assert out["coordination_readiness"]["ready"] is True


def test_claims_inspection_reports_unready_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed(tmp_path, monkeypatch)
    path = tmp_path / "worktrees" / "wt-A.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: _blocked_readiness(),
    )

    assert m.cmd_claims(argparse.Namespace(target=[], json=True)) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["coordination_readiness"]["code"] == (
        "knowledge_binding_required"
    )
    assert path.read_bytes() == before


def test_claims_missing_worktree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tdir = tmp_path / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    import types
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine="m"))
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, cfg_: "ghost")
    rc = m.cmd_claims(m.build_parser().parse_args(
        ["claims", "show", "--worktree-id", "ghost", "--json"]))
    assert rc == 1


def test_claims_human_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    claim = tracking_claims.ResourceClaim(
        kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B")
    _seed(tmp_path, monkeypatch, resources=[claim])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=False))
    assert rc == 0
    text = buf.getvalue()
    assert "Claim ledger for wt-A" in text
    assert "anomalous-potato/copilot-extensions/wt-B" in text
    assert "Inbound" in text


# --- claims owner -----------------------------------------------------------

def _seed_project_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project: str, worktree_id: str, resources: list[tracking_claims.ResourceClaim]) -> tracking.WorktreeRecord:
    root = tmp_path / project
    tdir = root / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    wdir = root / worktree_id
    wdir.mkdir(exist_ok=True)
    monkeypatch.setattr(socket, "gethostname", lambda: "example-machine")
    rec = tracking_lifecycle.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(wdir), project,
        "example-machine", detect_platform(), tdir,
    )
    for claim in resources:
        tracking_claims.add_resource_claim(rec, claim, save=False)
    tracking.save_record(rec, tdir / f"{worktree_id}.yaml")
    monkeypatch.setattr(
        "agent_worktrees.installer.read_projects_registry",
        lambda: {"projects": {"proj-a": {}, "proj-b": {}}},
    )
    monkeypatch.setattr(
        "agent_worktrees.config.project_dir",
        lambda name=None: tmp_path / (name or project),
    )
    return rec


def test_find_claim_owners_across_registered_projects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_project_record(
        tmp_path, monkeypatch, "proj-a", "wt-a",
        [tracking_claims.ResourceClaim(kind="codespace", ref="cs-one", state="active")],
    )
    _seed_project_record(
        tmp_path, monkeypatch, "proj-b", "wt-b",
        [
            tracking_claims.ResourceClaim(kind="codespace", ref="cs-one", state="at-rest",
                                   note="kept warm"),
            tracking_claims.ResourceClaim(kind="codespace", ref="cs-two", state="released"),
        ],
    )

    owners = claims_owner.find_claim_owners("codespace", "cs-one")
    assert [o["project"] for o in owners] == ["proj-a", "proj-b"]
    assert [o["worktree_id"] for o in owners] == ["wt-a", "wt-b"]
    assert owners[0]["qualified_ref"] == "example-machine/proj-a/wt-a"
    assert owners[0]["owner_ref"] == "example-machine/proj-a/wt-a"
    assert owners[1]["note"] == "kept warm"
    assert claims_owner.find_claim_owners("codespace", "cs-two") == []
    assert claims_owner.find_claim_owners(
        "codespace", "cs-two", include_released=True)[0]["state"] == "released"


def test_claims_owner_json_exit_codes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_project_record(
        tmp_path, monkeypatch, "proj-a", "wt-a",
        [tracking_claims.ResourceClaim(kind="codespace", ref="cs-one")],
    )
    _seed_project_record(tmp_path, monkeypatch, "proj-b", "wt-b", [])

    rc = m.cmd_claims(argparse.Namespace(
        target=["owner", "codespace", "cs-one"], json=True, all_states=False))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["owners"][0]["worktree_id"] == "wt-a"

    rc = m.cmd_claims(argparse.Namespace(
        target=["owner", "codespace", "missing"], json=True, all_states=False))
    assert rc == 1
    out = json.loads(capfd.readouterr().out)
    assert out["owners"] == []


def test_find_claim_owners_skips_bad_records(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadClaim:
        @property
        def kind(self) -> None:
            raise ValueError("bad claim")

    class BadRecord:
        resources = [BadClaim()]

    monkeypatch.setattr(
        "agent_worktrees.claims_owner._iter_records",
        lambda: [("proj-a", BadRecord())],
    )
    assert claims_owner.find_claim_owners("codespace", "cs-one") == []


# --- same_worktree_family ----------------------------------------------------

def test_same_worktree_family_identical_id_is_family() -> None:
    assert claims_owner.same_worktree_family("wt-a", "wt-a") is True


def test_same_worktree_family_direct_parent_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # wt-child's owner_ref names wt-parent -- a worktree wt-parent created.
    _seed_project_record(tmp_path, monkeypatch, "proj-a", "wt-parent", [])
    child = _seed_project_record(tmp_path, monkeypatch, "proj-a", "wt-child", [])
    child.owner_ref = "example-machine/proj-a/wt-parent#session"
    tracking.save_record(child, tmp_path / "proj-a" / "worktrees" / "wt-child.yaml")

    assert claims_owner.same_worktree_family("wt-parent", "wt-child") is True
    assert claims_owner.same_worktree_family("wt-child", "wt-parent") is True


def test_same_worktree_family_transitive_grandchild(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_project_record(tmp_path, monkeypatch, "proj-a", "wt-grandparent", [])
    parent = _seed_project_record(tmp_path, monkeypatch, "proj-a", "wt-parent", [])
    parent.owner_ref = "example-machine/proj-a/wt-grandparent#session"
    tracking.save_record(parent, tmp_path / "proj-a" / "worktrees" / "wt-parent.yaml")
    child = _seed_project_record(tmp_path, monkeypatch, "proj-a", "wt-child", [])
    child.owner_ref = "example-machine/proj-a/wt-parent#session"
    tracking.save_record(child, tmp_path / "proj-a" / "worktrees" / "wt-child.yaml")

    assert claims_owner.same_worktree_family("wt-grandparent", "wt-child") is True


def test_same_worktree_family_unrelated_worktrees_are_not_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _seed_project_record(tmp_path, monkeypatch, "proj-a", "wt-a", [])
    _seed_project_record(tmp_path, monkeypatch, "proj-b", "wt-b", [])
    assert claims_owner.same_worktree_family("wt-a", "wt-b") is False


def test_same_worktree_family_degrades_safe_on_bad_records(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadRecord:
        worktree_id = "wt-a"
        owner_ref = None

    monkeypatch.setattr(
        "agent_worktrees.claims_owner._iter_records",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert claims_owner.same_worktree_family("wt-a", "wt-b") is False


# --- claims release ---------------------------------------------------------

def _release_args(ref: str, *, remove: bool=False, json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["release", ref], remove=remove, release_worktree=None,
        json=json_)


def test_claims_release_marks_released(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    ref = "anomalous-potato/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="worktree", ref=ref)])
    rc = m.cmd_claims(_release_args(ref))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["action"] == "released" and out["ref"] == ref
    # Reload: the claim persists but is no longer live.
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert len(rec.resources) == 1
    assert rec.resources[0].state == "released"
    assert rec.live_resources == []


def test_claims_release_remove_drops_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    ref = "anomalous-potato/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="worktree", ref=ref)])
    rc = m.cmd_claims(_release_args(ref, remove=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["action"] == "removed"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources == []


def test_claims_release_unknown_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(
              kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B")])
    rc = m.cmd_claims(_release_args("anomalous-potato/copilot-extensions/wt-Z"))
    assert rc == 1


def test_claims_release_missing_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(
        target=["release"], remove=False, release_worktree=None, json=True))
    assert rc == 2


def _annotate_args(ref: str, *, note: str="", json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["annotate", ref], note=note, release_worktree=None, json=json_)


def test_claims_annotate_updates_existing_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """Update an existing claim's note without release + re-add
    (copilot-extensions#2631)."""
    ref = "anomalous-potato/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="worktree", ref=ref, note="")])
    rc = m.cmd_claims(_annotate_args(ref, note="linked to PR #3705"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["ref"] == ref and out["note"] == "linked to PR #3705"
    assert out["previous_note"] == ""
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources[0].note == "linked to PR #3705"


def test_claims_annotate_overwrites_prior_note_and_logs_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    ref = "anomalous-potato/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="worktree", ref=ref, note="auto-claimed")])
    rc = m.cmd_claims(_annotate_args(ref, note="now linked to PR #3705"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["previous_note"] == "auto-claimed"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources[0].note == "now linked to PR #3705"


def test_claims_annotate_requires_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ref = "anomalous-potato/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="worktree", ref=ref)])
    rc = m.cmd_claims(_annotate_args(ref, note=""))
    assert rc == 2


def test_claims_annotate_unknown_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(
              kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B")])
    rc = m.cmd_claims(_annotate_args("anomalous-potato/copilot-extensions/wt-Z", note="x"))
    assert rc == 1


def test_claims_annotate_missing_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(
        target=["annotate"], note="x", release_worktree=None, json=True))
    assert rc == 2


# --- claims add -------------------------------------------------------------

def _add_args(kind: str, ref: str, *, note: str="", json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["add", kind, ref], note=note, release_worktree=None, json=json_)


def _blocked_readiness() -> state_root.CoordinationReadiness:
    root = state_root.StateRoot(
        None,
        "knowledge_repo",
        "",
        True,
        True,
        False,
        error="no knowledge_repo is bound",
    )
    return state_root.CoordinationReadiness(
        False,
        "knowledge_binding_required",
        root,
        error=(
            "Set `knowledge_repo: <name>` in the machine-local project config "
            "and retry the same operation."
        ),
    )


@pytest.mark.parametrize(
    "kind",
    ["worktree", "codespace", "container", "ssh", "workdir", "pr", "task"],
)
def test_claims_add_rejects_unready_coordination_without_mutation(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed(tmp_path, monkeypatch)
    path = tmp_path / "worktrees" / "wt-A.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: _blocked_readiness(),
    )

    assert m.cmd_claims(_add_args(kind, f"{kind}-resource")) == 3
    payload = json.loads(capfd.readouterr().out)
    assert payload["code"] == "knowledge_binding_required"
    assert payload["coordination_readiness"]["version"] == 1
    assert path.read_bytes() == before


def test_claims_add_journals_active_claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_args("codespace", "cs-blue", note="example-web"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["kind"] == "codespace" and out["ref"] == "cs-blue"
    assert out["state"] == "active"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert len(rec.resources) == 1
    c = rec.resources[0]
    assert c.kind == "codespace" and c.ref == "cs-blue" and c.note == "example-web"
    assert c.is_unsettled and c.created_at  # active + timestamped


def test_claims_add_rejects_unknown_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_args("gizmo", "x"))
    assert rc == 2


def test_claims_add_dedups_by_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    m.cmd_claims(_add_args("codespace", "cs-blue"))
    capfd.readouterr()
    m.cmd_claims(_add_args("codespace", "cs-blue", note="second"))
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert len(rec.resources) == 1  # refreshed, not duplicated


# --- claims mirror-status (#2584 follow-up) ---------------------------------

def _mirror_args(kind: str, ref: str, *, status: str | None="active", holder: str | None=None, json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["mirror-status", kind, ref], status=status,
        claim_holder=holder, json=json_)


def test_claims_mirror_status_requires_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rc = m.cmd_claims(_mirror_args("task", "task-1", status=None))
    assert rc == 2


def test_claims_mirror_status_rejects_non_task_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rc = m.cmd_claims(_mirror_args("codespace", "cs-1"))
    assert rc == 2


def test_claims_mirror_status_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import task_claim_registry
    seen: dict[str, Any] = {}

    def _fake_set(ref: str, status: str, *, holder: str="agent-dispatch", config: cfg.Config | None=None, settings: dict[str, Any] | None=None) -> bool:
        seen.update(ref=ref, status=status, holder=holder)
        return True

    monkeypatch.setattr(task_claim_registry, "set_task_claim_status", _fake_set)
    rc = m.cmd_claims(_mirror_args("task", "task-1", status="released", holder="w"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["kind"] == "task" and out["ref"] == "task-1"
    assert out["status"] == "released" and out["mirrored"] is True
    assert seen == {"ref": "task-1", "status": "released", "holder": "w"}


def test_claims_mirror_status_failure_is_nonzero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import task_claim_registry
    monkeypatch.setattr(task_claim_registry, "set_task_claim_status",
                        lambda ref, status, **kw: False)
    rc = m.cmd_claims(_mirror_args("task", "task-1"))
    assert rc == 1
    out = json.loads(capfd.readouterr().out)
    assert out["mirrored"] is False


def test_claims_add_rejects_finalizing_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    path = tmp_path / "worktrees" / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.status = "finalizing"
    tracking.save_record(rec, path)
    rc = m.cmd_claims(_add_args("codespace", "cs-late"))
    assert rc == 1
    out = json.loads(capfd.readouterr().out)
    assert "ownership is frozen" in out["error"]
    assert tracking.load_record(path).resources == []


def test_claims_add_allows_finalized_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """``finalized`` is not terminal (docs/worktree-lifecycle.md): a resumed,
    already-finalized worktree may still accept a new outbound claim, which
    reopens it to `active` (worktree-finality-and-obligations Phase 2)."""
    _seed(tmp_path, monkeypatch)
    path = tmp_path / "worktrees" / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.status = "finalized"
    tracking.save_record(rec, path)
    rc = m.cmd_claims(_add_args("codespace", "cs-resumed"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["kind"] == "codespace" and out["ref"] == "cs-resumed"
    assert out["reopened"] is True
    reloaded = tracking.load_record(path)
    assert [c.ref for c in reloaded.resources] == ["cs-resumed"]
    assert reloaded.status == "active"


def test_claims_add_reopen_surfaces_earlier_finalize_release_trail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """worktree-finality-and-obligations Phase 2: reopening a finalized
    worktree via `claims add` reports exactly what the earlier finalize's
    `release_all_resources` cascade let go, since reopening does not restore
    those resources."""
    _seed(tmp_path, monkeypatch)
    path = tmp_path / "worktrees" / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.status = "finalized"
    rec.last_finalize_released = [
        tracking_claims.ResourceClaim(
            kind="codespace", ref="cs-old", state="released", note="idle box"),
    ]
    tracking.save_record(rec, path)
    rc = m.cmd_claims(_add_args("codespace", "cs-resumed"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["reopened"] is True
    assert out["released_by_earlier_finalize"] == [
        {"kind": "codespace", "ref": "cs-old", "note": "idle box"},
    ]


def test_claims_add_reopen_empty_trail_when_nothing_was_released(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, monkeypatch)
    path = tmp_path / "worktrees" / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.status = "finalized"
    tracking.save_record(rec, path)
    rc = m.cmd_claims(_add_args("codespace", "cs-resumed"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["reopened"] is True
    assert out["released_by_earlier_finalize"] == []


def test_claims_add_missing_operands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(
        target=["add", "codespace"], note="", release_worktree=None, json=True))
    assert rc == 2


def test_claims_add_ambiguous_write_outcome_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """agent-worktrees-authoritative-daemon Phase 3: a write whose daemon
    request was sent and then failed is genuinely ambiguous -- the command
    must surface `AmbiguousWriteOutcome` as a reported failure, never
    silently retry or swallow it."""
    from agent_worktrees import tracking_write

    tdir = _seed(tmp_path, monkeypatch)

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise tracking_write.AmbiguousWriteOutcome("request sent, no response")

    monkeypatch.setattr(tracking_write, "dispatch", _raise)
    rc = m.cmd_claims(_add_args("codespace", "cs-x"))
    assert rc == 1
    out = json.loads(capfd.readouterr().out)
    assert "unknown state" in out["error"]
    # Refused before any mutation -- the record must be untouched.
    assert tracking.load_record(tdir / "wt-A.yaml").resources == []


# --- claims add --owner-ref (cross-project resolution, 3b-wiring/2) ----------

def _add_ownerref_args(kind: str, ref: str, owner_ref: str | None, *, note: str="", json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["add", kind, ref], note=note, release_worktree=None,
        claim_owner_ref=owner_ref, json=json_)


def _seed_ownerref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, machine: str="anomalous-potato") -> Path:
    """Seed a borrowing-worktree record in ITS OWN project dir, and point the
    'current' cwd at a DIFFERENT project (the daemon-cwd gotcha)."""
    import types
    # The borrowing worktree lives in project 'example-web'.
    owner_proj_dir = tmp_path / ".example-web"
    owner_wt_dir = owner_proj_dir / "worktrees"
    owner_wt_dir.mkdir(parents=True, exist_ok=True)
    wdir = tmp_path / "wt-borrower"
    wdir.mkdir(exist_ok=True)
    monkeypatch.setattr(socket, "gethostname", lambda: machine)
    tracking_lifecycle.create_new_record(
        "wt-borrower", "worktree/wt-borrower", str(wdir), "example-web",
        machine, detect_platform(), owner_wt_dir,
    )
    # The 'current' project (daemon cwd) is a different one entirely.
    cur_tdir = tmp_path / ".dotfiles" / "worktrees"
    cur_tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: cur_tdir)
    monkeypatch.setattr("agent_worktrees.config.project_dir",
                        lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine=machine))
    (wdir / ".agent-worktrees").mkdir()
    (wdir / ".agent-worktrees" / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    owner_config = cfg.Config(
        srcroot=str(tmp_path), machine=machine, platform=detect_platform(), repo_name="example-web",
        repos={"example-web": cfg.RepoConfig(anchor=str(wdir), worktree_root=str(tmp_path))},
    )
    monkeypatch.setattr(
        "agent_worktrees.config.load_project_config",
        lambda name, **kwargs: owner_config,
    )
    monkeypatch.setattr(
        "agent_worktrees.config_authority.load_project_authority_config",
        lambda name, **kwargs: owner_config,
    )
    ready_root = state_root.StateRoot(
        str(tmp_path), "launch_repo", "example-web", False, False, True)
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            True, "ready", ready_root),
    )
    monkeypatch.setattr(m, "_dispatch_assigned_tasks",
                        lambda machine, wid, cwd: {"available": False,
                                                   "reason": "stubbed"})
    return owner_wt_dir


def test_claims_add_owner_ref_lands_on_cross_project_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    owner_wt_dir = _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-xyz", "anomalous-potato/example-web/wt-borrower", note="borrow"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["worktree_id"] == "wt-borrower" and out["ref"] == "cs-xyz"
    # Landed on the BORROWING project's record, not the current (dotfiles) one.
    rec = tracking.load_record(owner_wt_dir / "wt-borrower.yaml")
    assert [c.ref for c in rec.resources] == ["cs-xyz"]
    assert rec.resources[0].is_unsettled  # active
    # The current-project tracking dir got NOTHING.
    assert not list((tmp_path / ".dotfiles" / "worktrees").glob("*.yaml"))


def test_claims_add_owner_ref_cross_machine_defers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-remote", "other-box/example-web/wt-borrower"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out.get("deferred") is True and out["reason"] == "cross-machine-owner"
    # No local ledger write anywhere.
    assert not list((tmp_path / ".example-web" / "worktrees").glob("*.yaml")) or \
        all(not tracking.load_record(p).resources
            for p in (tmp_path / ".example-web" / "worktrees").glob("*.yaml"))


def test_claims_add_owner_ref_cannot_bypass_coordination_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    owner_wt_dir = _seed_ownerref(tmp_path, monkeypatch)
    path = owner_wt_dir / "wt-borrower.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: _blocked_readiness(),
    )

    rc = m.cmd_claims(_add_ownerref_args(
        "codespace",
        "cs-remote",
        "other-box/example-web/wt-borrower",
    ))
    assert rc == 3
    assert json.loads(capfd.readouterr().out)["code"] == (
        "knowledge_binding_required"
    )
    assert path.read_bytes() == before


def test_claims_add_owner_ref_uses_owner_project_readiness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    owner_wt_dir = _seed_ownerref(tmp_path, monkeypatch)
    path = owner_wt_dir / "wt-borrower.yaml"
    before = path.read_bytes()
    ambient_config = cfg.load_project_config("provider")
    owner_config = cfg.load_project_config("example-web")
    monkeypatch.setattr(cfg, "load_config", lambda: ambient_config)
    monkeypatch.setattr(
        cfg, "load_project_config", lambda name, **kwargs: owner_config
    )
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: (
            _blocked_readiness()
            if config.repo_name == owner_config.repo_name
            else state_root.CoordinationReadiness(
                True,
                "ready",
                state_root.StateRoot(
                    str(tmp_path),
                    "launch_repo",
                    "provider",
                    False,
                    False,
                    True,
                ),
            )
        ),
    )

    rc = m.cmd_claims(_add_ownerref_args(
        "codespace",
        "cs-owner-project",
        "anomalous-potato/example-web/wt-borrower",
    ))
    assert rc == 3
    assert json.loads(capfd.readouterr().out)["code"] == (
        "knowledge_binding_required"
    )
    assert path.read_bytes() == before


def test_claims_add_succeeds_after_binding_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed(tmp_path, monkeypatch)
    blocked = {"value": True}

    def readiness(config: cfg.Config) -> state_root.CoordinationReadiness:
        if blocked["value"]:
            return _blocked_readiness()
        root = state_root.StateRoot(
            str(tmp_path),
            "knowledge_repo",
            "knowledge",
            True,
            True,
            True,
        )
        return state_root.CoordinationReadiness(True, "ready", root)

    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        readiness,
    )
    args = _add_args("codespace", "existing-resource")
    assert m.cmd_claims(args) == 3
    capfd.readouterr()
    blocked["value"] = False
    assert m.cmd_claims(args) == 0
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert [claim.ref for claim in rec.resources] == ["existing-resource"]


def test_claims_add_owner_ref_rejects_unqualified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args("codespace", "cs-x", "just-an-id"))
    assert rc == 2


def test_claims_add_owner_ref_missing_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-x", "anomalous-potato/example-web/no-such-wt"))
    assert rc == 1  # resolved to a same-machine path that doesn't exist


# --- claims settle ----------------------------------------------------------

def _settle_args(ref: str, *, released: bool=False, json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["settle", ref], released=released, release_worktree=None, json=json_)


def test_claims_settle_marks_at_rest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="codespace", ref="cs-blue")])
    rc = m.cmd_claims(_settle_args("cs-blue"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["disposition"] == "at-rest"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    c = rec.resources[0]
    assert c.state == "at-rest" and c.is_at_rest and not c.is_unsettled
    assert c.is_live  # at-rest is still held


def test_claims_settle_released(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="codespace", ref="cs-blue")])
    rc = m.cmd_claims(_settle_args("cs-blue", released=True))
    assert rc == 0
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources[0].state == "released"


def test_claims_settle_unknown_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="codespace", ref="cs-blue")])
    rc = m.cmd_claims(_settle_args("cs-red"))
    assert rc == 1


def _settle_ownerref_args(ref: str, owner_ref: str | None, *, released: bool=False, json_: bool=True) -> argparse.Namespace:
    return argparse.Namespace(
        target=["settle", ref], released=released, release_worktree=None,
        claim_owner_ref=owner_ref, json=json_)


def test_claims_settle_owner_ref_lands_on_cross_project_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    owner_wt_dir = _seed_ownerref(tmp_path, monkeypatch)
    # First journal an active claim onto the borrowing record via owner-ref.
    m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-xyz", "anomalous-potato/example-web/wt-borrower"))
    capfd.readouterr()
    # Now settle it via owner-ref (disconnect-hook path).
    rc = m.cmd_claims(_settle_ownerref_args(
        "cs-xyz", "anomalous-potato/example-web/wt-borrower"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["disposition"] == "at-rest" and out["worktree_id"] == "wt-borrower"
    rec = tracking.load_record(owner_wt_dir / "wt-borrower.yaml")
    assert rec.resources[0].state == "at-rest" and not rec.resources[0].is_unsettled


def test_claims_settle_owner_ref_cross_machine_defers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_settle_ownerref_args(
        "cs-remote", "other-box/example-web/wt-borrower"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out.get("deferred") is True and out["reason"] == "cross-machine-owner"


def test_claims_settle_owner_ref_rejects_unqualified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_settle_ownerref_args("cs-x", "just-an-id"))
    assert rc == 2


def test_claims_add_then_settle_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    m.cmd_claims(_add_args("codespace", "cs-blue"))
    capfd.readouterr()
    m.cmd_claims(_settle_args("cs-blue"))
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources[0].state == "at-rest"


# --- activity.log_event instrumentation (#3113) -----------------------------

def test_claims_add_logs_claim_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    logged = []
    monkeypatch.setattr(activity, "log_event", lambda *a, **k: logged.append((a, k)))
    rc = m.cmd_claims(_add_args("codespace", "cs-blue", note="example-web"))
    assert rc == 0
    assert logged == [(("claim_added",), {
        "worktree_id": "wt-A", "kind": "codespace", "ref": "cs-blue",
        "state": "active", "reopened": False})]


def test_claims_release_logs_claim_released(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    ref = "anomalous-potato/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking_claims.ResourceClaim(kind="worktree", ref=ref)])
    logged = []
    monkeypatch.setattr(activity, "log_event", lambda *a, **k: logged.append((a, k)))
    rc = m.cmd_claims(_release_args(ref))
    assert rc == 0
    assert logged == [(("claim_released",), {
        "worktree_id": "wt-A", "kind": "worktree", "ref": ref,
        "action": "released"})]


def test_claims_settle_logs_claim_settled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path, monkeypatch)
    m.cmd_claims(_add_args("codespace", "cs-blue"))
    capfd.readouterr()
    logged = []
    monkeypatch.setattr(activity, "log_event", lambda *a, **k: logged.append((a, k)))
    rc = m.cmd_claims(_settle_args("cs-blue"))
    assert rc == 0
    assert logged == [(("claim_settled",), {
        "worktree_id": "wt-A", "kind": "codespace", "ref": "cs-blue",
        "disposition": "at-rest"})]
