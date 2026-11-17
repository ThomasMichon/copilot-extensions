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
from agent_remote_driver.mux_observation_types import Observation, Pane

from worktree_manager import mux_attached_clients, mux_daemon, mux_discovery


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"


def _observed(clients: int = 3, incarnation: str = "$7:100") -> Observation:
    return Observation(
        "present", incarnation=incarnation, clients=clients,
        panes=(Pane("%2", False, True, os.getcwd()),),
    )


def test_native_discovery_creation_and_client_refresh_share_driver_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_fresh_observation_does_not_reenable_periodic_native_cli_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mux_daemon, "run_mux_child", lambda *a, **k: pytest.fail("Periodic PSMux CLI"))
    assert mux_daemon.apply_status_options(
        {"mux_bin": "psmux.exe", "mux_session": "wt-fixture"}, {"@aw_ctx": "fixture"},
    ) is False


def _helpers() -> str:
    source = SOURCE.read_text("utf-8")
    helpers = []
    for name in (
        "Invoke-AwMuxCoreProcess", "Get-AwMuxObservation",
        "Test-AwMuxCreationSession", "Test-AwPlannedMuxJoin",
        "Get-AwPsmuxCreationVersion", "Assert-AwPsmuxCreationVersion",
    ):
        start = source.index("function " + name + " {")
        helpers.append(source[start:source.index("\n}\n", start) + 3])
    return "\n".join(helpers)


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
@pytest.mark.parametrize("state,session", [
    ("present", "wt-fixture"), ("unknown", "wt-fixture"),
    ("present", "wt-d\u00e8sktop-win"),
])
def test_shipped_open_check_uses_real_finite_provider_without_psmux(
    tmp_path: Path, state: str, session: str,
) -> None:
    shell = shutil.which("pwsh")
    assert shell is not None
    root = tmp_path / "native"
    root.mkdir()
    listener = None
    worker = None
    failures: list[Exception] = []
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

        def serve() -> None:
            assert listener is not None
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
    result: subprocess.CompletedProcess[str] | None = None
    try:
        result = subprocess.run(
            [shell, "-NoProfile", "-File", str(script), sys.executable, session],
            env=dict(os.environ, PSMUX_DATA_DIR=str(root)),
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
    finally:
        if worker:
            worker.join(timeout=16)
            assert listener is not None
            listener.close()
            diagnostic = None if result is None else (result.returncode, result.stdout, result.stderr)
            assert not worker.is_alive() and not failures, (failures, diagnostic)
    assert result is not None
    assert result.returncode == 0, result.stdout + result.stderr
    reply = json.loads(result.stdout)
    assert reply["unknown"] is (state == "unknown"), reply
    if state != "unknown":
        assert reply["present"] is (state == "present")
    else:
        assert "native-query-unavailable" in reply["error"]
        assert "Use a runtime" not in reply["error"]


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
def test_unsupported_old_runtime_cannot_fall_back_to_passive_cli(tmp_path: Path) -> None:
    shell = shutil.which("pwsh")
    assert shell is not None
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
        [shell, "-NoProfile", "-File", str(script), sys.executable],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
@pytest.mark.parametrize("planned", [True, False, "true", None])
def test_planned_positive_join_cannot_fall_through_to_creation(tmp_path: Path, planned: object) -> None:
    source = SOURCE.read_text("utf-8")
    start = source.index('if (-not $noMux) {\n    $wtId =')
    end = source.index('    # Build -e flags', start)
    script = tmp_path / "join.ps1"
    plan = json.dumps({
        "worktree_id": "fixture", "work_dir": str(tmp_path),
        "mux_join_existing": planned,
    })
    script.write_text(
        "$ErrorActionPreference='Stop'\n$noMux=$false\n$script:Probes=0\n"
        + "$plan=ConvertFrom-Json '" + plan.replace("'", "''") + "'\n"
        + _helpers()
        + "\nfunction Write-SetupLog {}\n"
        "function Get-AwMuxObservation { $script:Probes++; return @{status='unknown';reason='fixture'} }\n"
        "function Resolve-AwMuxCore { throw 'Creation support must not run' }\n"
        "function Join-AwExistingPsmuxSession {\n"
        "ConvertTo-Json -Compress @{joined=$true;probes=$script:Probes}; exit 0\n}\n"
        "try {\n" + source[start:end] + "\nthrow 'Unexpected creation fallthrough'\n}\n"
        "} catch { ConvertTo-Json -Compress @{joined=$false;error=$_.Exception.Message} }\n",
        encoding="utf-8",
    )
    shell = shutil.which("pwsh")
    assert shell is not None
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(script)], capture_output=True,
        text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reply = json.loads(result.stdout)
    if planned is True:
        assert reply == {"joined": True, "probes": 0}
    else:
        assert reply["joined"] is False
        assert "refusing duplicate creation" in reply["error"]


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
def test_selected_observation_uses_one_finite_core_call(tmp_path: Path) -> None:
    script = tmp_path / "observe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n$VenvPython='selected-python'\n$script:Calls=0\n"
        + _helpers()
        + """
function Invoke-AwMuxCoreProcess {
    param($Python, $Arguments, $InputText, $TimeoutMilliseconds)
    if ($Python -ne 'selected-python' -or
        ($Arguments -join ' ') -ne '-I -X utf8 -m agent_remote_driver.mux_observation --observe' -or
        $TimeoutMilliseconds -ne 2000) { throw 'Wrong selected boundary' }
    $request = ConvertFrom-Json $InputText
    if ($request.version -ne 1 -or $request.session -ne 'wt-fixture') { throw 'Wrong request' }
    $script:Calls++
    return @{code=0;output='{"contract":"copilot-driver.mux-observation","version":1,"status":"present"}'}
}
if (-not (Test-AwMuxCreationSession 'wt-fixture')) { throw 'Positive observation lost' }
if ($script:Calls -ne 1) { throw 'Observation restarted its provider' }
""",
        encoding="utf-8",
    )
    shell = shutil.which("pwsh")
    assert shell is not None
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(script)], capture_output=True,
        text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell consumer required")
@pytest.mark.parametrize("version,allowed", [
    (None, False), ("3.3.5", False), ("3.3.8", False), ("3.4.0", True),
    ("invalid", False), ("3.4.0.0", True),
])
def test_creation_version_floor_is_independent_of_selected_core(
    tmp_path: Path, version: str | None, allowed: bool,
) -> None:
    helper = SOURCE.with_name("psmux-path.ps1")
    shutil.copyfile(helper, tmp_path / helper.name)
    script = tmp_path / "creation-version.ps1"
    value = "$null" if version is None else "'" + version + "'"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "function Resolve-FixturePsmux { . $args[0] }\n"
        "Resolve-FixturePsmux $args[0]\n"
        "if (Get-Command Test-AwPsmuxVersionCompatible -ErrorAction SilentlyContinue) {"
        " throw 'Fixture leaked resolver-local helpers' }\n"
        + _helpers()
        + "\n$script:AwPsmuxBin='forbidden-native-cli'\n"
        + "function Get-AwPsmuxCreationVersion { return " + value + " }\n"
        + "function Resolve-AwMuxCore { throw 'Creation version read the selected core' }\n"
        + "try { Assert-AwPsmuxCreationVersion; ConvertTo-Json -Compress @{allowed=$true} }\n"
        + "catch { ConvertTo-Json -Compress @{allowed=$false;error=$_.Exception.Message} }\n",
        encoding="utf-8",
    )
    shell = shutil.which("pwsh")
    assert shell is not None
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(script), str(helper)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reply = json.loads(result.stdout)
    assert reply["allowed"] is allowed
    if not allowed:
        assert "require version 3.4.0" in reply["error"]
