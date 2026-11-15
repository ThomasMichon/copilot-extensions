"""Actual exact live-CLI parser, admission, receipt and rollover observations."""

from __future__ import annotations

import io
import json
from pathlib import Path
import re
import subprocess
import time
from unittest.mock import Mock

from agent_procutil import no_window_kwargs
from agent_bridge import __main__ as core
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.db import Database
from agent_bridge.db_schema import _SchemaMixin
from test_live_exact_rollback import prior_owners


def observe_replay_controls() -> dict:
    """Exercise the actual extension's model-free rejected replay controls."""
    result = subprocess.run(
        ["node", "--test", "--test-reporter=tap",
         "--test-name-pattern=replay retains|rejected legacy replay|consumed.*replay|ACK failure",
         str(Path(__file__).with_name("delivery.test.mjs")),
         str(Path(__file__).with_name("plugin-readiness.test.mjs"))],
        capture_output=True, text=True, timeout=25, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    passed = re.search(r"^# pass (\d+)$", result.stdout, re.MULTILINE)
    failed = re.search(r"^# fail (\d+)$", result.stdout, re.MULTILINE)
    assert passed is not None and failed is not None, result.stdout
    return {"passed": int(passed[1]), "failed": int(failed[1])}


def observe(api, db, vector, monkeypatch, capsys, *, client_factory) -> dict:
    body = vector["requests"]["live_cli_request"]
    sid = body["expected_session_id"]
    root = f"/api/v1/live-sessions/{sid}/messages/"
    caps = api.get("/health").json()["capabilities"]
    observed = {"capabilities": {key: caps[key] for key in (
        "live_keyed_receipt", "live_exact_notify",
    )}}
    posts = []
    masked = [None]

    def transport(method, path, payload=None, **kwargs):
        if method == "POST":
            posts.append(path)
        response = api.request(method, path, json=payload)
        if response.is_error:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        result = response.json()
        if path == "/health" and masked[0] is not None:
            result["capabilities"].pop(masked[0], None)
        return result

    def configured_client(_cls):
        client = BridgeClient("http://testserver", "fixture-token", connect_grace=0)
        client._request = transport
        return client

    def command(mode):
        argv = list(vector["requests"]["live_cli_commands"][mode])
        with monkeypatch.context() as patch:
            factory = Mock(wraps=client_factory)
            ensure = Mock(side_effect=AssertionError("exact live command ensured a daemon"))
            start = Mock(side_effect=AssertionError("exact live command started a daemon"))
            patch.setattr(core, "_get_client", factory)
            patch.setattr(core, "_ensure_daemon", ensure)
            patch.setattr(core, "_launch_bridge_daemon", start)
            patch.setattr(BridgeClient, "from_config", classmethod(configured_client))
            patch.setattr("sys.stdin", io.StringIO(body["body"]))
            capsys.readouterr()
            try:
                core.main(argv)
                status = 0
            except SystemExit as exc:
                status = exc.code
            output = capsys.readouterr()
            result = json.loads(output.out) if output.out.strip() else None
            assert factory.call_args.kwargs == {"ensure": False}
            ensure.assert_not_called()
            start.assert_not_called()
        return status, result

    now = time.time()
    db.register_live_session(
        sid, machine="fixture-host", cwd="/fixture", worktree_id="fixture-live-worktree",
        repo="fixture", branch="dev", pid=42, role=None, process_started_at=now - 60, now=now,
    )
    db.execute_write("UPDATE live_sessions SET cli_mode=1 WHERE session_id=?", (sid,))
    status, prompt = command("prompt")
    assert status == 0, prompt
    observed["prompt"] = {"exit": status, "stage": prompt["receipt"]["stage"]}
    prompt_id = prompt["receipt"]["message_id"]
    before = [dict(row) for row in db.execute_read("SELECT * FROM live_messages")]
    status, receipt = command("receipt")
    observed["receipt"] = {
        "exit": status, "same_original": receipt["receipt"] == prompt["receipt"],
        "readonly": before == [dict(row) for row in db.execute_read("SELECT * FROM live_messages")],
    }
    status, notify = command("notify")
    assert status == 0, notify
    notify_id = notify["admission"]["message_id"]
    row = db.execute_read("SELECT * FROM live_messages WHERE id=?", (notify_id,))[0]
    observed["notify"] = {"exit": status, "kind": row["kind"], "delivery": row["delivery"]}
    original = [dict(row) for row in db.execute_read("SELECT * FROM live_messages")]
    exact_poll = api.get(root + "?exact_native=true").json()["messages"]
    observed["poll_binding"] = {
        "old_reader_hidden": api.get(root).json()["messages"] == [],
        "original_native": len(exact_poll) == 2 and all(
            item["exact_session"] is True and item["original_session_id"] == sid
            for item in exact_poll
        ),
    }
    prior = prior_owners(monkeypatch)
    import sys
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "agent_bridge.inbox_custody", prior["inbox_custody"])
        patch.setattr(_SchemaMixin, "_init_schema", prior["db_schema"]._SchemaMixin._init_schema)
        try:
            rolled_back = Database(db.db_path)
        except ValueError as exc:
            refused = str(exc) in {
                "Inbox custody requires writable preparation: " + guard
                for guard in (
                    "live_messages_runtime_admission", "live_messages_handoff_input_immutable",
                )
            }
            assert refused, str(exc)
        else:
            rolled_back.close()
            refused = False
    observed["cold_rollback"] = {
        "refused": refused,
        "unchanged": original == [dict(row) for row in db.execute_read("SELECT * FROM live_messages")],
        "no_fabricated_custody": all(
            row["runtime_custody_json"] is None and row["handoff_json"] is None
            for row in original
        ),
    }
    observed["replay_rejections"] = observe_replay_controls()
    for mode, capability in (("prompt", "live_keyed_receipt"), ("notify", "live_exact_notify")):
        masked[0] = capability
        posts.clear()
        status, result = command(mode)
        observed["old_" + mode] = {
            "exit": status, "outcome": result["outcome"], "posts": list(posts),
        }
    masked[0] = None
    db.register_live_session(
        "fixture-live-successor", machine="fixture-host", cwd="/fixture",
        worktree_id="fixture-live-worktree", repo="fixture", branch="dev", pid=42,
        role=None, process_started_at=now - 60, now=now + 1,
    )
    rows = db.execute_read("SELECT * FROM live_messages")
    observed["rollover"] = {
        "original_rows": all(row["session_id"] == sid and row["exact_session"] == 1 for row in rows),
        "successor_pending": api.get(
            "/api/v1/live-sessions/fixture-live-successor/messages",
        ).json()["messages"],
        "successor_ack": db.ack_live_messages("fixture-live-successor", [prompt_id, notify_id], now + 2),
        "original_receipt": api.post(root + "keyed-receipt/v1", json=body).json()["receipt"]["message_id"] == prompt_id,
        "post_probe_notify": api.post(
            root + "exact-notify/v1", json={**body, "idempotency_key": "after-rollover"},
        ).status_code,
    }
    observed["no_hosted_queue"] = not db.execute_read("SELECT * FROM pending_prompts")
    observed["no_ensure"] = True
    return observed
