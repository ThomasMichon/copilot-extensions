"""Owned native cross-volume qualification with persistent original custody."""

import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent_procutil._daemon_instance_identity import identity_state
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as state
from agent_worktrees import git_ops, tracking


_CRASH = """
import os, sys
from pathlib import Path
from agent_worktrees import config as cfg, checkout_relocation as move
cfg.set_active_project("sample")
target, cut = Path(sys.argv[2]), sys.argv[3]
original = move.durability.replace
def interrupted(source, destination, **kwargs):
    original(source, destination, **kwargs)
    if cut == "publication" and Path(destination) == target:
        os._exit(73)
    if cut == "retirement" and ".retiring-" in Path(destination).name:
        os._exit(74)
move.durability.replace = interrupted
move.move(sys.argv[1], str(target), cfg.load_config())
raise AssertionError("owned crash checkpoint was not reached")
"""


def _crash(fixture, cut):
    result = run_caller_owned(
        [sys.executable, "-c", _CRASH, fixture.record.worktree_id, str(fixture.destination), cut],
        env=dict(os.environ, AGENT_HOME=str(fixture.home), USERPROFILE=str(fixture.home), HOME=str(fixture.home)),
        cwd=fixture.home, timeout=100, max_stdout_bytes=128 * 1024, max_stderr_bytes=128 * 1024,
    )
    assert result.returncode == (73 if cut == "publication" else 74), (result.stdout, result.stderr)
    retained = state.read(fixture.record.yaml_path)
    assert retained["phase"] == ("copied" if cut == "publication" else "rebound")
    assert identity_state(retained["caller"]["pid"], retained["caller"]["birth"]) in {"dead", "reused"}
    return retained


def _recover(fixture, retained):
    ledger_before = fixture.record.yaml_path.read_bytes()
    journal_before = state.journal_path(fixture.record.yaml_path).read_bytes()
    planned = relocation._action_plan(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert planned["recovery"] and not planned["apply"]
    assert fixture.record.yaml_path.read_bytes() == ledger_before
    assert state.journal_path(fixture.record.yaml_path).read_bytes() == journal_before
    result = relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "complete" and result["stage"] == retained["stage"]
    return result


def qualify(fixture, request):
    value = os.environ.get("AGENT_WORKTREES_RELOCATION_SECOND_ROOT")
    if not value:
        pytest.skip("second fixture volume not enrolled")
    approved = Path(value)
    assert approved.is_dir() and fixture.source.stat().st_dev != approved.stat().st_dev
    limit = 1024 * 1024
    assert sum(path.stat().st_size for path in fixture.source.rglob("*") if path.is_file()) + 16384 <= limit
    original_manifest = relocation.transfer.manifest(fixture.source)
    nonce = fixture.record.creation_nonce
    head = git_ops.git("rev-parse", "HEAD", cwd=fixture.source).stdout.strip()
    parent = approved / ("relocation-fixture-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    parent.mkdir(mode=0o700)
    (parent / "scratch-owner.json").write_text(json.dumps({
        "owner": request.node.nodeid, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "disposable native cross-volume and original-operation crash qualification",
        "retention": "owned until confirmed successful teardown; retain failures and persistent primary custody",
    }), encoding="utf-8")
    destination = parent / fixture.record.worktree_id
    receipt = fixture.home / "cross-volume-proof.json"
    proof = {"owner": request.node.nodeid, "source": str(fixture.source), "external": str(destination),
             "git_common": str(fixture.anchor / ".git"), "ledger": str(fixture.record.yaml_path),
             "journal": str(state.journal_path(fixture.record.yaml_path)), "creation_nonce": nonce,
             "original_head": head, "status": "in_progress"}
    receipt.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    succeeded = False
    try:
        fixture.destination = destination
        outward = _crash(fixture, "publication")
        proof["publication_crash"] = {"phase": outward["phase"], "stage": outward["stage"], "caller": outward["caller"]}
        assert fixture.source.exists() and destination.exists() and not Path(outward["stage"]).exists()
        with pytest.raises(ValueError, match="publication is uncertain"):
            relocation.abort(fixture.record.worktree_id, str(destination), fixture.config)
        _recover(fixture, outward)
        assert not fixture.source.exists() and (destination / "ignored.bin").read_bytes() == bytes(range(256))
        assert sum(path.stat().st_size for path in parent.rglob("*") if path.is_file()) <= limit
        fixture.destination = fixture.source
        returning = _crash(fixture, "retirement")
        retirement = destination.with_name(destination.name + ".retiring-" + Path(returning["stage"]).name.rsplit("-", 1)[1])
        proof["retirement_crash"] = {"phase": returning["phase"], "stage": returning["stage"],
                                     "retirement": str(retirement), "caller": returning["caller"]}
        assert not destination.exists() and retirement.exists() and fixture.source.exists()
        _recover(fixture, returning)
        assert not retirement.exists() and not destination.exists()
        assert relocation.transfer.manifest(fixture.source) == original_manifest
        current = tracking.load_record(fixture.record.yaml_path)
        assert current.worktree_path == str(fixture.source) and current.creation_nonce == nonce
        assert git_ops.git("rev-parse", "HEAD", cwd=fixture.source).stdout.strip() == head
        assert not git_ops.git("status", "--porcelain", cwd=fixture.source).stdout
        assert (fixture.anchor / ".git").is_dir() and fixture.record.yaml_path.is_file()
        assert state.read(fixture.record.yaml_path)["phase"] == "complete"
        proof["status"] = "passed"
        succeeded = True
    except BaseException as exc:
        proof["status"], proof["failure"] = "retained_failure", type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        proof["finished_utc"] = datetime.now(timezone.utc).isoformat()
        receipt.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        if succeeded:
            assert sorted(path.name for path in parent.iterdir()) == ["scratch-owner.json"]
            shutil.rmtree(parent)
