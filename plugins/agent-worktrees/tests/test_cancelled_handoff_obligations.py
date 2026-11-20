"""Cancelled handoff task settlement against actual private claim ledgers."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg
from agent_worktrees import tracking_claims, tracking_lifecycle
from machine_transport import detect_platform
from agent_worktrees import output as cli_output, tracking, worktree_identity
from agent_worktrees.finalize import _assert_obligations_settled


@pytest.fixture
def owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: None,
) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "plugins" / "agent-dispatch" / "src"))
    from agent_dispatch import handoff_claim_release, procutil
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.queue_common import Task
    from agent_dispatch.queue_records import Status

    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create(
        "Continue the fixture", repo="https://github.com/example/project",
        status=Status.PROPOSED, labels=["handoff"], source="context-handoff",
        target_worktree="shared-id", target_machine=socket.gethostname(),
    )
    assert isinstance(task, Task)
    roots: dict[str, Path] = {}
    configs: dict[str, cfg.Config] = {}
    for name in ("creator", "coordinator"):
        root = tmp_path / name
        records = root / "worktrees"
        records.mkdir(parents=True)
        checkout = root / "shared-id"
        checkout.mkdir()
        (checkout / ".agent-worktrees").mkdir()
        (checkout / ".agent-worktrees" / "machines.yaml").write_text(
            "machines: {}\n", encoding="utf-8",
        )
        configs[name] = cfg.Config(
            srcroot=str(root), repo_name=name, machine=socket.gethostname(),
            platform=detect_platform(),
            repos={name: cfg.RepoConfig(
                anchor=str(checkout), worktree_root=str(root),
            )},
        )
        record = tracking_lifecycle.create_new_record(
            "shared-id", "worktree/shared-id", str(checkout), name,
            socket.gethostname(), detect_platform(), root,
        )
        record.resources = [tracking_claims.ResourceClaim(
            kind="task", ref=task.id, state="active", note="context handoff",
        )]
        tracking.save_record(record, records / "shared-id.yaml")
        roots[name] = root

    current = {"project": "coordinator"}
    monkeypatch.setattr(cfg, "project_name", lambda: current["project"])
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: configs[current["project"]])
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **k: configs[name])
    monkeypatch.setattr(
        "agent_worktrees.config_authority.load_project_authority_config",
        lambda name, **k: configs[name],
    )
    monkeypatch.setattr(
        cfg, "project_dir", lambda name=None: roots[name or current["project"]],
    )
    monkeypatch.setattr(
        cfg, "tracking_dir",
        lambda name=None: roots[name or current["project"]] / "worktrees",
    )
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id", lambda wid, config=None: wid or "shared-id",
    )
    calls: list[list[str]] = []

    def capture(*argv: str, timeout: float) -> SimpleNamespace:
        calls.append(list(argv))
        assert argv[0] == "claims", "The focused release witness must not call another owner"
        args = cli.build_parser().parse_args(list(argv))
        with cli_output.capture_json_output() as output:
            code = cli.cmd_claims(args)
        return SimpleNamespace(returncode=code, stdout=output.getvalue(), stderr="")

    monkeypatch.setattr(procutil, "run_agent_worktrees_capture", capture)
    terminal = queue.abandon(task.id, permitted=True, expected_status=Status.PROPOSED)
    assert terminal.status == Status.ABANDONED
    assert terminal.attempts == 0 and terminal.owner is None
    return {
        "task": asdict(terminal), "release": handoff_claim_release,
        "roots": roots, "current": current, "calls": calls,
        "queue": queue, "configs": configs,
    }


def _record(owners: dict[str, Any], project: str) -> tracking.WorktreeRecord:
    return tracking.load_record(owners["roots"][project] / "worktrees" / "shared-id.yaml")


def _claims(*argv: str) -> tuple[int, dict[str, Any]]:
    args = cli.build_parser().parse_args(["claims", *argv, "--json"])
    with cli_output.capture_json_output() as output:
        code = cli.cmd_claims(args)
    return code, json.loads(output.getvalue())


def _journal_binding(owners: dict[str, Any]) -> dict[str, Any]:
    owners["current"]["project"] = "creator"
    code, result = _claims(
        "add", "task", owners["task"]["id"], "--worktree", "shared-id",
        "--note", "context handoff",
    )
    assert code == 0
    binding: dict[str, Any] = result["claim_binding"]
    assert binding["contract"] == "context-handoff-creator-claim/v1"
    assert binding["project"] == "creator"
    assert binding["creation_nonce"] and binding["claim_nonce"]
    return binding


def _settle_binding(owners: dict[str, Any], binding: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return _claims(
        "settle", owners["task"]["id"], "--released",
        "--owner-ref", f'{binding["machine"]}/{binding["project"]}/{binding["worktree_id"]}',
        "--expected-record-nonce", binding["creation_nonce"],
        "--expected-claim-nonce", binding["claim_nonce"], "--expected-kind", "task",
    )


def test_journaling_returns_durable_idempotent_task_binding(owners: dict[str, Any]) -> None:
    first = _journal_binding(owners)
    second = _journal_binding(owners)
    assert second == first
    record = _record(owners, "creator")
    assert first["creation_nonce"] == record.creation_nonce
    assert first["claim_nonce"] == record.resources[0].claim_nonce


@pytest.mark.parametrize("stale_change", ["title", "state", "kind", "omission"])
def test_stale_record_cannot_restore_an_obsolete_task_claim(
    owners: dict[str, Any], stale_change: str,
) -> None:
    first = _journal_binding(owners)
    stale = _record(owners, "creator")
    assert _settle_binding(owners, first)[0] == 0
    tracking.save_record(stale)
    assert _record(owners, "creator").resources[0].state == "released"
    replacement = _journal_binding(owners)
    assert replacement["claim_nonce"] != first["claim_nonce"]
    stale.title = "An unrelated status update"
    if stale_change == "state":
        stale.resources[0].state = "released"
    elif stale_change == "kind":
        stale.resources[0].kind = "workdir"
    elif stale_change == "omission":
        stale.resources = []
    path = owners["roots"]["creator"] / "worktrees" / "shared-id.yaml"
    tracking.save_record(stale, path)
    current = _record(owners, "creator")
    assert current.title == stale.title
    assert len(current.resources) == 1
    assert current.resources[0].kind == "task"
    assert current.resources[0].claim_nonce == replacement["claim_nonce"]
    assert current.resources[0].state == "active"
    before = path.read_bytes()
    assert _settle_binding(owners, first)[0] != 0
    assert path.read_bytes() == before
    assert _settle_binding(owners, replacement)[0] == 0
    active = _journal_binding(owners)
    assert active["claim_nonce"] != replacement["claim_nonce"]
    assert _claims("release", first["ref"], "--worktree", "shared-id", "--remove")[0] == 0
    assert _record(owners, "creator").resources == []


@pytest.mark.parametrize("explicit_removal", [False, True])
def test_terminal_task_claim_cannot_be_resurrected_by_two_stale_saves(
    owners: dict[str, Any], explicit_removal: bool,
) -> None:
    binding = _journal_binding(owners)
    stale = _record(owners, "creator")
    omitted = _record(owners, "creator")
    omitted.resources = []
    assert _settle_binding(owners, binding)[0] == 0
    if explicit_removal:
        assert _claims("release", binding["ref"], "--worktree", "shared-id", "--remove")[0] == 0
    else:
        tracking.save_record(omitted)
    tracking.save_record(stale)
    current = _record(owners, "creator")
    if explicit_removal:
        assert current.resources == []
        replacement = _journal_binding(owners)
        assert replacement["claim_nonce"] != binding["claim_nonce"]
        assert _settle_binding(owners, replacement)[0] == 0
    else:
        assert len(current.resources) == 1
        assert current.resources[0].claim_nonce == binding["claim_nonce"]
        assert current.resources[0].state == "released"
    assert _assert_obligations_settled(_record(owners, "creator"), "shared-id", abandon=False)


@pytest.mark.parametrize("native", [False, True])
def test_stale_task_binding_cannot_replace_a_workdir_claim(
    owners: dict[str, Any], native: bool,
) -> None:
    binding = _journal_binding(owners)
    stale = _record(owners, "creator")
    if native:
        assert _claims("add", "workdir", binding["ref"], "--worktree", "shared-id")[0] == 0
    else:
        fresh = _record(owners, "creator")
        fresh.resources[0].kind = "workdir"
        fresh.resources[0].claim_nonce = ""
        tracking.save_record(fresh)
    replacement = _record(owners, "creator").resources[0]
    tracking.save_record(stale)
    current = _record(owners, "creator")
    assert current.resources[0].kind == "workdir"
    assert current.resources[0].claim_nonce == replacement.claim_nonce
    assert replacement.claim_nonce and replacement.claim_nonce != binding["claim_nonce"]
    path = current.yaml_path
    assert path is not None
    before = path.read_bytes()
    assert _settle_binding(owners, binding)[0] != 0
    assert path.read_bytes() == before
    assert not _assert_obligations_settled(current, "shared-id", abandon=False)


def test_fresh_full_record_reactivation_rotates_the_task_incarnation(
    owners: dict[str, Any],
) -> None:
    binding = _journal_binding(owners)
    assert _settle_binding(owners, binding)[0] == 0
    fresh = _record(owners, "creator")
    fresh.resources[0].state = "active"
    tracking.save_record(fresh)
    current = _record(owners, "creator")
    assert current.resources[0].state == "active"
    assert current.resources[0].claim_nonce != binding["claim_nonce"]
    assert _settle_binding(owners, binding)[0] != 0


def test_stale_task_snapshot_cannot_overwrite_a_recreated_unbound_claim(
    owners: dict[str, Any],
) -> None:
    binding = _journal_binding(owners)
    stale = _record(owners, "creator")
    assert _claims("release", binding["ref"], "--worktree", "shared-id", "--remove")[0] == 0
    assert _claims("add", "workdir", binding["ref"], "--worktree", "shared-id")[0] == 0
    replacement = _record(owners, "creator").resources[0]
    assert replacement.kind == "workdir" and not replacement.claim_nonce
    tracking.save_record(stale)
    current = _record(owners, "creator")
    assert current.resources[0].kind == "workdir"
    assert not current.resources[0].claim_nonce
    assert _settle_binding(owners, binding)[0] != 0
    assert not _assert_obligations_settled(current, "shared-id", abandon=False)


@pytest.mark.parametrize("replacement", ["record", "claim", "kind"])
def test_bound_settlement_refuses_replaced_identity(
    owners: dict[str, Any], replacement: str,
) -> None:
    binding = _journal_binding(owners)
    path = owners["roots"]["creator"] / "worktrees" / "shared-id.yaml"
    record = tracking.load_record(path)
    if replacement == "record":
        path.write_text(
            path.read_text(encoding="utf-8").replace(binding["creation_nonce"], "replacement-record"),
            encoding="utf-8",
        )
    elif replacement == "claim":
        record.resources[0].claim_nonce = "replacement-claim"
        tracking.save_record(record, path)
    else:
        record.resources[0].kind = "workdir"
        tracking.save_record(record, path)
    before = path.read_bytes()
    owners["current"]["project"] = "coordinator"
    code, result = _settle_binding(owners, binding)
    assert code != 0 and result.get("error")
    assert path.read_bytes() == before
    assert _record(owners, "coordinator").resources[0].state == "active"


def test_bound_settlement_is_cross_project_and_idempotent(owners: dict[str, Any]) -> None:
    binding = _journal_binding(owners)
    unrelated = owners["roots"]["coordinator"] / "worktrees" / "shared-id.yaml"
    before = unrelated.read_bytes()
    owners["current"]["project"] = "coordinator"
    for _ in range(2):
        code, result = _settle_binding(owners, binding)
        assert code == 0 and result["disposition"] == "released"
    record = _record(owners, "creator")
    assert record.resources[0].state == "released"
    assert _assert_obligations_settled(record, "shared-id", abandon=False)
    assert unrelated.read_bytes() == before


@pytest.mark.parametrize("project", ["creator", "coordinator"])
def test_terminal_release_reconciles_originating_claim(
    owners: dict[str, Any], project: str,
) -> None:
    from agent_dispatch.handoff_claim_binding import bind_claim

    binding = _journal_binding(owners)
    task = asdict(bind_claim(owners["queue"], owners["task"]["id"], binding))
    owners["current"]["project"] = project
    before = _record(owners, "creator")
    assert not _assert_obligations_settled(before, "shared-id", abandon=False)
    owners["release"]._release_task_claim(
        owners["task"]["id"], worktree="shared-id", task=task,
    )
    after = _record(owners, "creator")
    assert after.resources[0].state == "released"
    assert _assert_obligations_settled(after, "shared-id", abandon=False)


def test_terminal_release_preserves_matching_leaf_in_other_project(
    owners: dict[str, Any],
) -> None:
    from agent_dispatch.handoff_claim_binding import bind_claim

    binding = _journal_binding(owners)
    task = asdict(bind_claim(owners["queue"], owners["task"]["id"], binding))
    owners["current"]["project"] = "coordinator"
    path = owners["roots"]["coordinator"] / "worktrees" / "shared-id.yaml"
    before = path.read_bytes()
    owners["release"]._release_task_claim(
        owners["task"]["id"], worktree="shared-id", task=task,
    )
    assert path.read_bytes() == before
    assert _record(owners, "coordinator").resources[0].state == "active"


def test_unbound_legacy_task_requires_explicit_owner_retry(owners: dict[str, Any]) -> None:
    result = owners["release"].reconcile_claim(owners["queue"], owners["task"]["id"])
    assert result["taskTerminal"] and not result["claimSettled"]
    assert owners["calls"] == []
    assert _record(owners, "creator").resources[0].state == "active"
    unrelated = owners["roots"]["coordinator"] / "worktrees" / "shared-id.yaml"
    before = unrelated.read_bytes()
    result = owners["release"].reconcile_claim(
        owners["queue"], owners["task"]["id"],
        owner_ref=f'{socket.gethostname()}/creator/shared-id',
    )
    assert result["claimSettled"]
    assert owners["queue"].get(owners["task"]["id"]).handoff_claim == result["claimBinding"]
    assert _record(owners, "creator").resources[0].state == "released"
    assert unrelated.read_bytes() == before


def test_native_settlement_retains_a_newly_associated_candidate(owners: dict[str, Any]) -> None:
    binding = _journal_binding(owners)
    path = owners["roots"]["creator"] / "worktrees" / "shared-id.yaml"
    record = tracking.load_record(path)
    record.handoffs.append(tracking.SessionHandoff(
        ordinal=1, token=owners["task"]["id"], predecessor="predecessor",
        state="pending", opened_at=tracking._now_iso(), candidate="successor",
    ))
    tracking.save_record(record, path)
    before = path.read_bytes()
    owners["current"]["project"] = "coordinator"
    code, result = _settle_binding(owners, binding)
    assert code != 0 and "candidate" in result["error"]
    assert path.read_bytes() == before
    assert not _assert_obligations_settled(_record(owners, "creator"), "shared-id", abandon=False)


def test_delivered_async_hook_settles_the_actual_creator_record(
    owners: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch.handoff_claim_binding import bind_claim

    binding = _journal_binding(owners)
    task = asdict(bind_claim(owners["queue"], owners["task"]["id"], binding))
    owners["current"]["project"] = "coordinator"
    delivered = threading.Event()
    body = owners["release"]._release_in_background

    def observe(*args: Any, **kwargs: Any) -> None:
        try:
            body(*args, **kwargs)
        finally:
            delivered.set()

    monkeypatch.setattr(owners["release"], "_release_in_background", observe)
    owners["release"].release_if_handoff(task, queue=owners["queue"])
    assert delivered.wait(10)
    assert _record(owners, "creator").resources[0].state == "released"
    assert _record(owners, "coordinator").resources[0].state == "active"


@pytest.mark.parametrize("abort_project,genuine_obligation", [
    ("creator", False), ("coordinator", False), ("coordinator", True),
])
def test_real_node_save_trigger_abort_and_retry(
    owners: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, abort_project: str,
    genuine_obligation: bool,
) -> None:
    from agent_dispatch.handoff_claim_binding import CONTRACT
    from agent_dispatch.queue_records import Status
    from agent_worktrees import finalize as finalize_owner, git_ops, handoff_cancel_cli, session_binding_cli, sessions
    from agent_worktrees.finalize import validate_and_finalize

    for project in ("creator", "coordinator"):
        path = owners["roots"][project] / "worktrees" / "shared-id.yaml"
        record = tracking.load_record(path)
        record.resources = []
        tracking.save_record(record, path)
    monkeypatch.setattr(session_binding_cli, "_activate_project_for_path", lambda *_a, **_k: None)
    monkeypatch.setattr("agent_worktrees.hook_ipc.send_best_effort", lambda *_a, **_k: None)
    root = Path(__file__).resolve().parents[3]
    core = root / "plugins" / "context-handoff" / "extensions" / "context-handoff" / "handoff-core.mjs"
    state = tmp_path / "handoff-state"
    state.mkdir()
    creator_cwd = owners["roots"]["creator"] / "shared-id"
    abort_cwd = owners["roots"][abort_project] / "shared-id"
    topology = creator_cwd / ".agent-worktrees" / "machines.yaml"
    topology.unlink()
    topology.parent.rmdir()
    anchor = owners["roots"]["creator"] / "anchor"
    git_ops.git("init", "--initial-branch=main", str(anchor), cwd=str(tmp_path))
    git_ops.git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Fixture",
        cwd=str(anchor),
    )
    git_ops.git(
        "worktree", "add", "-b", "worktree/shared-id", str(creator_cwd), "main", cwd=str(anchor),
    )
    (anchor / ".git" / "info" / "exclude").write_text(".agent-worktrees/\n", encoding="utf-8")
    for checkout in (anchor, creator_cwd):
        private_config = checkout / ".agent-worktrees"
        private_config.mkdir()
        (private_config / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    config = owners["configs"]["creator"]
    repo = config.default_repo
    owners["configs"]["creator"] = replace(config, repos={
        "creator": replace(
            repo, anchor=str(anchor), default_branch="main",
            pr=replace(repo.pr, enabled=False, required=False),
        ),
    })
    monkeypatch.setattr(sessions, "has_mux_session", lambda *_a, **_k: False)
    monkeypatch.setattr(finalize_owner, "_warn_of_codespace_claims_for_worktree", lambda *_a: None)
    monkeypatch.setattr(finalize_owner, "_warn_of_dev_slot_claims_for_worktree", lambda *_a: None)
    script = r"""
import { pathToFileURL } from "node:url";
import { readSync, writeSync } from "node:fs";
const core = await import(pathToFileURL(process.argv[1]));
const [state, creatorCwd, abortCwd, machine] = process.argv.slice(2);
function execute(bin, argv, options = {}) {
  writeSync(1, JSON.stringify({ bin, argv, cwd: options.cwd }) + "\n");
  let line = "", byte = Buffer.alloc(1);
  while (readSync(0, byte, 0, 1, null) === 1 && byte[0] !== 10) line += byte.toString();
  const response = JSON.parse(line);
  if (response.error) throw new Error(response.error);
  return response.value;
}
const get = key => ({
  "worktree-dir": creatorCwd, "worktree-state-dir": state, machine,
})[key] || null;
const stored = core.dispatchHandoff("Continue the disposable fixture.", "predecessor", creatorCwd, "Fixture", { get, execute });
if (!stored?.metadata?.creatorClaim) throw new Error("creator binding was not durably recorded");
const noted = core.noteHandoffInRecord(creatorCwd, "predecessor", stored.id, "Fixture", false, execute);
if (!noted.noted || noted.liveCutover || !noted.raw.handoff_ordinal) throw new Error("manual-only handoff was not recorded");
const saved = core.writeSessionStateHandoff({
  sid: "predecessor", promptText: "Continue the disposable fixture.", seed: "fixture",
  stored: { ...stored, storage: "agent-dispatch", taskId: stored.id },
});
if (!saved.ok) throw new Error(saved.error);
const first = core.abortHandoffTask(abortCwd, stored.id, "cancel fixture", execute);
const retry = core.abortHandoffTask(abortCwd, stored.id, "retry fixture", execute);
writeSync(1, JSON.stringify({ result: { stored, first, retry } }) + "\n");
"""
    abandonments = []
    unrelated_before: bytes | None = None

    def handle(request: dict[str, Any]) -> str:
        nonlocal unrelated_before
        result: Any
        bin_name, argv = request["bin"], request["argv"]
        if bin_name == "agent-worktrees":
            owners["current"]["project"] = (
                "creator" if request.get("cwd") == str(creator_cwd) else "coordinator"
            )
            args = cli.build_parser().parse_args(argv)
            handler = {
                "claims": cli.cmd_claims, "note-handoff": session_binding_cli.cmd_note_handoff,
                "cancel-handoff": handoff_cancel_cli.cmd_cancel_handoff,
            }[argv[0]]
            if argv[0] == "note-handoff":
                args.worktree_dir = str(creator_cwd)
            with cli_output.capture_json_output() as output:
                code = handler(args)
            if code:
                raise AssertionError(output.getvalue())
            return output.getvalue()
        assert bin_name == "agent-dispatch"
        owners["current"]["project"] = "coordinator"
        queue = owners["queue"]
        if argv[0] == "health":
            result = {"status": "ok", "handoff_claim_contract": CONTRACT}
        elif argv[0] == "create":
            def value(flag: str) -> str:
                return str(argv[argv.index(flag) + 1])
            task = queue.create(
                str(argv[1]), repo="https://github.com/example/project", status=Status.PROPOSED,
                labels=["handoff"], source="context-handoff", dedup_key=value("--dedup-key"),
                target_worktree=value("--target-worktree"), target_machine=value("--target-machine"),
                payload_inline=Path(value("--payload-file")).read_text(encoding="utf-8"),
            )
            result = {**asdict(task), "handoff_claim_contract": CONTRACT}
            other_path = owners["roots"]["coordinator"] / "worktrees" / "shared-id.yaml"
            other = tracking.load_record(other_path)
            other.resources = [tracking_claims.ResourceClaim(kind="task", ref=task.id)]
            tracking.save_record(other, other_path)
            unrelated_before = other_path.read_bytes()
        elif argv[0] == "handoff-claim":
            binding = (
                json.loads(Path(argv[argv.index("--binding-file") + 1]).read_text(encoding="utf-8"))
                if "--binding-file" in argv else None
            )
            result = owners["release"].reconcile_claim(queue, argv[1], binding=binding)
        elif argv[0] == "show":
            result = {**asdict(queue.get(argv[1])), "handoff_claim_contract": CONTRACT}
        elif argv[0] == "list":
            result = [asdict(task) for task in queue.list(status=["proposed", "queued"], label="handoff")]
        elif argv[0] == "payload":
            return queue.read_payload(argv[1]) or ""
        elif argv[0] == "abandon":
            abandonments.append(argv[1])
            result = asdict(queue.abandon(
                argv[1], permitted=True, expected_status=argv[argv.index("--expected-status") + 1],
            ))
        else:
            pytest.fail(f"unexpected disposable task operation: {argv[0]}")
        return json.dumps(result)

    result: dict[str, Any] | None = None
    with (tmp_path / "node.stderr").open("w+", encoding="utf-8") as stderr:
        with subprocess.Popen(
            [
                shutil.which("node") or "node", "--input-type=module", "-e", script,
                str(core), str(state), str(creator_cwd), str(abort_cwd), socket.gethostname(),
            ],
            env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True,
            **no_window_kwargs(),
        ) as child:
            try:
                assert child.stdout is not None and child.stdin is not None
                for line in child.stdout:
                    request = json.loads(line)
                    if "result" in request:
                        result = request["result"]
                        break
                    try:
                        response = {"value": handle(request)}
                    except (AssertionError, KeyError, ValueError) as exc:
                        response = {"error": str(exc)}
                    child.stdin.write(json.dumps(response) + "\n")
                    child.stdin.flush()
                code = child.wait(timeout=15)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
        stderr.seek(0)
        assert code == 0, stderr.read()
    assert result is not None
    task_id = result["stored"]["id"]
    assert abandonments == [task_id]
    assert all(result[key]["taskCancelled"] and result[key]["claimSettled"] for key in ("first", "retry"))
    terminal = owners["queue"].get(task_id)
    assert terminal.status == Status.ABANDONED and terminal.attempts == 0 and terminal.owner is None
    record = _record(owners, "creator")
    assert next(claim for claim in record.resources if claim.ref == task_id).state == "released"
    assert next(handoff for handoff in record.handoffs if handoff.token == task_id).state == "cancelled"
    assert _assert_obligations_settled(record, "shared-id", abandon=False)
    assert (owners["roots"]["coordinator"] / "worktrees" / "shared-id.yaml").read_bytes() == unrelated_before
    if genuine_obligation:
        record.resources.append(tracking_claims.ResourceClaim(kind="task", ref="another-task"))
        tracking.save_record(record, owners["roots"]["creator"] / "worktrees" / "shared-id.yaml")
    owners["current"]["project"] = "creator"
    finalized = validate_and_finalize(
        "shared-id", owners["configs"]["creator"], retain_checkout=True,
    )
    assert finalized is not genuine_obligation
    assert _record(owners, "creator").status == ("active" if genuine_obligation else "finalized")
    assert creator_cwd.is_dir()
