"""Manager consumes Driver observations, never a second raw mux backend."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading

import pytest
from agent_procutil import no_window_kwargs
from agent_remote_driver.mux_observation import Observation, Pane

from worktree_manager import mux_attached_clients, mux_daemon, mux_discovery


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"


def _observed(clients=3, incarnation="$7:100"):
    return Observation(
        "present", incarnation=incarnation, clients=clients,
        panes=(Pane("%2", False, True, os.getcwd()),),
    )


def test_native_discovery_creation_and_client_refresh_share_driver_evidence(monkeypatch):
    observed = _observed()
    monkeypatch.setattr(mux_discovery, "observe_psmux", lambda *a, **k: observed)
    monkeypatch.setattr(
        mux_discovery, "run_mux_child", lambda *a, **k: pytest.fail("passive PSMux CLI"),
    )
    payload = {
        "mux_bin": "psmux.exe", "mux_session": "wt-fixture",
        "worktree_path": os.getcwd(), "session_incarnation": "$7:100",
        "panes": [{"pane_id": "%2"}],
    }
    assert mux_discovery.confirm_creation_cli(payload) == {"applied": True}
    assert mux_discovery.confirm_native_session(payload, connected_only=True) == {
        "applied": True, "attached_clients": 3,
    }
    assert mux_attached_clients.mux_attached_clients("psmux.exe", "wt-fixture") == 3
    observed = _observed(incarnation="$8:101")
    assert mux_discovery.confirm_native_session(payload, connected_only=True)["reason"] == "stale-incarnation"
    observed = Observation("unknown", "timeout")
    assert mux_discovery.legacy_session_presence("psmux.exe", "wt-fixture", lambda: None) is None
    assert mux_attached_clients.mux_attached_clients("psmux.exe", "wt-fixture") is None


def test_fresh_observation_does_not_reenable_periodic_native_cli_writes(monkeypatch):
    monkeypatch.setattr(mux_daemon, "run_mux_child", lambda *a, **k: pytest.fail("Periodic PSMux CLI"))
    assert mux_daemon.apply_status_options(
        {"mux_bin": "psmux.exe", "mux_session": "wt-fixture"}, {"@aw_ctx": "fixture"},
    ) is False


def _helpers():
    source = SOURCE.read_text("utf-8")
    helpers = []
    for name in ("Invoke-AwMuxCoreProcess", "Get-AwMuxObservation", "Test-AwMuxCreationSession"):
        start = source.index("function " + name + " {")
        helpers.append(source[start:source.index("\n}\n", start) + 3])
    return "\n".join(helpers)


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
@pytest.mark.parametrize("state,session", [
    ("present", "wt-fixture"), ("unknown", "wt-fixture"),
    ("present", "wt-d\u00e8sktop-win"),
])
def test_shipped_open_check_uses_real_finite_provider_without_psmux(tmp_path, state, session):
    root = tmp_path / "native"
    root.mkdir()
    listener = None
    worker = None
    failures = []
    if state == "unknown":
        (root / (session + ".port")).write_text("invalid", encoding="ascii")
        (root / (session + ".key")).write_text("synthetic-key", encoding="ascii")
    if state == "present":
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(15)
        (root / (session + ".port")).write_text(str(listener.getsockname()[1]), encoding="ascii")
        (root / (session + ".key")).write_text("synthetic-key", encoding="ascii")

        def serve():
            try:
                for command, response in (
                    (b"list-panes ", b"OK\nP\twt-fixture\t$7:100\t%2\t0\t1\tC:\\fixture\n"),
                    (b"list-clients ", b"OK\n"),
                    (b"display-message ", b"OK\nI\twt-fixture\t$7:100\t%2\n"),
                ):
                    with listener.accept()[0] as stream:
                        stream.settimeout(5)
                        with stream.makefile("rb") as reader:
                            scheme = b"AU" + b"TH "
                            assert reader.readline() == scheme + b"synthetic-key\n"
                            assert reader.readline().startswith(command)
                            stream.sendall(response.replace(b"wt-fixture", session.encode("utf-8")))
            except Exception as exc:
                failures.append(exc)

        worker = threading.Thread(target=serve)
        worker.start()
    script = tmp_path / "open.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$VenvPython=$args[0]\n"
        "$sessionName=$args[1]\n"
        "$script:AwMuxObservationPython=$null\n"
        "$script:AwMuxObservationProvider=$null\n"
        "$script:AwPsmuxBin='forbidden-native-cli'\n"
        + _helpers()
        + "\ntry { $present=Test-AwMuxCreationSession $sessionName;"
        "ConvertTo-Json -Compress @{present=$present;unknown=$false} }"
        " catch { ConvertTo-Json -Compress @{unknown=$true;error=$_.Exception.Message} }\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-File", str(script), sys.executable, session],
            env=dict(os.environ, PSMUX_DATA_DIR=str(root)),
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
    finally:
        if worker:
            worker.join(timeout=16)
            listener.close()
            assert not worker.is_alive() and not failures
    assert result.returncode == 0, result.stdout + result.stderr
    reply = json.loads(result.stdout)
    assert reply["unknown"] is (state == "unknown"), reply
    if state != "unknown":
        assert reply["present"] is (state == "present")


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
def test_unsupported_old_runtime_cannot_fall_back_to_passive_cli(tmp_path):
    script = tmp_path / "old.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n$VenvPython=$args[0]\n"
        "$script:AwMuxObservationPython=$null\n$script:AwMuxObservationProvider=$null\n"
        + _helpers()
        + "\nfunction Invoke-AwMuxCoreProcess { return @{code=1;output=''} }\n"
        "if((Get-AwMuxObservation 'wt-fixture').status -ne 'unsupported'){throw 'Guessed support'}\n"
        "$refused=$false\ntry{Test-AwMuxCreationSession 'wt-fixture'}catch{$refused=$true}\n"
        "if(-not $refused){throw 'Unknown became absence'}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-File", str(script), sys.executable],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
