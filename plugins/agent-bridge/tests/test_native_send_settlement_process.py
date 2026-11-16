"""Private-journal process races; not an actor-free required-unit admission."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any

import pytest

from agent_bridge.native_send_settlement import settle, settle_record
from agent_bridge.db import Database
from test_native_keyed_admission import keyed as keyed
from test_native_send_settlement import _seed

OLDER_ADMISSION = """
import json, sqlite3, sys
db, session, prompt, delivery, native, key, caller, ordering = sys.argv[1:]
conn = sqlite3.connect(db, timeout=10)
receipt = dict(version=1, session_id=session, native_session_id=native,
               invocation_key=key, turn_index=1, queued=False, admission='accepted')
facts = dict(version=1, operation='send', source='agent-bridge',
             native_session_id=native, caller_id=caller)
if ordering == 'settlement-first':
    print('ready', flush=True)
    sys.stdin.readline()
conn.execute('BEGIN IMMEDIATE')
try:
    conn.execute(
        'INSERT INTO turns (session_id,turn_index,prompt,acquisition_delivery_id,'
        'native_admission_json) VALUES (?,1,?,?,?)',
        (session, prompt, delivery, json.dumps(dict(facts=facts, receipt=receipt))))
except sqlite3.IntegrityError as exc:
    assert str(exc) == 'native_send_settled'
    conn.rollback()
    print('excluded', flush=True)
else:
    print('admitted', flush=True)
    sys.stdin.readline()
    conn.commit()
conn.close()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("ordering", ["admission-first", "settlement-first"])
async def test_independent_older_writer_and_settlement_linearize(
    keyed: tuple[Any, Any, Any], tmp_db: Database, ordering: str,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    process = subprocess.Popen(
        [
            sys.executable, "-c", OLDER_ADMISSION, str(tmp_db.db_path), session.session_id,
            request.prompt, request.delivery_id(), request.expected_native_session_id,
            request.invocation_key, request.caller_id, ordering,
        ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pending = None
    try:
        assert process.stdout is not None
        first = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15)
        assert first.strip() == ("admitted" if ordering == "admission-first" else "ready")
        if ordering == "admission-first":
            pending = asyncio.create_task(asyncio.to_thread(
                settle_record, tmp_db, session.session_id, request,
            ))
            await asyncio.sleep(0)
        else:
            negative = await settle(manager, session.session_id, request)
            assert negative["admission"] == "not_admitted"
        output, error = await asyncio.to_thread(process.communicate, "\n", timeout=15)
        assert process.returncode == 0, error
        if ordering == "admission-first":
            assert pending is not None
            positive = await asyncio.wait_for(pending, 15)
            assert positive["admission"] == "accepted"
            assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
            assert len(tmp_db.get_turns(session.session_id)) == 2
        else:
            assert output.strip() == "excluded"
            assert await settle(manager, session.session_id, request) == negative
            assert len(tmp_db.get_turns(session.session_id)) == 1
        manager._run_prompt.assert_not_awaited()
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.communicate, timeout=15)
        if pending is not None:
            await asyncio.wait_for(pending, 15)
