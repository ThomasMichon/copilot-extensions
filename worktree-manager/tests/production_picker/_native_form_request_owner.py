"""Disposable real dispatch owner/CLI for the opt-in Manager boundary test."""

import json
from pathlib import Path
import socket
import subprocess
import sys


def main():
    mode, root = sys.argv[1], Path(sys.argv[2])
    if mode == "invoke":
        url = (root / "url").read_text(encoding="utf-8")
        args = sys.argv[3:]
        result = subprocess.run(
            [sys.executable, "-m", "agent_dispatch", "--url", url, *args],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if args[:2] == ["steer", "submit"] and result.returncode == 0 and not (root / "lost").exists():
            (root / "lost").write_text("committed", encoding="utf-8")
            print("accepted owner response lost before Manager received it", file=sys.stderr)
            return 7
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        return result.returncode

    from agent_dispatch.queue import TaskQueue
    queue = TaskQueue(root / "queue.db")
    if mode == "advance":
        task_id = (root / "task").read_text(encoding="utf-8")
        queue.take_steer(task_id, "host/worker")
        wake = queue.claim_due_wake()
        assert wake is not None
        queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
        queue.set_card(task_id, "host/worker", card={"title": "A replacement question"})
        return 0
    if mode == "snapshot":
        task_id = (root / "task").read_text(encoding="utf-8")
        print(json.dumps({
            "answers": queue.steer_log(task_id),
            "wake_messages": [wake.message for wake in queue.list_wakes(task_id)],
        }))
        return 0

    import uvicorn
    from agent_dispatch.coordinator import create_app
    task = queue.create("A native question", repo="example.com/acme/widget")
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker", owner_session_id="session-one")
    queue.set_card(task.id, "host/worker", card={
        "title": "Original question", "request_input": [{"name": "answer", "type": "textarea"}],
    })
    (root / "task").write_text(task.id, encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        (root / "url").write_text(url, encoding="utf-8")
        server = uvicorn.Server(uvicorn.Config(create_app(queue), log_level="error", lifespan="off"))
        server.run(sockets=[listener])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
