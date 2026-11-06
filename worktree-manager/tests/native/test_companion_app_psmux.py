"""Actual PSMux/ConPTY input and unmodified app; synthetic owner, not rollout."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import psutil
import pytest

from worktree_manager import companion_pane, mux_mapping_registry
from _companion_client_fixture import fixture_engine
from _manager_process_fixture import synthetic_runtime
from _worktree_timeline_fixtures import delta


pytestmark = [
    pytest.mark.skipif(
        os.name != "nt" or not (os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux"))
        or not shutil.which("pwsh"),
        reason="Native Windows PSMux, PowerShell and ConPTY are required",
    ),
    pytest.mark.timeout(120),
]


def test_actual_companion_psmux_client_history_resize_and_same_cli(
    tmp_path, monkeypatch, record_property,
):
    from _terminal_console_fixture import Console

    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    session = "companion-client-" + uuid.uuid4().hex
    profile, checkout, owner_root = [tmp_path / part for part in ("profile", "checkout", "owner")]
    profile.mkdir()
    checkout.mkdir()
    for key in list(os.environ):
        if key.startswith("PSMUX_") or key in {"TMUX", "TMUX_PANE", "WT_SESSION"}:
            monkeypatch.delenv(key)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(profile))
    monkeypatch.setenv("PSMUX_NO_WARM", "1")
    interpreter, calls = fixture_engine(owner_root, checkout)
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_ARGV", json.dumps([
        str(interpreter), "-m", "agent_worktrees",
    ]))
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    root = tmp_path / "manager"
    slot = synthetic_runtime(root, "1.2.3")
    package = slot / "Lib/site-packages/worktree_manager"
    assert (package / "mux_companion.py").read_bytes() == Path(companion_pane.__file__).with_name(
        "mux_companion.py",
    ).read_bytes()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(companion_pane.ec, "current_worktree_status", lambda **kw: {
        "repo": "example", "id": "selected", "path": str(checkout),
    })
    config = tmp_path / "psmux.conf"
    config.write_text("", encoding="utf-8")
    inert = tmp_path / "inert_cli.py"
    cli_receipt = tmp_path / "inert-started.json"
    inert.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        f"Path({str(cli_receipt)!r}).write_text(json.dumps({{'pid':os.getpid(),'argv':sys.argv}}))\n"
        "print('INERT SOURCE CLI',flush=True)\n"
        "for line in sys.stdin: print('CLI RECEIVED '+line.strip(),flush=True)\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)

    def mux(*args, check=True):
        arguments = list(args)
        if "-t" in arguments:
            index = arguments.index("-t") + 1
            if arguments[index].startswith("%"):
                arguments[index] = companion_pane._psmux_pane_target(
                    binary, session, arguments[index], incarnation,
                )
        result = subprocess.run(
            [binary, *arguments], capture_output=True, encoding="utf-8", timeout=5,
            env=dict(environment, PSMUX_SESSION=session),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.rstrip("\r\n") if check else result

    argv = [
        binary, "-f", str(config), "new-session", "-s", session, "-c", str(checkout),
        "--", sys.executable, "-I", "-u", str(inert),
    ]
    pinned = {}
    with Console(argv, env=environment, cwd=checkout) as client:
        def diagnostic():
            return client.output.decode("utf-8", "replace")

        def wait_for(condition, describe=diagnostic):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert not client.exited(client.process.process), diagnostic()
                assert client.read_error is None, client.read_error
                if condition() and time.monotonic() < deadline:
                    return
                time.sleep(.025)
            pytest.fail(describe())

        def pin_owned(pid):
            process = psutil.Process(pid)
            parents = process.parents()
            parent_ids = [parent.pid for parent in parents]
            assert client.process.pid in parent_ids, (
                "Native fixture escaped its caller-owned process tree", pid,
            )
            for owned in [process, *parents[:parent_ids.index(client.process.pid)]]:
                if owned.pid not in pinned:
                    pinned[owned.pid] = client.pin(owned.pid)
            return pinned[pid]

        try:
            wait_for(lambda: mux("has-session", "-t", session, check=False).returncode == 0)
            cli, incarnation, pid, window = mux(
                "display-message", "-p", "-t", session,
                "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_pid}\t#{window_id}",
            ).split("\t")
            cli_handle = pin_owned(int(pid))
            cli_birth = client.birth(cli_handle)

            def cli_diagnostic():
                process = psutil.Process(int(pid))
                return json.dumps({
                    "client": diagnostic(),
                    "pane": mux("capture-pane", "-p", "-t", cli, check=False).stdout,
                    "command": process.cmdline(),
                    "children": [
                        {"pid": child.pid, "command": child.cmdline()}
                        for child in process.children(recursive=True)
                    ],
                    "started": cli_receipt.read_text() if cli_receipt.exists() else None,
                    "native_process": client.process_state(int(pid)),
                })

            wait_for(lambda: "INERT SOURCE CLI" in mux("capture-pane", "-p", "-t", cli), cli_diagnostic)
            mux_mapping_registry.register_mapping({
                "project": "example", "worktree_id": "selected", "mux_bin": binary,
                "mux_session": session, "worktree_path": str(checkout),
                "session_incarnation": incarnation,
                "panes": [{"pane_id": cli, "role": "cli", "live": True}],
                "mapping_revision": 1, "live": True,
            }, root=root)
            observer = companion_pane.switch("show", str(checkout))

            def screen():
                return mux("capture-pane", "-p", "-t", observer)

            wait_for(lambda: "Purpose" in screen() and "A live Copilot session" in screen(), screen)
            assert mux("display-message", "-p", "-t", session, "#{pane_id}") == observer
            observer_pid = int(mux("display-message", "-p", "-t", observer, "#{pane_pid}"))
            observer_handle = pin_owned(observer_pid)
            assert client.output, "Actual client received no terminal output"
            client.send(b"\x1b[C")
            wait_for(lambda: "Observed change 40" in screen(), screen)
            wait_for(lambda: (owner_root / "feed-pid").exists(), screen)
            feed_handle = pin_owned(int((owner_root / "feed-pid").read_text()))
            client.send(b"\t\t")
            client.send(b"\x1b[H")
            wait_for(lambda: calls.exists() and any(
                "--before-cursor" in json.loads(line) for line in calls.read_text().splitlines()
            ), screen)
            client.send(b"\x1b[F")
            wait_for(lambda: "Observed change 40" in screen(), screen)
            client.send(b"\x1b[H")
            wait_for(lambda: re.search(r"Observed change 1(?!\d)", screen()), screen)
            pending = owner_root / "push.pending"
            pending.write_text(json.dumps(delta()), encoding="utf-8")
            pending.replace(owner_root / "push.json")
            wait_for(lambda: "Updated purpose from pushed owner facts." in screen(), screen)
            assert "Observed change 41" not in screen()
            assert "1 unread" in screen()
            record_property("psmux-browsing-screen", screen())
            client.resize(92, 38)
            wait_for(lambda: mux("display-message", "-p", "-t", session, "#{window_width}") == "92", screen)
            wait_for(lambda: "Updated purpose from pushed owner facts." in screen(), screen)
            record_property("psmux-resized-screen", screen())
            client.send(b"\x1b[F")
            wait_for(lambda: "Observed change 41" in screen(), screen)
            assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
            assert client.birth(cli_handle) == cli_birth and not client.exited(cli_handle)
            assert mux("display-message", "-p", "-t", observer, "#{window_id}") == window
            assert len(mux("list-windows", "-t", session, "-F", "#{window_id}").splitlines()) == 1
            client.send(b"\x1b")
            wait_for(lambda: mux("display-message", "-p", "-t", session, "#{pane_id}") == cli)
            wait_for(lambda: client.exited(feed_handle) and client.exited(observer_handle))
            wait_for(lambda: mux("list-panes", "-t", session, "-F", "#{pane_id}") == cli)
            client.send(b"native-return\r")
            wait_for(lambda: "CLI RECEIVED native-return" in mux("capture-pane", "-p", "-t", cli))
            assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
            assert mux("display-message", "-p", "-t", cli, "#{session_id}:#{session_created}") == incarnation
            record_property("psmux-client-boundary", json.dumps({
                "version": mux("-V"), "client_bytes": len(client.output),
                "app": "unmodified mux_companion.py", "owner": "synthetic wire-valid fixture",
                "cli": "inert source fixture", "input": "actual ConPTY client",
                "display": "server capture, not headed pixels or frame latency",
            }))
        finally:
            try:
                mux("kill-session", "-t", session, check=False)
            finally:
                client.job.close()
                client.job = None
                assert all(client.exited(handle, 5000) for handle in pinned.values()), (
                    "Owned native process survived strict Job cleanup",
                )
    assert client.handles == []
