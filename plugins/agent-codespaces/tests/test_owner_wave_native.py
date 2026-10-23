"""Explicit local consumer lane: real owned children, no GitHub or SSH access."""
from __future__ import annotations

import asyncio
import importlib.util
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_codespaces import account_candidates, gh_account, lifecycle
from agent_codespaces import connection_owner as owner
from agent_codespaces import owner_inventory as inv
from agent_codespaces import owner_mux_probe as mux
from agent_codespaces import session_forwards as sf


BASELINE = "35872e7ad67e1c0ef182a1f4401a49af69c4afb6"
REPO = Path(__file__).resolve().parents[3]
FAKE = r'''
import json, os, pathlib, sys
op, arg, count, root = sys.argv[1:]
root = pathlib.Path(root)
(root / (str(os.getpid()) + "-" + op + ".json")).write_text(json.dumps([op, arg]))
count = int(count)
if op == "list":
    print(json.dumps([{"name": "cs-" + str(i), "state": "Available"}
                     for i in range(count) if i % 3 == int(arg)]))
elif op == "batch":
    for i in range(4):
        print("ACMUX1\t" + str(i) + "\tpresent")
    print("ACMUX1\tDONE\t4")
elif op != "legacy":
    raise SystemExit(2)
'''


@pytest.fixture
def local_consumer(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    # This lane measures executable boundaries, not registry disk throughput.
    # Keep hold/prune/heartbeat logic, with identical synthetic storage for both
    # versions. Real file-lock replacement is covered by test_owner_wave.py.
    holds = {}
    lock = threading.Lock()

    def write_holds(value):
        holds.clear()
        holds.update(copy.deepcopy(value))

    monkeypatch.setattr(owner, "_owner_lock", lambda: lock)
    monkeypatch.setattr(owner, "_read_holds", lambda: copy.deepcopy(holds))
    monkeypatch.setattr(owner, "_write_holds", write_holds)
    monkeypatch.setattr(lifecycle, "validate_context", lambda: None)
    monkeypatch.setattr(account_candidates, "candidates", lambda **kwargs: ("0", "1", "2"))
    monkeypatch.setattr(gh_account, "env_for_account", lambda account: {"FAKE_ACCOUNT": account})
    monkeypatch.setattr(inv, "credential", lambda account: account)
    script = tmp_path / "fake_provider.py"
    script.write_text(FAKE, encoding="utf-8")
    run = subprocess.run
    state = {"count": 0, "directory": tmp_path, "opens": 0, "closes": 0}

    def execute(op, arg):
        if sys.platform == "win32":
            # A cmd fixture avoids booting a Python runtime for each simulated
            # gh/SSH command. The product boundary and invocation count remain real.
            directory = state["directory"]
            output = directory / f"output-{op}-{arg}.txt"
            if op == "list":
                text = json.dumps([
                    {"name": f"cs-{i}", "state": "Available"}
                    for i in range(state["count"]) if i % 3 == int(arg)
                ])
            elif op == "batch":
                text = "".join(f"ACMUX1\t{i}\tpresent\n" for i in range(4)) + "ACMUX1\tDONE\t4\n"
            else:
                text = ""
            output.write_text(text, encoding="utf-8")
            event = directory / (uuid.uuid4().hex + ".json")
            command = f'echo ["{op}","{arg}"]>"{event}" & type "{output}"'
            return run(
                f'"{os.environ["COMSPEC"]}" /d /s /c "{command}"',
                capture_output=True, text=True, timeout=10, **no_window_kwargs(),
            )
        return run(
            [sys.executable, "-S", str(script), op, arg, str(state["count"]), str(state["directory"])],
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )

    def list_process(args, **kwargs):
        assert args[:3] == ["gh", "codespace", "list"]
        assert args[-2:] == ["--limit", "50"]
        env = kwargs["env"]
        return execute("list", env.get("FAKE_ACCOUNT", env.get("GH_TOKEN")))

    monkeypatch.setattr(lifecycle.subprocess, "run", list_process)

    class Manager:
        async def exec_command(self, name, command, timeout=None):
            op = "batch" if "ACMUX1" in command else "legacy"
            # The injected transport owns a bounded real child, joined even if
            # the owner wave is cancelled.
            result = await inv.joined_thread(execute, op, name)
            return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout)

        async def disconnect(self, name):
            state["closes"] += 1

    async def opener(name):
        state["opens"] += 1
        return Manager()

    return state, opener, run


def load_baseline(run):
    source = run(
        ["git", "-C", str(REPO), "show",
         BASELINE + ":plugins/agent-codespaces/src/agent_codespaces/session_forwards.py"],
        capture_output=True, text=True, check=True, timeout=10, **no_window_kwargs(),
    ).stdout
    name = "agent_codespaces._owner_wave_baseline"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, "<pinned-owner-wave-baseline>", "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("count", [0, 1, 10])
@pytest.mark.parametrize("label", ["baseline", "candidate"])
async def test_identical_baseline_candidate_invocations(local_consumer, tmp_path, count, label):
    state, opener, run = local_consumer
    implementation = load_baseline(run) if label == "baseline" else sf
    directory = tmp_path / label
    directory.mkdir()
    state.update(count=count, directory=directory, opens=0, closes=0)
    for i in range(count):
        for j in range(4):
            owner.hold(f"cs-{i}", f"t-{j}", mux_session=f"mux-{j}", confirmed=True)
    if label == "baseline":
        probe = implementation.make_remote_mux_probe(
            list_codespaces=lifecycle.list_codespaces, open_manager=opener,
        )
    else:
        scope = inv.InventoryScope("fixture", "fixture", ("0", "1", "2"), ())
        probe = mux.RemoteMuxProbe(
            inventory_reader=lambda wave: inv.collect_inventory(wave, scope_reader=lambda: scope),
            scope_reader=lambda: scope, open_manager=opener,
        )
    forwards = implementation.SessionForwards(lambda *a: None, probe, clock=lambda: 1000)
    started = time.monotonic()
    await forwards.probe(owner.list_holds())
    elapsed = time.monotonic() - started
    events = [json.loads(path.read_text()) for path in directory.glob("*.json")]
    before = len(events)
    await forwards.probe(owner.list_holds())
    assert len(list(directory.glob("*.json"))) == before
    receipt = {
        "list": sum(op == "list" for op, _ in events),
        "mux": sum(op in {"batch", "legacy"} for op, _ in events),
        "opens": state["opens"], "closes": state["closes"],
        "elapsed": elapsed, "pages_per_account_listing": 1 if count else 0, "retries": 0,
    }
    await forwards.shutdown()
    assert receipt["list"] == (count * 3 if label == "baseline" else (3 if count else 0))
    assert receipt["mux"] == (count * 4 if label == "baseline" else count)
    assert receipt["opens"] == receipt["closes"] == count
    print(json.dumps({
        "venues": count, "implementation": label, "baseline_sha": BASELINE, "receipt": receipt,
    }, sort_keys=True))


def test_actual_shell_batch_protocol_and_quoting(tmp_path):
    if sys.platform == "win32":
        git = shutil.which("git")
        shell = Path(git).resolve().parent.parent / "bin" / "bash.exe" if git else None
    else:
        shell = shutil.which("bash")
    if not shell or not Path(shell).is_file():
        pytest.skip("Explicit native shell lane requires Bash (Git Bash on Windows)")
    fake = tmp_path / "tmux"
    fake.write_text(
        '#!/bin/sh\ncase "$3" in "=gone") exit 1;; "=error") exit 7;; *) exit 0;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    targets = ["literal'; echo injected; #", "gone", "error"]
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [str(shell), "-c", mux.batch_command(targets)], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert mux.parse_batch(targets, result.stdout, result.returncode) == {
        targets[0]: True, "gone": False, "error": None,
    }
    env["PATH"] = str(tmp_path / "absent")
    result = subprocess.run(
        [str(shell), "-c", mux.batch_command(targets)], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 127
    assert mux.parse_batch(targets, result.stdout, result.returncode) == dict.fromkeys(targets)


async def test_cancel_real_owned_config_child_is_drained(monkeypatch, tmp_path):
    script, done = tmp_path / "config.py", tmp_path / "done"
    script.write_text(
        "import pathlib,sys,time\n"
        "time.sleep(.2)\n"
        "pathlib.Path(sys.argv[1]).write_text('finished')\n"
        "print('Host fixture\\n  HostName fixture')\n",
        encoding="utf-8",
    )
    run = subprocess.run
    entered = threading.Event()
    calls = []

    def config_process(args, **kwargs):
        assert args[:4] == ["gh", "codespace", "ssh", "--config"]
        calls.append(args)
        entered.set()
        return run(
            [sys.executable, "-S", str(script), str(done)],
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )

    factory = mux._admitted_source

    def source(*args):
        value = factory(*args)
        value._config_dir = tmp_path / "configs"
        return value

    monkeypatch.setattr(mux, "_admitted_source", source)
    monkeypatch.setattr(mux.subprocess, "run", config_process)
    monkeypatch.setattr(mux, "credential", lambda account: "fixture")
    monkeypatch.setattr(mux, "_admitted_manager", lambda *a: pytest.fail("cancelled owner connected"))
    scope = inv.InventoryScope("fixture", "fixture", ("a",), (inv._digest("fixture"),))
    now = time.monotonic()
    value = inv.OwnerInventory(1, scope, (
        inv.AccountListing("a", (inv.InventoryRow("cs", "Available", "a"),), now, True),
    ))
    probe = mux.RemoteMuxProbe(scope_reader=lambda: scope)
    task = asyncio.create_task(probe.probe_wave("cs", ["m"], value, 1, lambda: True))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert done.read_text() == "finished" and len(calls) == 1
