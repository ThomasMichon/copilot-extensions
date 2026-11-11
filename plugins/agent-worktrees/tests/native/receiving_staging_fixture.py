"""Fresh source-candidate consumers; positive admission is fixture-owned only."""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import sys
from contextlib import ExitStack
from unittest.mock import patch

mode, home_text, source_package, expected_text, *cli_args = sys.argv[1:]
home = Path(home_text)

def audit(event, args):
    if event in {"socket.connect", "socket.bind"}:
        raise RuntimeError("source fixture must not contact a backend")
    if event == "subprocess.Popen":
        command = args[1]
        if isinstance(command, (list, tuple)):
            arguments = list(command)
        else:
            import shlex
            arguments = shlex.split(command, posix=False)
        executable = Path(arguments[0].strip('"')).stem.casefold()
        assert Path(args[2] or Path.cwd()).resolve().is_relative_to(home.resolve().parent)
        if executable == "robocopy" and mode == "flow":
            source, stage = (Path(value.strip('"')).resolve() for value in arguments[1:3])
            assert source.parent == home / "old-root" and stage.parent == home / "new-root"
            assert stage.name.startswith(source.name + ".relocating-")
            assert arguments[3:] == [
                "/E", "/COPY:DATS", "/SL", "/DCOPY:DAT", "/XJ", "/R:0", "/W:0",
                "/NFL", "/NDL", "/NJH", "/NJS", "/UNICODE",
            ]
        elif executable != "git":
            raise RuntimeError("fixture permits only local Git or its exact private native copy")
    if mode == "lookup":
        if event == "open" and not isinstance(args[0], int):
            flags = args[2] if isinstance(args[2], int) else 0
            if Path(args[0]).resolve().is_relative_to(home):
                assert not any(word in (args[1] or "") for word in "wax+")
                assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            for path in args[:2] if event == "os.rename" else args[:1]:
                if isinstance(path, str):
                    assert not Path(path).resolve().is_relative_to(home)


sys.addaudithook(audit)
import agent_worktrees
from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import config as cfg, execution_spaces, git_ops, receiving_allocation, retained_checkout, tracking
from agent_worktrees import permissions, sessions
from agent_worktrees.worktree_allocation_receiver import receive

expected = json.loads(expected_text)
prefix = Path(sys.prefix).resolve()
assert sys.flags.isolated and sys.flags.dont_write_bytecode
assert Path(agent_worktrees.__file__).resolve().parent == Path(source_package).resolve()
for name, digest in expected.items():
    path = Path(agent_worktrees.__file__).parent / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
assert not (prefix / "Lib" / "site-packages" / "__editable__.pth").exists()
provenance = {
    "python": sys.executable, "sys_prefix": sys.prefix,
    "module": agent_worktrees.__file__, "version": importlib.metadata.version("agent-worktrees"),
    "modules_sha256": expected, "evidence_tier": "source_candidate_not_installed",
}

if mode == "flow":
    home.mkdir()
    assert cfg._home().resolve() == home.resolve()
    from types import SimpleNamespace
    from agent_worktrees import owned_worktree as allocation, resource_producer as producer
    from agent_worktrees import tracking_allocation_write as origin_owner

    def forbidden(*_args, **_kwargs):
        raise AssertionError("source consumer attempted an unqualified native owner probe")

    with ExitStack() as fixtures:
        fixtures.enter_context(patch.object(permissions, "_copilot_dir", lambda: home / ".copilot"))
        fixtures.enter_context(patch.object(sessions, "_session_state_dir", lambda: home / ".copilot" / "session-state"))
        fixtures.enter_context(patch.object(sessions, "verify_worktree_active", forbidden))
        anchor, old_root, new_root = home / "anchor", home / "old-root", home / "new-root"
        for directory in (anchor, old_root, new_root):
            directory.mkdir()
        git_ops.git("init", "--template=", "-b", "main", cwd=anchor)
        (anchor / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
        (anchor / "tracked.txt").write_bytes(b"source consumer fixture\n")
        git_ops.git("add", ".", cwd=anchor)
        git_ops.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-m", "fixture", cwd=anchor)
        git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=anchor)
        git_ops.git("remote", "add", "origin", str(anchor), cwd=anchor)
        cfg.set_active_project("sample")
        machine = cfg.detect_machine()
        project = cfg.project_dir("sample")
        project.mkdir()
        configuration = {
            "repo_name": "sample", "machine": machine, "srcroot": str(home),
            "repos": {"sample": {"anchor": str(anchor), "worktree_root": str(old_root),
                                "remote": "origin", "default_branch": "main"}},
        }
        config_path = project / "config.yaml"
        config_path.write_text(json.dumps(configuration), encoding="utf-8")
        config = cfg.load_project_config("sample")
        origin_id = machine + "-win-20200101-000000-abcd"
        origin_checkout = old_root / origin_id
        git_ops.create_worktree(str(anchor), str(origin_checkout), "worktree/" + origin_id, "HEAD")
        origin = tracking.create_new_record(
            origin_id, "worktree/" + origin_id, str(origin_checkout), "sample",
            config.machine, config.platform, cfg.tracking_dir("sample"),
        )
        origin.status = "active"
        tracking.save_record(origin)
        context = execution_spaces.current_execution_context(config)
        origin_ref = tracking.format_claim_ref(origin.execution_machine, origin.repo, origin.worktree_id)
        target = {"machine": context.execution_machine, "project": "sample", "options": {"no_pair": True}}
        with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
            claim, _ = origin_owner._reserve(
                origin, tracking.parse_claim_ref(origin_ref),
                {"request_id": "receiving-root-fixture", "target": target},
            )
            with producer.transaction(origin.yaml_path):
                tracking.save_record(origin)
        envelope = {
            "schema": allocation.SCHEMA, "version": allocation.VERSION, "origin": origin_ref,
            "request_id": claim.producer["request_id"], "token": claim.producer["token"],
            "target": target, "identity": None,
        }
        created = receive("create", envelope, config)
        assert created["status"] == "allocated"
        child_id = allocation.qualified_ref(created["identity"]["ref"])[2]
        child = tracking.load_record(cfg.tracking_dir("sample") / (child_id + ".yaml"))
        with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
            origin = tracking.load_record(origin.yaml_path)
            claim = origin_owner._matches(origin, {"token": envelope["token"]})
            origin_owner._transition(claim, "bind", created)
            with producer.transaction(origin.yaml_path):
                tracking.save_record(origin)
        child.status = "finalized"
        tracking.save_record(child)
        envelope["identity"] = created["identity"]
        concluded = receive("inspect", envelope, config)
        assert concluded["status"] == "finalized"
        with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
            origin = tracking.load_record(origin.yaml_path)
            claim = origin_owner._matches(origin, {"token": envelope["token"]})
            origin_owner._transition(claim, "close", concluded)
            with producer.transaction(origin.yaml_path):
                tracking.save_record(origin)
        fixture = SimpleNamespace(
            record=child, source=Path(child.worktree_path), destination=new_root / child_id,
            origin=origin, envelope=envelope,
        )
        ledger = fixture.record.yaml_path
        creation = ledger.parent.parent / "workspace-creation.sqlite3"
        original_db = creation.read_bytes()
        original_receipt = fixture.record.receiving_allocation
        original_identity = receiving_allocation.record_identity(fixture.record).to_dict()
        original_creator = fixture.origin.yaml_path.read_bytes()
        path = cfg.project_dir("sample") / "config.yaml"
        configuration = json.loads(path.read_text(encoding="utf-8"))
        old_root = fixture.source.parent
        new_root = fixture.destination.parent
        configuration["repos"]["sample"]["worktree_root"] = str(new_root)
        path.write_text(json.dumps(configuration), encoding="utf-8")
        current_config = cfg.load_project_config("sample")
        assert Path(current_config.default_repo.worktree_root) == new_root
        assert retained_checkout.matches(fixture.origin, current_config)
        assert retained_checkout.matches(fixture.record, current_config)
        execution_spaces.require_record_mutation(fixture.record, current_config, recover_receiving=False)
        before_plan = (ledger.read_bytes(), creation.read_bytes(), fixture.origin.yaml_path.read_bytes())
        try:
            relocation.plan(child_id, str(fixture.destination), current_config)
        except ValueError as error:
            assert "qualified passive native owner evidence" in str(error)
        else:
            raise AssertionError("real passive plan unexpectedly claimed complete owner evidence")
        assert before_plan == (ledger.read_bytes(), creation.read_bytes(), fixture.origin.yaml_path.read_bytes())
        assert not fixture.destination.exists()

        def fixture_admission(record, path):
            assert record.worktree_id == child_id
            assert Path(path).parent.resolve() in {old_root.resolve(), new_root.resolve()}
            assert not record.sessions and not record.head_session and record.status == "finalized"

        fixtures.enter_context(patch.object(relocation, "_stopped", fixture_admission))
        result = relocation.move(fixture.record.worktree_id, str(fixture.destination), current_config)
        assert result["phase"] == "complete"
        current = tracking.load_record(ledger)
        assert Path(current.worktree_path) == fixture.destination
        assert current.receiving_allocation == original_receipt
        assert receiving_allocation.record_identity(current).to_dict() == original_identity
        assert creation.read_bytes() == original_db
        assert fixture.origin.yaml_path.read_bytes() == original_creator
        assert retained_checkout.matches(current, current_config)
        payload = {
            "ledger": str(ledger), "origin_ledger": str(fixture.origin.yaml_path),
            "destination": str(fixture.destination), "old_root": str(old_root), "new_root": str(new_root),
            "envelope": fixture.envelope, "original_identity": original_identity,
            "original_receipt": original_receipt,
            "creation_sha256": hashlib.sha256(original_db).hexdigest(),
            "creator_sha256": hashlib.sha256(original_creator).hexdigest(),
            "provenance": provenance, "real_passive_plan_refused_unknown": True,
        }
        (home / "continuation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "flow", "passed": True, **payload}), flush=True)
elif mode == "lookup":
    print("SOURCE_LOOKUP_PROOF=" + json.dumps(provenance), file=sys.stderr, flush=True)
    sys.argv = ["agent-worktrees", *cli_args]
    runpy.run_module("agent_worktrees", run_name="__main__", alter_sys=True)
elif mode == "continue":
    permissions._copilot_dir = lambda: home / ".copilot"
    sessions._session_state_dir = lambda: home / ".copilot" / "session-state"
    data = json.loads((home / "continuation.json").read_text(encoding="utf-8"))
    cfg.set_active_project("sample")
    config = cfg.load_project_config("sample")
    current = tracking.load_record(Path(data["ledger"]))
    origin = tracking.load_record(Path(data["origin_ledger"]))
    assert Path(config.default_repo.worktree_root) == Path(data["new_root"])
    assert retained_checkout.matches(current, config) and retained_checkout.matches(origin, config)
    assert current.worktree_path == data["destination"]
    execution_spaces.require_record_mutation(current, config, recover_receiving=False)
    current.title = "fresh source-process continuation"
    tracking.save_record(current)
    assert current.receiving_allocation == data["original_receipt"]
    assert receive("inspect", data["envelope"], config)["identity"] == data["original_identity"]
    assert receive("confirm", data["envelope"], config)["status"] == "finalized"
    creation = current.yaml_path.parent.parent / "workspace-creation.sqlite3"
    assert hashlib.sha256(creation.read_bytes()).hexdigest() == data["creation_sha256"]
    assert hashlib.sha256(origin.yaml_path.read_bytes()).hexdigest() == data["creator_sha256"]
    from agent_worktrees import creation_requests, owned_worktree as allocation
    from agent_worktrees import resource_producer as producer, tracking_allocation_write as origin_owner

    admission = receiving_allocation.ReceivingAllocation.from_fields({
        key: data["envelope"][key] for key in ("origin", "request_id", "token", "target")
    })
    original_row = creation_requests.inspect(config, admission.key)
    original_claim = next(claim.producer for claim in origin.resources if claim.producer.get("token") == admission.token)
    with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
        claim, _ = origin_owner._reserve(
            origin, tracking.parse_claim_ref(data["envelope"]["origin"]),
            {"request_id": "source-new-root-allocation", "target": data["envelope"]["target"]},
        )
        with producer.transaction(origin.yaml_path):
            tracking.save_record(origin)
    envelope = {
        **data["envelope"], "identity": None, "request_id": claim.producer["request_id"],
        "token": claim.producer["token"],
    }
    new = receive("create", envelope, config)
    new_id = allocation.qualified_ref(new["identity"]["ref"])[2]
    new_record = tracking.load_record(cfg.tracking_dir("sample") / (new_id + ".yaml"))
    assert Path(new_record.worktree_path).parent == Path(data["new_root"])
    with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
        origin = tracking.load_record(origin.yaml_path)
        claim = origin_owner._matches(origin, {"token": envelope["token"]})
        origin_owner._transition(claim, "bind", new)
        with producer.transaction(origin.yaml_path):
            tracking.save_record(origin)
    new_record.status = "finalized"
    tracking.save_record(new_record)
    concluded = receive("inspect", {**envelope, "identity": new["identity"]}, config)
    with tracking._RecordLock(origin.yaml_path, require_sidecar=True):
        origin = tracking.load_record(origin.yaml_path)
        claim = origin_owner._matches(origin, {"token": envelope["token"]})
        origin_owner._transition(claim, "close", concluded)
        with producer.transaction(origin.yaml_path):
            tracking.save_record(origin)
    assert creation_requests.inspect(config, admission.key) == original_row
    assert next(claim.producer for claim in origin.resources if claim.producer.get("token") == admission.token) == original_claim
    assert receive("inspect", data["envelope"], config)["identity"] == data["original_identity"]
    print(json.dumps({
        "phase": "continue", "passed": True, "provenance": provenance,
        "recorded_destination": current.worktree_path,
        "original_proof_unchanged": True, "retained_old_origin_supported": True,
        "new_allocation_uses_new_root": True, "new_allocation": new["identity"],
        "both_creator_obligations_closed": all(claim.producer["phase"] == "closed" for claim in origin.resources),
        "continuation": "fresh process metadata/original-operation consumer, not session resume",
    }), flush=True)
else:
    raise ValueError("unknown fixture phase")
