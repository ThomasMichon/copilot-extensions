"""Owned native TCP runtime accessed only through the public Python SDK."""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from uuid import uuid4

from copilot import CopilotClient, RuntimeConnection


async def main():
    executable, workspace, logs, identity_path, pinned_path, output_path = sys.argv[1:]
    connection_token = uuid4().hex + uuid4().hex
    process = subprocess.Popen(
        [
            executable, "--headless", "--no-auto-update", "--no-auto-login",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color",
            "--no-ask-user", "--log-dir", logs,
        ],
        cwd=workspace, env={**os.environ, "COPILOT_CONNECTION_TOKEN": connection_token},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    identity = Path(identity_path)
    temporary = identity.with_suffix(".pending")
    temporary.write_text(json.dumps({"pid": process.pid}), encoding="utf-8")
    temporary.replace(identity)
    errors = []
    stderr_reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    stderr_reader.start()
    stdout_reader = None
    client = session = None
    try:
        deadline = time.monotonic() + 15
        while not Path(pinned_path).is_file():
            assert time.monotonic() < deadline, "Native runtime identity was not pinned"
            await asyncio.sleep(.025)
        while True:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15)
            assert line, b"".join(errors).decode("utf-8", "replace")
            match = re.search(rb"listening on port (\d+)", line, re.IGNORECASE)
            if match:
                port = int(match.group(1))
                break
        stdout_reader = threading.Thread(target=process.stdout.read, daemon=True)
        stdout_reader.start()
        client = CopilotClient(
            connection=RuntimeConnection.for_uri(
                f"127.0.0.1:{port}", connection_token=connection_token,
            ),
            working_directory=workspace, env=os.environ.copy(), use_logged_in_user=False,
        )
        await client.start()
        status = await client.get_status()
        session = await client.create_session(
            model="gpt-4.1", working_directory=workspace, skip_custom_instructions=True,
            enable_config_discovery=False, enable_file_hooks=False, enable_skills=False,
            mcp_servers={}, request_extensions=False, streaming=True,
            provider={
                "type": "openai", "base_url": os.environ["COPILOT_PROVIDER_BASE_URL"],
                "wire_api": "completions",
            },
        )
        before_tasks = await session.rpc.tasks.list()
        before_schedules = await session.rpc.schedule.list()
        response = await session.send_and_wait("ISOLATED_HEADLESS_SDK_INVENTORY_FIXTURE", timeout=30)
        after_tasks = await session.rpc.tasks.list()
        after_schedules = await session.rpc.schedule.list()
        result = {
            "sessionId": session.session_id, "status": status.to_dict(),
            "beforeTasks": before_tasks.to_dict(), "beforeSchedules": before_schedules.to_dict(),
            "afterTasks": after_tasks.to_dict(), "afterSchedules": after_schedules.to_dict(),
            "responseType": response.type.value if response else None,
            "responseText": getattr(response.data, "content", None) if response else None,
        }
    finally:
        try:
            if session is not None:
                await asyncio.wait_for(session.disconnect(), 5)
            if client is not None:
                await asyncio.wait_for(client.stop(), 5)
        finally:
            if process.poll() is None:
                process.terminate()
            await asyncio.to_thread(process.wait, 10)
            stderr_reader.join(5)
            assert not stderr_reader.is_alive(), "Native stderr remained open after retirement"
            if stdout_reader is not None:
                stdout_reader.join(5)
                assert not stdout_reader.is_alive(), "Native stdout remained open after retirement"
    Path(output_path).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
