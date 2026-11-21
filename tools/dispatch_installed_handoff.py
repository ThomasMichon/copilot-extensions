"""Exercise two installed Dispatch wheels against private proposal generations."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any


def worker(root: Path, phase: str, expected: str, draft_case: str = "") -> dict[str, Any]:
    from importlib.metadata import distribution
    from agent_dispatch import proposal_transfer as transfer
    from agent_dispatch.proposal_recovery import ProposalIntake

    dist = distribution("agent-dispatch")
    modules = ("agent_dispatch.queue", "agent_dispatch.proposal_recovery", "agent_dispatch.proposal_transfer")
    for module in modules:
        path = Path(str(sys.modules[module].__file__)).resolve()
        if not path.is_relative_to(Path(sys.prefix).resolve()):
            raise RuntimeError("Dispatch escaped its installed slot")
    payload = "portable fixture payload\n" * 100
    records = root / "intake" / "proposal.records"
    marker = root / "current-version"
    fields = {"decision": "accept revised input", "future-extension": {"v": [3, None, "opaque"]}}
    if phase == "seed":
        intake = ProposalIntake.create(records.parent, title="schema trial", repo="https://example.invalid/o/r",
                                       payload=payload)
        route = "create"
    else:
        dist_home = root / phase
        outgoing = root / marker.read_text(encoding="utf-8")
        intake, route = transfer.receive(records, dist_home, outgoing=outgoing)
        assert route == expected
    task = intake.queue.get(intake.identity)
    assert task is not None and task.title == "schema trial" and task.status == "proposed" and task.owner is None
    assert intake.queue.read_payload(task) == payload
    receipt = None
    if draft_case:
        original = root / "seed-draft.json"
        changed = root / "incoming-draft.json"
        if phase != "seed":
            witness = original if phase == "incoming" else changed
            assert task.card_draft == json.loads(witness.read_bytes())
            assert intake.queue.steer_receipt(task.id, "answer") == json.loads((root / "expected.json").read_bytes())
        if phase == "seed":
            receipt = intake.steer("answer", fields)
            task = intake.queue.get(intake.identity)
            assert task is not None
        if phase != "return":
            value: dict[str, Any] | None = ({"answer": "original seed"} if phase == "seed" else
                     {"answer": "B acknowledged", "nested": [1, None]} if draft_case == "saved" else None)
            draft = intake.write_draft(value, expected_revision=task.steering_revision,
                                       expected_draft_revision=task.card_draft_revision)
            assert (draft is None if value is None else draft is not None and draft["fields"] == value)
            readback = intake.queue.get(task.id)
            assert readback is not None and readback.card_draft == draft
            (original if phase == "seed" else changed).write_text(json.dumps(draft), encoding="utf-8")
    elif expected == "compatible-import":
        if phase == "seed":
            intake.steer("answer", fields)
            saved = intake.queue.save_card_draft(task.id, fields={"answer": "original draft"})
            assert saved.card_draft is not None
            (root / "expected-draft.json").write_text(json.dumps(saved.card_draft), encoding="utf-8")
        elif phase == "incoming":
            assert task.card_draft == json.loads((root / "expected-draft.json").read_bytes())
            cleared = intake.queue.clear_card_draft(task.id, expected_revision=task.steering_revision,
                                                    expected_draft_revision=task.card_draft_revision)
            assert cleared.card_draft is None
            persisted = intake.queue.get(task.id)
            assert persisted is not None and persisted.card_draft is None
        else:
            assert task.card_draft is None and task.card_draft_revision == "absent"
    if phase == "return":
        assert intake.queue.steer_receipt(task.id, "answer") == json.loads((root / "expected.json").read_bytes())
    if phase != "seed":
        receipt = intake.steer("answer", fields)
        assert receipt["wake_status"] == "not_requested"
        assert [row["fields"] for row in intake.queue.steer_log(task.id)] == [fields]
    with closing(sqlite3.connect(intake.queue.db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM wake_outbox").fetchone() == (0,)
    transfer.offer(intake)
    selected = Path(intake.queue.db_path).parent
    marker.write_text(str(selected.relative_to(root)), encoding="utf-8")
    return {"phase": phase, "task": task.id, "receipt": receipt, "route": route, "cache_format": transfer.CACHE_FORMAT,
            "version": dist.version, "prefix": sys.prefix, "module_digest": hashlib.sha256(
                Path(transfer.__file__).read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", required=True, type=Path)
    parser.add_argument("--baseline-python", required=True, type=Path)
    parser.add_argument("--candidate-python", required=True, type=Path)
    parser.add_argument("--expect-incompatible", action="store_true")
    parser.add_argument("--withhold-draft", choices=("saved", "cleared"))
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(dir=args.scratch_parent, prefix="installed-dispatch-")).resolve()
    route = ("incompatible-format" if args.expect_incompatible else
             "unavailable-cache" if args.withhold_draft else "compatible-import")
    results = []
    for phase, exe in (("seed", args.baseline_python), ("incoming", args.candidate_python),
                       ("return", args.baseline_python)):
        exe = exe.absolute()
        if phase != "seed" and (args.expect_incompatible or args.withhold_draft):
            outgoing = root / (root / "current-version").read_text(encoding="utf-8")
            outgoing.rename(root / ("withheld-" + phase))
            assert not outgoing.exists()
        env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "LANG") if k in os.environ}
        env.update(HOME=str(root), USERPROFILE=str(root), TEMP=str(root), TMP=str(root), APPDATA=str(root),
                   LOCALAPPDATA=str(root), PATH="", COPILOT_EXTENSIONS_FOOTPRINT_ROOT=str(root))
        p = subprocess.run([str(exe), "-I", "-B", __file__, "--worker", str(root), phase, route, args.withhold_draft or ""],
                           executable=str(exe), cwd=root, env=env, text=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90)
        if p.returncode:
            raise RuntimeError(p.stderr)
        result = json.loads(p.stdout)
        if phase == ("seed" if args.withhold_draft else "incoming"):
            (root / "expected.json").write_text(json.dumps(result["receipt"]), encoding="utf-8")
        results.append(result)
    a, b, back = results
    assert a["prefix"] == back["prefix"] != b["prefix"]
    assert a["version"] == back["version"] != b["version"]
    assert (a["cache_format"] != b["cache_format"]) == args.expect_incompatible
    assert a["task"] == b["task"] == back["task"] and b["receipt"] == back["receipt"]
    report = {"result": "pass", "root": str(root), "phases": results}
    (root / "result.json").write_text(json.dumps(report), encoding="utf-8")
    print(json.dumps({k: [r[k] for r in results] for k in ("phase", "version", "cache_format", "route")}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        print(json.dumps(worker(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])))
    else:
        main()
