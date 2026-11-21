"""Private two-installation drill for historical native admission; no live Bridge or ACP."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


async def worker(root: Path, phase: str, lost: bool) -> dict[str, Any]:
    from agent_bridge import native_receipt_records as records, native_send_admission, native_keyed_admission
    from agent_bridge.db import Database
    from agent_bridge.events import EventLog
    from agent_bridge.session_status import SessionStatus
    from agent_bridge.session_manager import Session, SessionManager
    from agent_bridge.transport import SpawnTarget

    for module in (records, native_send_admission, native_keyed_admission):
        if module.__file__ is None or not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise RuntimeError("Bridge module outside selected installation")
    session_id, native = "fixture-bridge", "fixture-native"
    req = native_send_admission.NativeSendPrompt(invocation_key="fixture-operation", prompt="fixture prompt",
                                                expected_native_session_id=native, caller_id="fixture", no_resume=True)
    home = root / "records"
    dbpath = root / "origin" / "session.db"
    count = 0
    with ExitStack() as stack:
        if phase == "seed":
            dbpath.parent.mkdir()
            store = records.NativeReceiptRecords.create(home)
            db = Database(dbpath)
            stack.callback(db.close)
            manager = SessionManager(db)
            session = Session(session_id, "fixture", SpawnTarget(type="local", cwd=str(root)))
            session.status = SessionStatus.IDLE
            session.acp_session_id = native
            session.client = MagicMock(is_running=True)
            session.event_log = EventLog(db=db, session_id=session_id)
            db.create_session(session_id, "fixture", None, str(root), "local", "idle", 1.0)
            manager._sessions[session_id] = session
            prompt = stack.enter_context(patch.object(manager, "_run_prompt", new_callable=AsyncMock))
            stack.enter_context(patch.object(manager, "_notify_host_reapable", new_callable=AsyncMock))
            for name in ("resume_session", "handoff_session"):
                stack.enter_context(patch.object(manager, name, side_effect=AssertionError(name)))
            original = native_send_admission.submit

            async def transport(*args: Any) -> dict[str, Any]:
                reply = await original(*args)
                if lost:
                    raise ConnectionError("lost after journal admission")
                return reply

            with patch.object(records, "submit", transport):
                try:
                    await store.submit_once(manager, session_id, req)
                except ConnectionError:
                    if not lost:
                        raise
            assert session._prompt_task is not None
            await session._prompt_task
            db.flush()
            count = prompt.await_count
            assert count == len(db.get_turns(session_id)) == 1
        else:
            store = records.NativeReceiptRecords(home / "native-receipts.db")
            stack.enter_context(patch.object(records, "submit", side_effect=AssertionError("duplicate send")))
            if phase == "confirm":
                # Only the original generation gets its journal back. This facade
                # exposes holder lookup and the genuine turn journal, no lifecycle verbs.
                db = Database(dbpath)
                stack.callback(db.close)
                holder = SimpleNamespace(session_id=session_id, acp_session_id=native, _turn_start_lock=asyncio.Lock())
                original_owner = SimpleNamespace(get_session=lambda sid: holder if sid == session_id else None, db=db)
                await store.reconcile(original_owner, session_id, req)
                assert len(db.get_turns(session_id)) == 1
            else:
                assert not dbpath.parent.exists()
                unavailable = SimpleNamespace(get_session=lambda _: None)
                try:
                    await store.submit_once(unavailable, session_id, req)
                except KeyError:
                    if phase != "unavailable" or not lost:
                        raise
        receipt = store.inspect(session_id, req)
        if lost and phase in ("seed", "unavailable"):
            assert receipt is None
        else:
            assert receipt == native_keyed_admission.admission_receipt(session_id, 0, req)
    return {"phase": phase, "receipt": receipt, "prompts": count, "prefix": sys.prefix, "version": version("agent-bridge")}


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--scratch-parent", required=True, type=Path)
    cli.add_argument("interpreters", nargs=2, type=Path)
    args = cli.parse_args()
    a, b = [p.absolute() for p in args.interpreters]
    root = Path(tempfile.mkdtemp(dir=args.scratch_parent, prefix="bridge-receipt-")).absolute()
    for lost in (False, True):
        lane = root / ("ambiguous" if lost else "confirmed")
        lane.mkdir()
        results = []
        for phase, exe in (("seed", a), ("unavailable", b), ("confirm", a), ("return", b)):
            if phase in ("unavailable", "return"):
                (lane / "origin").rename(lane / "withheld")
            elif phase == "confirm":
                (lane / "withheld").rename(lane / "origin")
            env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "LANG") if k in os.environ}
            env.update(HOME=str(lane), USERPROFILE=str(lane), TEMP=str(lane), TMP=str(lane),
                       APPDATA=str(lane), LOCALAPPDATA=str(lane), PATH="")
            proc = subprocess.run([str(exe), "-I", "-B", __file__, "--worker", str(lane), phase, str(int(lost))],
                                  executable=str(exe), cwd=lane, env=env, capture_output=True, text=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90)
            if proc.returncode:
                raise RuntimeError(proc.stderr)
            results.append(json.loads(proc.stdout))
        assert results[0]["version"] != results[1]["version"] and results[0]["prefix"] != results[1]["prefix"]
        assert sum(r["prompts"] for r in results) == 1
        assert results[-1]["receipt"] == results[-2]["receipt"]
        (lane / "result.json").write_text(json.dumps(results), encoding="utf-8")
        print(lane.name, [r["version"] for r in results], ["accepted" if r["receipt"] else "unknown" for r in results])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        print(json.dumps(asyncio.run(worker(Path(sys.argv[2]), sys.argv[3], bool(int(sys.argv[4]))))))
    else:
        main()
