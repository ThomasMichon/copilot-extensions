"""The actual CLI attests its physical cell before touching owner discovery."""

import json
import os
import subprocess
import time
from contextlib import ExitStack
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs

from agent_pull_requests import __main__ as cli
from agent_pull_requests.watch_control import WatchControlClient
from agent_pull_requests.watch_custody import launch_path
from agent_pull_requests.watch_journal import WatchJournal
from test_watch_driver import _begin, _driver, _port
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture
from test_watch_journal import _manifest
from test_watch_runtime import _advance_installation


_ENSURE_KNOWN = """
import json, sys
from pathlib import Path
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import WatchInvocation, capture_running
from agent_pull_requests import watch_entry
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
invocation = WatchInvocation(
    installation, capture_running(installation, environment={}), str(installation.receipt),
)
def forbidden_spawn(*args, **kwargs):
    raise AssertionError("known incumbent must not spawn a replacement")
watch_entry.spawn_daemon = forbidden_spawn
assert watch_entry.ensure(invocation) is True
print(json.dumps(watch_entry.dial(invocation)), flush=True)
"""


@pytest.mark.parametrize("native", [False, True])
def test_governed_request_budget_covers_only_native_registration_preflight(monkeypatch, native):
    from agent_pull_requests import watch_entry

    seen = []
    monkeypatch.setattr(watch_entry, "dial", lambda _: ("127.0.0.1", 1, "test-token"))

    def request(*args, **kwargs):
        seen.append(kwargs)
        return {"registered": True}

    monkeypatch.setattr(watch_entry, "request", request)
    payload = {"repo": "project/repo", "number": 7}
    if native:
        payload["worktrees_command"] = "attributable-command"
    assert watch_entry.call(object(), "register", payload, boot=False)["registered"]
    assert seen[0]["request_deadline_s"] == (27.0 if native else 5.0)
    assert seen[0]["completion_timeout_s"] == (27.0 if native else 5.0)


@pytest.mark.parametrize("was_running", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
def test_governed_restart_preserves_existing_response_contract(
    monkeypatch, capsys, was_running, json_output,
):
    from agent_pull_requests import watch_entry

    invocation = object()
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", invocation)

    def ensure(actual, *, restart):
        assert actual is invocation
        assert restart is True
        return was_running

    monkeypatch.setattr(watch_entry, "ensure", ensure)
    assert cli._cmd_serve_restart(SimpleNamespace(json=json_output)) == 0
    output = capsys.readouterr().out
    if json_output:
        assert json.loads(output) == {"restarted": True, "was_running": was_running}
    else:
        assert output == "agent-pull-requests: watch daemon restarted\n"


@pytest.mark.parametrize("error", [PermissionError("owner refused"), RuntimeError("rolled back")])
def test_governed_restart_failure_never_emits_a_success_response(monkeypatch, capsys, error):
    from agent_pull_requests import watch_entry

    monkeypatch.setattr(cli, "_WATCH_INVOCATION", object())

    def ensure(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(watch_entry, "ensure", ensure)
    with pytest.raises(type(error), match=str(error)):
        cli._cmd_serve_restart(SimpleNamespace(json=True))
    assert capsys.readouterr().out == ""


@pytest.mark.timeout(180)
@pytest.mark.parametrize("state", ["archived-terminal", "unarchived-terminal", "pending"])
def test_ensure_receipt_advance_retains_only_archived_real_incumbent(installed, tmp_path, state):
    installation, _, python, _ = installed
    with ExitStack() as children:
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard)
            driver = _driver(installed, journal, children)
            assert driver.run().ok
            archive = installation.run_root / "watch-owner-history" / f"{journal.transaction_id}.json"
            if state != "archived-terminal":
                archive.unlink()
            if state == "pending":
                from test_watch_installation import _mutate

                _mutate(installation.run_root / "watch-owner-transaction.json", lambda document: (
                    document.update(phase="committed")
                ))
            before = (installation.run_root / "watch-owner-transaction.json").read_bytes()
            fresh = _advance_installation(installation, tmp_path)
        result = subprocess.run(
            [str(python), "-I", "-c", _ENSURE_KNOWN, *(
                str(getattr(fresh, key)) for key in (
                    "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
                )
            )],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
            **no_window_kwargs(),
        )
        if state == "archived-terminal":
            assert result.returncode == 0, result.stderr
            assert tuple(json.loads(result.stdout)) == driver.successor._endpoint
            assert archive.read_bytes() == before
        else:
            assert result.returncode != 0
            assert "reconciliation and archival" in result.stderr
        assert (installation.run_root / "watch-owner-transaction.json").read_bytes() == before


def test_main_captures_context_before_handler_or_discovery(monkeypatch):
    from agent_pull_requests.watch_invocation import WatchInvocation
    admissions = []
    sentinel = object()

    def capture(*, require_selected=False):
        assert require_selected
        admissions.append("selected")
        return sentinel

    monkeypatch.setattr(WatchInvocation, "capture", capture)
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)

    def status(args):
        assert cli._WATCH_INVOCATION is sentinel
        assert admissions == ["selected"]
        return 0

    monkeypatch.setattr(cli, "_cmd_serve_status", status)
    assert cli.main(["watch", "health", "--json"]) == 0


def test_invalid_context_never_reaches_even_read_only_discovery(monkeypatch):
    from agent_pull_requests.watch_invocation import WatchInvocation

    monkeypatch.setattr(WatchInvocation, "capture", lambda **_kwargs: (
        (_ for _ in ()).throw(PermissionError("fixture physical context refused"))
    ))
    monkeypatch.setattr(cli, "_cmd_serve_status", lambda _: pytest.fail("discovery ran"))
    with pytest.raises(PermissionError, match="physical context"):
        cli.main(["watch", "health"])


def test_legacy_cannot_enter_reserved_passive_mode(monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    args = cli.build_parser().parse_args([
        "serve", "--passive-instance", str(uuid4()), "--port", "12345",
    ])
    with pytest.raises(PermissionError, match="explicit installation"):
        args.handler(args)


@pytest.mark.timeout(180)
@pytest.mark.skipif(os.name != "nt", reason="Windows physical-profile entry; POSIX needs isolated account")
def test_real_module_entry_publishes_only_reserved_private_passive_custody(installed):
    installation, target, python, _ = installed
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = WatchJournal.begin(
            installation, guard, target=target, target_manifest=_manifest(target), environment={},
        )
        instance_id = str(uuid4())
        journal.reserve_successor(instance_id)
        port = _port()
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "COPILOT_EXTENSIONS_CONTEXT"}
        }
        environment.update(
            COPILOT_EXTENSIONS_CONTEXT=str(installation.receipt),
            AGENT_RT_ROOT=str(installation.plugin_root),
            COPILOT_PLUGIN_ROOT=str(installation.payload_root),
            USERPROFILE=str(installation.os_profile),
        )
        child = subprocess.Popen(
            [
                str(python), "-I", "-m", "agent_pull_requests", "serve",
                "--passive-instance", instance_id, "--port", str(port),
            ],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
        )
        client = None
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    if not launch_path(installation, journal._document).exists():
                        raise FileNotFoundError("fixture launch receipt is not present")
                    instance, endpoint = journal.launched_successor()
                    break
                except FileNotFoundError:
                    if child.poll() is not None:
                        raise AssertionError(child.communicate(timeout=5))
                    if time.monotonic() >= deadline:
                        raise TimeoutError("fixture passive launch receipt unavailable")
                    time.sleep(0.05)
            client = WatchControlClient(
                endpoint=endpoint, instance=instance, installation=lambda: installation,
                transaction=lambda: (journal.transaction_id, journal.authorization),
                request_timeout=30,
            )
            health = client.health()
            assert health["instance"]["instanceId"] == instance_id
            assert not health["writerHeld"] and not health["effectsEnabled"]
            assert not installation.state_root.exists()
            assert not (installation.run_root / "active.json").exists()
        finally:
            if client is not None:
                journal.bind_cleanup_successor(
                    client.instance, endpoint=client._endpoint, read_health=client.cleanup_health,
                )
                reference = client.capture_cleanup_exit()
                try:
                    client.cleanup_stop()
                    journal.record_cleanup_exit(reference, timeout=10)
                finally:
                    reference.close()
                journal.rollback()
                journal.finish()
                journal.archive()
            if child.poll() is not None or client is not None:
                child.communicate(timeout=15)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
