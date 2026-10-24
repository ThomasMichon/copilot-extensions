"""Owned subprocess fixture for cold recovery races and precise crash windows."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from agent_bridge import cold_outages, session_cold_drain
from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.session_manager import SessionManager
from test_cold_drain import api, bind_peer, live_session, messages, until


async def wait_file(path: Path) -> None:
    await until(path.exists)


async def main() -> None:
    database, shared, control, mode = (
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        sys.argv[4],
    )
    bindings = SimpleNamespace(setattr=setattr)
    db = Database(database)
    manager = SessionManager(
        db,
        read_only_rehydrate=not mode.startswith("drain-") and mode != "startup-predecessor",
        session_host_unexpected_reap_seconds=0,
    )
    bind_peer(shared, bindings, reject_reconnect=mode == "provider-refusal")
    if mode == "startup-predecessor":
        import httpx

        session, transcript = await live_session(manager, shared, bindings)
        await manager.submit_prompt(session.session_id, "original interrupted fixture work")
        await until(lambda: any(m.get("method") == "session/prompt" for m in messages(transcript)))
        (control / "ready").touch()
        await wait_file(control / "request-drain")
        request = json.loads((control / "request-drain").read_text())
        transport_api = api(manager)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport_api.app),
            base_url="http://fixture",
            headers=dict(transport_api.headers),
        ) as wire:
            response = await wire.post(
                "/api/v1/drain",
                json={
                    **request,
                    "drain_contract": {"minimum": 2, "maximum": 2},
                },
            )
        assert response.status_code == 200, response.text
        (control / "drain-result.json").write_text(response.text)
        await wait_file(control / "request-shutdown")
        (control / "shutdown-accepted").touch()
        await wait_file(control / "allow-exit")
        db.close()
        os._exit(0)
    elif mode.startswith("drain-"):
        session, transcript = await live_session(manager, shared, bindings)
        await manager.submit_prompt(session.session_id, "original interrupted fixture work")
        await until(lambda: any(m.get("method") == "session/prompt" for m in messages(transcript)))

        async def settle(*_args):
            if mode == "drain-crash":
                os._exit(24)
            raise RuntimeError("fixture settlement failure after confirmed exits")

        session_cold_drain._settle = settle
        result = await session_cold_drain.cold_drain(
            manager,
            timeout=10,
            poll=0.05,
            source="fixture",
            reason="update",
            exclude_session_id=None,
        )
    else:

        def no_rebuild(*_args, **_kwargs):
            raise AssertionError("cold resync must never rebuild a transcript")

        EventLog.rebuild = no_rebuild
        original = manager._connect_via_session_host

        async def connect(*args, **kwargs):
            (control / "connect-entered").write_text("entered")
            if mode == "blocked-launch":
                await wait_file(control / "continue")
            if mode == "crash-before-connect":
                os._exit(26)
            connected = await original(*args, **kwargs)
            if mode == "crash-registered":
                os._exit(23)
            return connected

        manager._connect_via_session_host = connect
        if mode == "crash-claim":

            def crash_claim(*_args, **_kwargs):
                os._exit(27)

            manager._host_index.claim = crash_claim
        if mode in {"crash-admitted", "crash-false-confirmed"}:

            async def never_send(*_args):
                await asyncio.Event().wait()

            manager._run_prompt = never_send
        if mode == "crash-provider-proof":
            run_prompt = manager._run_prompt
            record_result = cold_outages.provider_result

            async def after_admission(*args):
                await until(
                    lambda: (
                        cold_outages.latest(db, "cold-session")["state"] == "reconnect_admitted"
                    )
                )
                await run_prompt(*args)

            def lose_provider_evidence(manager, session, index, result):
                if result is not None:
                    os._exit(28)
                record_result(manager, session, index, result)

            manager._run_prompt = after_admission
            cold_outages.provider_result = lose_provider_evidence
        (control / "ready").write_text("ready")
        await wait_file(control / "start")
        if mode == "resync":
            try:
                result = {"events": await manager.resync_session("cold-session")}
            except (ValueError, RuntimeError) as error:
                result = {"refused": str(error)}
        else:
            result = {
                "reattached": await manager.reattach_session_hosts(remote_recovery_timeout=2)
            }
        if mode == "crash-admitted":
            os._exit(25)
        if mode == "crash-false-confirmed":
            # Reproduce the old bug's durable state without inventing a send.
            db.execute_write(
                "UPDATE cold_outages SET state='reconnected' WHERE session_id='cold-session'"
            )
            os._exit(29)
        session = manager.get_session("cold-session")
        if session is not None and session._prompt_task is not None:
            await asyncio.wait_for(asyncio.shield(session._prompt_task), timeout=5)
        if mode == "provider-refusal":
            try:
                await manager.resync_session("cold-session")
            except ValueError as error:
                result["resync_refused"] = str(error)
    row = cold_outages.latest(db, "cold-session")
    (control / "result.json").write_text(
        json.dumps(
            {
                "result": result,
                "outage": row,
                "pid": os.getpid(),
                "at": time.time(),
            }
        )
    )
    if mode == "exit-confirmed":
        db.close()
        os._exit(0)
    await wait_file(control / "done")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
