"""Payload-local invocation and explicit management-boundary tests."""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import venv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from agent_remote_driver.session_host import launcher

PLUGIN = Path(__file__).resolve().parents[1]

_ABBREVIATED_OBSERVER_ARGS = [
    pytest.param(
        [option, "demo", "observe-session", "fixture"],
        id=f"abbreviation-project-{len(option)}-separate",
    )
    for option in ("--project"[:length] for length in range(3, len("--project")))
] + [
    pytest.param(
        [option + "=demo", "session-observation-history", "fixture"],
        id=f"abbreviation-project-{len(option)}-attached",
    )
    for option in ("--project"[:length] for length in range(3, len("--project")))
] + [
    pytest.param(
        [option, "observe-session", "fixture"],
        id=f"abbreviation-json-{len(option)}",
    )
    for option in ("--json"[:length] for length in range(3, len("--json")))
] + [
    pytest.param(
        ["--proj", "observe-session", "create", "fixture"],
        id="abbreviation-project-value-is-not-verb",
    ),
]


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
@pytest.mark.parametrize("argv", [
    ["observe-session", "fixture"],
    ["--json", "session-observation-history", "fixture"],
    ["--project", "demo", "observe-session", "fixture"],
    ["-p", "demo", "session-observation-history", "fixture"],
    ["--project=demo", "--json", "observe-session", "fixture"],
    ["-pdemo", "observe-session", "fixture"],
    ["-p=demo", "observe-session", "fixture"],
    ["agent-bridge", "--project", "demo", "observe-session", "fixture"],
    ["--project", "observe-session", "create", "fixture"],
    ["--project=session-observation-history", "create", "fixture"],
    pytest.param(["observe-owner"], id="membership-direct"),
    pytest.param(["--proj=demo", "--j", "observe-owner"], id="membership-project-json"),
    pytest.param(["-p", "observe-owner", "create", "fixture"], id="membership-value-not-verb"),
    pytest.param(["telemetry", "capabilities"], id="telemetry-capabilities"),
    pytest.param(
        ["telemetry", "retained", "--directory", "unused", "--apply", "--confirm", "unused"],
        id="telemetry-apply",
    ),
] + _ABBREVIATED_OBSERVER_ARGS)
def test_passive_observer_attributed_payload_never_provisions(tmp_path, shell, mode, argv):
    """Run the shipped dispatcher with inert context/publication/installer spies."""
    if shell == "bash" and os.name == "nt":
        pytest.skip("native POSIX Bash required; Windows bash.exe is a WSL launcher")
    from agent_bridge.__main__ import build_parser

    argv = list(argv)
    verb = "telemetry" if argv[0] == "telemetry" else (
        "observe-owner" if argv[-1] == "observe-owner" else argv[-2]
    )
    if verb == "observe-owner":
        argv += ["--owner-ref", "fixture-host/demo/parent-worktree", "--contract-version", "1", "--json"]
    elif verb not in {"create", "telemetry"}:
        argv += ["--worktree-id", "fixture-worktree", "--contract-version", "1", "--json"]
    parser_argv = argv[1:] if argv[0] == "agent-bridge" else argv
    parsed = build_parser().parse_args(parser_argv)
    assert parsed.command == verb
    if verb == "create":
        assert parsed.project in {"observe-session", "session-observation-history", "observe-owner"}
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    suffix = "sh" if shell == "bash" else "ps1"
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    context_scripts = scripts / "installation-context"
    context_scripts.mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-bridge"}', encoding="utf-8")
    for relative in (
        "bin/agent-bridge" if shell == "bash" else "bin/agent-bridge.ps1",
        f"scripts/runtime-gate.{suffix}",
        "scripts/installation-context/json-query.awk",
    ):
        shutil.copyfile(PLUGIN / relative, payload / relative)
    shutil.copyfile(
        PLUGIN / f"scripts/resolve-runtime.{suffix}",
        scripts / f"resolve-runtime-original.{suffix}",
    )
    home = tmp_path / "home"
    home.mkdir()
    runtime = home / (".agent-bridge" if mode == "legacy" else "namespaced-runtime")
    context = home / "contexts" / "market" / "agent-bridge" / "receipt" / "context.json"
    status = {
        "status": "ready", "reason": "legacy" if mode == "legacy" else "namespaced-active",
        "actualMode": mode, "desiredMode": mode, "installGeneration": "fixture-generation",
        "runtimeRoot": str(runtime), "context": str(context), "marketplaceId": "fixture-market",
        "legacy": {"root": str(home / ".agent-bridge")},
    }
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    validated = tmp_path / "validated.json"
    validated.write_text(json.dumps({
        "generation": "fixture-generation", "pluginRoot": str(runtime),
    }), encoding="utf-8")
    calls = tmp_path / "calls"
    installer_marker = tmp_path / "installer-called"
    if shell == "bash":
        (context_scripts / "installation-context.sh").write_text(
            'printf "%s\\n" "$1" >> "$PROBE_CALLS"\n'
            'case "$1" in\n'
            'status) cat "$PROBE_STATUS" ;;\n'
            'validate) cat "$PROBE_VALIDATED" ;;\n'
            '*) printf "PUBLICATION_SPY\\n" >&2; exit 53 ;;\nesac\n',
            encoding="utf-8",
        )
        (scripts / "resolve-runtime.sh").write_text(
            '# Trace contract: normal/read-only/ready-v1\n'
            'printf "resolver:%s:%s\\n" "$AGENT_RT_RESOLVE_TRACE_MODE" '
            '"${AGENT_BRIDGE_NO_SELFPROVISION:-unset}" >> "$PROBE_CALLS"\n'
            '. "$AGENT_BRIDGE_PAYLOAD_ROOT/scripts/resolve-runtime-original.sh"\n',
            encoding="utf-8",
        )
        (scripts / "install.sh").write_text(
            'touch "$PROBE_INSTALLER"; exit 91\n', encoding="utf-8",
        )
        command = [host, str(payload / "bin/agent-bridge"), *argv]
    else:
        (context_scripts / "installation-context.ps1").write_text(
            "param($Action,$PayloadRoot,$PluginId,$LegacyRoot,$Context,$DurableHome,"
            "$ExpectedPluginId,$ExpectedPayloadRoot)\n"
            "[IO.File]::AppendAllText($env:PROBE_CALLS, $Action + \"`n\")\n"
            "switch -CaseSensitive ($Action) {\n"
            "'status' { Get-Content -Raw -LiteralPath $env:PROBE_STATUS }\n"
            "'validate' { Get-Content -Raw -LiteralPath $env:PROBE_VALIDATED }\n"
            "default { [Console]::Error.WriteLine('PUBLICATION_SPY'); exit 53 }\n"
            "}\n$global:LASTEXITCODE=0\n", encoding="utf-8",
        )
        (scripts / "resolve-runtime.ps1").write_text(
            "# Trace contract: normal/read-only/ready-v1\nparam($TraceMode)\n"
            "$value = if ($env:AGENT_BRIDGE_NO_SELFPROVISION) { "
            "$env:AGENT_BRIDGE_NO_SELFPROVISION } else { 'unset' }\n"
            "[IO.File]::AppendAllText($env:PROBE_CALLS, "
            "'resolver:' + $TraceMode + ':' + $value + \"`n\")\n"
            ". (Join-Path $PSScriptRoot 'resolve-runtime-original.ps1') -TraceMode $TraceMode\n",
            encoding="utf-8",
        )
        (scripts / "install.ps1").write_text(
            "[IO.File]::WriteAllText($env:PROBE_INSTALLER,'called'); exit 91\n",
            encoding="utf-8",
        )
        command = [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(payload / "bin/agent-bridge.ps1"), *argv,
        ]
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_BRIDGE_", "AGENT_RT_", "COPILOT_EXTENSIONS_", "UPDATE_DRIVER_"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT"}
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(home / ".copilot"),
        PROBE_CALLS=str(calls), PROBE_STATUS=str(status_file), PROBE_VALIDATED=str(validated),
        PROBE_INSTALLER=str(installer_marker),
    )
    # PowerShell's host-owned startup caches are not plugin runtime state.
    host_cache = home / "AppData" / "Local" / "Microsoft" / "PowerShell"

    def snapshot_home():
        return {path.relative_to(home): path.read_bytes()
                for path in home.rglob("*")
                if path.is_file() and not path.is_relative_to(host_cache)}

    before = snapshot_home()
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    passive = verb != "create"
    recorded = calls.read_text(encoding="utf-8").splitlines()
    if passive:
        assert result.returncode == 1, result.stdout + result.stderr
        label = "configuration-only" if verb == "telemetry" else "passive observation"
        assert label + "; no self-provision" in result.stderr
        assert recorded == [
            "status", *(["validate"] if mode == "namespaced" else []),
            "resolver:read-only:unset",
        ]
    else:
        assert result.returncode in {3, 53}, result.stdout + result.stderr
        assert "PUBLICATION_SPY" in result.stderr
        assert "resolver:ready:unset" in recorded
        assert "probe-plugin-publication" in recorded
    assert not installer_marker.exists()
    assert not runtime.exists()
    assert snapshot_home() == before


@pytest.fixture(scope="module")
def windows_payload_runtime(tmp_path_factory):
    """Stage the real payload gate and a completed, selected probe runtime."""
    if os.name != "nt":
        pytest.skip("native Windows payload transport")
    tmp_path = tmp_path_factory.mktemp("windows-payload")
    plugin = tmp_path / "payload"
    shutil.copytree(PLUGIN / "bin", plugin / "bin")
    shutil.copytree(PLUGIN / "scripts", plugin / "scripts")
    for name in ("plugin.json", "payload-invocation.json"):
        shutil.copy2(PLUGIN / name, plugin / name)
    home = tmp_path / "home"
    home.mkdir()
    runtime = home / ".agent-bridge"
    slot = runtime / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False).create(slot)
    (slot / ".install-complete.json").write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "completed_at": "2026-01-01T00:00:00Z",
                "pid": 1,
            }
        ),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")
    module = slot / "Lib" / "site-packages" / "agent_bridge"
    shutil.copytree(
        PLUGIN / "src" / "agent_bridge", module,
        ignore=shutil.ignore_patterns("__pycache__", "__main__.py"),
    )
    (module.parent / "test-dependencies.pth").write_text(
        f"import site; site.addsitedir({str(Path(sys.prefix) / 'Lib' / 'site-packages')!r})\n",
        encoding="utf-8",
    )
    (module / "__main__.py").write_text(
        """import json
import os
import struct
import sys

def read_exact(size):
    result = bytearray()
    while len(result) < size:
        part = sys.stdin.buffer.read(size - len(result))
        if not part:
            if not result:
                return None
            raise EOFError("partial frame")
        result.extend(part)
    return bytes(result)

assert os.path.samefile(sys.executable, os.environ["EXPECTED_RUNTIME_PYTHON"])
assert os.path.samefile(sys.prefix, os.environ["EXPECTED_RUNTIME_SLOT"])
assert os.path.samefile(os.environ["AGENT_BRIDGE_INSTALL_DIR"],
                        os.environ["EXPECTED_RUNTIME_ROOT"])
assert os.path.samefile(os.environ["AGENT_RT_ROOT"],
                        os.environ["EXPECTED_RUNTIME_ROOT"])
if os.environ.get("PROBE_REAL_CARRIER"):
    from agent_bridge.carrier import cmd_carrier_stdio
    cmd_carrier_stdio()
    sys.exit(0)
if sys.argv[-2:] == ["--prompt-file", "-"]:
    sys.stdout.buffer.write(sys.stdin.buffer.read())
    sys.stderr.buffer.write(json.dumps(sys.argv[1:]).encode("utf-8"))
    sys.exit(17)
if os.environ.get("PROBE_DESCENDANT_FILE"):
    import subprocess
    from pathlib import Path
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    marker = Path(os.environ["PROBE_DESCENDANT_FILE"])
    pending = marker.with_suffix(".pending")
    pending.write_text(str(child.pid))
    pending.replace(marker)
sys.stdout.buffer.write(struct.pack("!I", 5) + b"ready")
sys.stdout.buffer.flush()
while True:
    header = read_exact(4)
    if header is None:
        break
    payload = read_exact(struct.unpack("!I", header)[0])
    if payload is None:
        raise EOFError("missing payload")
    sys.stdout.buffer.write(header + payload)
    sys.stdout.buffer.flush()
sys.stderr.buffer.write(b"probe-stderr")
sys.stderr.buffer.flush()
sys.exit(17)
""",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENT_BRIDGE_", "COPILOT_EXTENSIONS_"))
        and key not in ("PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT")
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        COPILOT_HOME=str(home / ".copilot"),
        COPILOT_PLUGIN_ROOT=str(plugin),
        AGENT_BRIDGE_INSTALL_DIR=str(runtime),
        AGENT_BRIDGE_NO_SELFPROVISION="1",
        EXPECTED_RUNTIME_ROOT=str(runtime),
        EXPECTED_RUNTIME_SLOT=str(slot),
        EXPECTED_RUNTIME_PYTHON=str(slot / "Scripts" / "python.exe"),
        POWERSHELL_TELEMETRY_OPTOUT="1",
    )
    installer = (plugin / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start = installer.index("function Write-Binstubs {")
    end = installer.index("\nfunction Resolve-SnapshotInstallerEngineSource {", start)
    legacy_bin = home / ".local" / "bin"
    legacy_bin.mkdir(parents=True)
    render = plugin / "scripts" / "render-test-binstubs.ps1"
    render.write_text(
        "\n".join(
            (
                "$ErrorActionPreference = 'Stop'",
                f"$PluginDir = '{str(plugin).replace(chr(39), chr(39) * 2)}'",
                f"$InstallDir = '{str(runtime).replace(chr(39), chr(39) * 2)}'",
                f"$LocalBin = '{str(legacy_bin).replace(chr(39), chr(39) * 2)}'",
                "$BinstubPs1 = Join-Path $LocalBin 'agent-bridge.ps1'",
                "$BinstubCmd = Join-Path $LocalBin 'agent-bridge.cmd'",
                "$publishGlobalBinstubs = $true",
                "function Write-Ok { param([string]$Message) }",
                installer[start:end],
                "Write-Binstubs",
            )
        ),
        encoding="utf-8",
    )
    host = shutil.which("pwsh") or shutil.which("powershell")
    assert host is not None
    rendered = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(render)],
        env=env, capture_output=True, timeout=15, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert rendered.returncode == 0, rendered.stderr
    return plugin, legacy_bin, env


def _assert_generated_windows_binary_frames(
    windows_payload_runtime, entrypoint: str, surface: str, held_open: bool
) -> None:
    plugin, legacy_bin, base_env = windows_payload_runtime
    env = dict(base_env)
    bin_dir = plugin / "bin" if surface == "payload" else legacy_bin
    if entrypoint.endswith("51"):
        system32 = Path(os.environ["SystemRoot"]) / "System32"
        # Select the legacy shell without removing its declared Python prerequisite.
        env["PATH"] = os.pathsep.join(
            (sys.base_prefix, str(system32), str(system32 / "WindowsPowerShell" / "v1.0"))
        )
    if entrypoint.startswith("powershell"):
        host = (
            str(system32 / "WindowsPowerShell" / "v1.0" / "powershell.exe")
            if entrypoint.endswith("51")
            else shutil.which("pwsh") or shutil.which("powershell")
        )
        assert host is not None
        command = [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(bin_dir / "agent-bridge.ps1"), "carrier", "--stdio",
        ]
    else:
        command = [
            os.environ["COMSPEC"], "/d", "/s", "/c",
            str(bin_dir / "agent-bridge.cmd"), "carrier", "--stdio",
        ]
    frame = b"".join(
        struct.pack("!I", size)
        + (bytes(range(256)) * ((size + 255) // 256))[:size]
        for size in (0, 1, 10, 13, 26, 255, 256, 1024, 65536)
    )
    expected = struct.pack("!I", 5) + b"ready" + frame
    process, job = spawn_sync_in_kill_on_close_job(
        command, env=env, cwd=plugin.parent, require_job=True,
        allow_breakaway=False, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert job is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            deadline = time.monotonic() + 15
            if held_open:
                assert process.stdin is not None and process.stdout is not None
                output = pool.submit(process.stdout.read, len(expected))

                def write_frames():
                    process.stdin.write(frame)
                    process.stdin.flush()

                writer = pool.submit(write_frames)
                writer.result(timeout=max(0.01, deadline - time.monotonic()))
                assert output.result(
                    timeout=max(0.01, deadline - time.monotonic())
                ) == expected
                assert process.poll() is None
                process.stdin.close()
                process.stdin = None
                stdout, stderr = process.communicate(
                    timeout=max(0.01, deadline - time.monotonic())
                )
                assert stdout == b""
            else:
                communication = pool.submit(process.communicate, frame)
                stdout, stderr = communication.result(
                    timeout=max(0.01, deadline - time.monotonic())
                )
                assert stdout == expected, (process.returncode, stderr)
            assert process.returncode == 17, stderr
            assert stderr == b"probe-stderr"
        finally:
            job.terminate_and_wait(2)
            process.wait(timeout=2)
            job.close()


@pytest.mark.parametrize("entrypoint", ["powershell", "cmd"])
@pytest.mark.skipif(os.name != "nt", reason="native Windows payload transport")
def test_generated_management_binary_smoke(windows_payload_runtime, entrypoint: str) -> None:
    _assert_generated_windows_binary_frames(
        windows_payload_runtime, entrypoint, "management", True
    )


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("surface", ["payload", "management"])
@pytest.mark.parametrize("held_open", [False, True])
@pytest.mark.parametrize(
    "entrypoint", ["powershell", "cmd", "powershell51", "cmd51"]
)
@pytest.mark.skipif(os.name != "nt", reason="native Windows payload transport")
def test_generated_windows_payload_preserves_binary_frames(
    windows_payload_runtime, entrypoint: str, surface: str, held_open: bool
) -> None:
    _assert_generated_windows_binary_frames(
        windows_payload_runtime, entrypoint, surface, held_open
    )


@pytest.mark.parametrize("surface", ["payload", "management"])
@pytest.mark.parametrize("input_kind", ["native", "objects"])
@pytest.mark.parametrize("host_kind", ["powershell", "powershell51"])
@pytest.mark.skipif(os.name != "nt", reason="native Windows prompt transport")
def test_generated_windows_prompt_stdin(
    windows_payload_runtime, surface: str, input_kind: str, host_kind: str
) -> None:
    plugin, legacy_bin, base_env = windows_payload_runtime
    env = dict(base_env)
    shim = (plugin / "bin" if surface == "payload" else legacy_bin) / "agent-bridge.ps1"
    host = (
        str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        if host_kind == "powershell51"
        else shutil.which("pwsh") or shutil.which("powershell")
    )
    assert host is not None
    prompt = b'quoted "body" & (not a command)\nsecond line'
    if input_kind == "native":
        if host_kind == "powershell51":
            invocation = (
                f"& '{str(shim).replace(chr(39), chr(39) * 2)}' "
                "send example --prompt-file -; exit $LASTEXITCODE"
            )
            command = [
                host, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(invocation.encode("utf-16-le")).decode("ascii"),
            ]
        else:
            command = [
                host, "-NoProfile", "-NonInteractive", "-File", str(shim),
                "send", "example", "--prompt-file", "-",
            ]
        input_bytes = prompt
        expected = prompt
    else:
        command = [
            host, "-NoProfile", "-NonInteractive", "-Command",
            f"'quoted \"body\" & (not a command)\nsecond line' | "
            f"& '{str(shim).replace(chr(39), chr(39) * 2)}' "
            "send example --prompt-file -; exit $LASTEXITCODE",
        ]
        input_bytes = b""
        expected = prompt + os.linesep.encode()
    process, job = spawn_sync_in_kill_on_close_job(
        command, env=env, cwd=plugin.parent, require_job=True,
        allow_breakaway=False, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert job is not None
    try:
        stdout, stderr = process.communicate(input_bytes, timeout=15)
        assert process.returncode == 17, stderr
        assert stdout == expected
        assert json.loads(stderr) == ["send", "example", "--prompt-file", "-"]
    finally:
        job.terminate_and_wait(2)
        process.wait(timeout=2)
        job.close()


@pytest.mark.parametrize("surface", ["payload", "management"])
@pytest.mark.parametrize("previous", [None, "caller-owned-runtime"])
@pytest.mark.skipif(os.name != "nt", reason="PowerShell caller environment")
def test_windows_entrypoint_restores_callers_runtime_root(
    windows_payload_runtime, surface: str, previous: str | None,
) -> None:
    plugin, legacy_bin, base_env = windows_payload_runtime
    environment = dict(base_env)
    if previous is None:
        environment.pop("AGENT_RT_ROOT", None)
    else:
        environment["AGENT_RT_ROOT"] = previous
    shim = (plugin / "bin" if surface == "payload" else legacy_bin) / "agent-bridge.ps1"
    expected = "$null" if previous is None else f"'{previous}'"
    script = (
        f"& '{str(shim).replace(chr(39), chr(39) * 2)}' send example --prompt-file -; "
        "$code = $LASTEXITCODE; "
        f"if ($env:AGENT_RT_ROOT -cne {expected}) {{ throw 'runtime root leaked into caller' }}; "
        "exit $code"
    )
    host = shutil.which("pwsh") or shutil.which("powershell")
    assert host is not None
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        input=b"fixture prompt", capture_output=True, env=environment, timeout=20,
        **no_window_kwargs(),
    )
    assert result.returncode == 17, result.stderr
    assert result.stdout == b"fixture prompt"


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("surface", ["payload", "management"])
@pytest.mark.parametrize("entrypoint", ["powershell", "cmd"])
@pytest.mark.skipif(os.name != "nt", reason="native Windows carrier transport")
def test_real_carrier_through_generated_windows_launchers(
    windows_payload_runtime, surface: str, entrypoint: str
) -> None:
    from ssh_manager import Envelope, EnvelopeType, hello_envelope
    from ssh_manager.carrier import read_envelope_sync, write_envelope_sync

    plugin, legacy_bin, base_env = windows_payload_runtime
    env = {**base_env, "PROBE_REAL_CARRIER": "1"}
    bin_dir = plugin / "bin" if surface == "payload" else legacy_bin
    if entrypoint == "powershell":
        host = shutil.which("pwsh") or shutil.which("powershell")
        assert host is not None
        command = [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(bin_dir / "agent-bridge.ps1"), "carrier", "--stdio",
        ]
    else:
        command = [
            os.environ["COMSPEC"], "/d", "/s", "/c",
            str(bin_dir / "agent-bridge.cmd"), "carrier", "--stdio",
        ]
    process, job = spawn_sync_in_kill_on_close_job(
        command, env=env, cwd=plugin.parent, require_job=True,
        allow_breakaway=False, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert job is not None
    assert process.stdin is not None and process.stdout is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            deadline = time.monotonic() + 15
            hello = pool.submit(read_envelope_sync, process.stdout).result(
                timeout=max(0.01, deadline - time.monotonic())
            )
            assert hello is not None and hello.type is EnvelopeType.HELLO
            write_envelope_sync(process.stdin, hello_envelope())
            write_envelope_sync(
                process.stdin,
                Envelope(
                    EnvelopeType.REQUEST, request_id="framing-test",
                    payload={"operation": "synthetic"},
                ),
            )
            response = pool.submit(read_envelope_sync, process.stdout).result(
                timeout=max(0.01, deadline - time.monotonic())
            )
            assert response is not None and response.type is EnvelopeType.ERROR
            assert response.request_id == "framing-test"
            assert response.payload["code"] == "unsupported_operation"
            assert process.poll() is None
            process.stdin.close()
            process.stdin = None
            stdout, stderr = process.communicate(
                timeout=max(0.01, deadline - time.monotonic())
            )
            assert process.returncode == 0, stderr
            assert stdout == stderr == b""
        finally:
            job.terminate_and_wait(2)
            process.wait(timeout=2)
            job.close()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("entrypoint", ["powershell", "cmd"])
@pytest.mark.skipif(os.name != "nt", reason="native Windows descendant custody")
def test_generated_management_cancellation_retires_descendants(
    windows_payload_runtime, tmp_path: Path, entrypoint: str
) -> None:
    import ctypes
    from ctypes import wintypes

    plugin, legacy_bin, base_env = windows_payload_runtime
    marker = tmp_path / "descendant-pid"
    env = {**base_env, "PROBE_DESCENDANT_FILE": str(marker)}
    if entrypoint == "powershell":
        host = shutil.which("pwsh") or shutil.which("powershell")
        assert host is not None
        command = [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(legacy_bin / "agent-bridge.ps1"), "carrier", "--stdio",
        ]
    else:
        command = [
            os.environ["COMSPEC"], "/d", "/s", "/c",
            str(legacy_bin / "agent-bridge.cmd"), "carrier", "--stdio",
        ]
    process, job = spawn_sync_in_kill_on_close_job(
        command, env=env, cwd=plugin.parent, require_job=True,
        allow_breakaway=False, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert job is not None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    descendant = None
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.01)
        assert marker.exists()
        descendant = kernel.OpenProcess(0x100000, False, int(marker.read_text()))
        assert descendant, ctypes.get_last_error()
        assert kernel.WaitForSingleObject(descendant, 0) == 258
        job.terminate_and_wait(2)
        process.wait(timeout=2)
        assert kernel.WaitForSingleObject(descendant, 2000) == 0
    finally:
        if job.handle is not None:
            job.terminate_and_wait(2)
        process.wait(timeout=2)
        job.close()
        if descendant is not None:
            assert kernel.CloseHandle(descendant)


@pytest.mark.skipif(os.name != "nt", reason="native Windows context admission")
def test_generated_payload_refuses_unavailable_context(
    windows_payload_runtime, tmp_path: Path
) -> None:
    plugin, _legacy_bin, base_env = windows_payload_runtime
    env = {**base_env, "COPILOT_EXTENSIONS_CONTEXT": str(tmp_path / "missing-context")}
    host = shutil.which("pwsh") or shutil.which("powershell")
    assert host is not None
    process, job = spawn_sync_in_kill_on_close_job(
        [host, "-NoProfile", "-NonInteractive", "-File",
         str(plugin / "bin" / "agent-bridge.ps1"), "carrier", "--stdio"],
        env=env, cwd=plugin.parent, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert job is not None
    try:
        stdout, stderr = process.communicate(b"", timeout=15)
        assert process.returncode == 126, stderr
        assert stdout == b""
        assert b"installation context" in stderr
    finally:
        job.terminate_and_wait(2)
        process.wait(timeout=2)
        job.close()


def test_payload_manifest_describes_agent_bridge_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-bridge",
        "module": "agent_bridge",
        "legacyRuntimeRoot": ".agent-bridge",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_BRIDGE_NO_SELFPROVISION",
        "purpose": "Communicate with persistent agent sessions",
        "installer": "install",
        "installerLifecycle": True,
        "inProcessCatalog": True,
        "windowsCatalogShim": "powershell",
        "powershellPipelineInput": True,
        "provisionMode": "direct",
        "payloadRootEnv": "AGENT_BRIDGE_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }

    posix = (PLUGIN / "bin" / "agent-bridge").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-bridge.ps1").read_text(encoding="utf-8")
    assert 'scripts/runtime-gate.sh' in posix
    assert 'AGENT_BRIDGE_PAYLOAD_ROOT' in posix
    assert 'scripts\\runtime-gate.ps1' in powershell
    assert 'AGENT_BRIDGE_PAYLOAD_ROOT' in powershell


def test_session_catalog_producer_is_not_registered_as_a_hook() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    assert all(
        "COPILOT_PLUGIN_ROOT" in hook["bash"]
        and "COPILOT_PLUGIN_ROOT" in hook["powershell"]
        for hook in session_hooks
    )
    assert "else printf '{}'" in session_hooks[0]["bash"]
    assert "else { [Console]::Out.Write('{}') }" in session_hooks[0]["powershell"]
    assert not any("emit-command-catalog" in str(hook) for hook in session_hooks)

    powershell_catalog = (
        PLUGIN / "scripts" / "emit-command-catalog.ps1"
    ).read_text(encoding="utf-8")
    assert r"bin\agent-bridge.ps1" in powershell_catalog
    assert "shell = 'direct'" in powershell_catalog


@pytest.mark.portfolio_tier("T3")
def test_payload_gate_refuses_old_resolver_before_first_use_writes(tmp_path: Path) -> None:
    suffix = "ps1" if os.name == "nt" else "sh"
    engine = shutil.which("pwsh" if os.name == "nt" else "bash")
    if engine is None:
        pytest.skip("Native shell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("runtime-gate", "install", "resolve-runtime"):
        shutil.copy2(PLUGIN / "scripts" / f"{name}.{suffix}", scripts)
    shutil.copytree(PLUGIN / "scripts/installation-context", scripts / "installation-context")
    resolver = scripts / f"resolve-runtime.{suffix}"
    content = resolver.read_text(encoding="utf-8-sig")
    capability = "# Trace contract: normal/read-only/ready-v1"
    assert capability in content
    resolver.write_text(content.replace(capability, "# Older resolver"), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    runtime = home / ".agent-bridge"
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_BRIDGE_PAYLOAD_ROOT": str(payload),
        "AGENT_BRIDGE_INSTALL_DIR": str(runtime), "AGENT_BRIDGE_CONFIG_DIR": str(runtime),
    }
    command = [engine]
    if os.name == "nt":
        command += ["-NoLogo", "-NoProfile", "-File"]
    command += [str(scripts / f"runtime-gate.{suffix}"), "--help"]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 126, result.stdout + result.stderr
    assert "read-only runtime resolver is unavailable" in result.stderr
    assert not runtime.exists()


def test_out_of_session_boundaries_remain_explicit() -> None:
    pivot = json.loads(
        (PLUGIN / "pivots" / "agent-bridge.json").read_text(encoding="utf-8")
    )
    assert pivot["list"][0] == "agent-bridge"

    # The Picker runs this argv verbatim, so it must actually parse. `--json` is
    # a GLOBAL flag (declared before the subcommand); placing it after the
    # subcommand makes argparse exit 2 with "unrecognized arguments: --json" and
    # the Bridges tab can never populate.
    from agent_bridge.__main__ import build_parser

    parsed = build_parser().parse_args(pivot["list"][1:])
    assert parsed.json is True
    assert parsed.command == "agents"

    provider = (
        PLUGIN / "src" / "agent_bridge" / "session_host" / "spawner.py"
    ).read_text(encoding="utf-8")
    assert 'shutil.which("agent-codespaces")' in provider
    assert "marketplace-isolation: allow provider-management" in provider

    cli_reference = (
        PLUGIN
        / "skills"
        / "agent-bridge"
        / "references"
        / "cli-commands.md"
    ).read_text(encoding="utf-8")
    assert "agent-bridge service start" in cli_reference
    assert "marketplace-isolation: allow service-management" in cli_reference

    extension = (
        PLUGIN / "extensions" / "agent-bridge" / "extension.mjs"
    ).read_text(encoding="utf-8")
    # KIND_GUIDANCE (the status-check reply instructions) moved out of
    # extension.mjs into delivery.mjs during agent-bridge-cli-mode-sessions
    # Phase 1 (extension.mjs's top-level joinSession() makes it untestable
    # directly, so the pure rendering/options logic was extracted into a
    # separately-importable module -- see delivery.mjs's own module docstring
    # and extension.mjs's `import { buildDeliveredSendOptions } from
    # "./delivery.mjs"`). extension.mjs still delivers that guidance to the
    # operator at runtime through the import; the literal text now lives in
    # delivery.mjs, not duplicated here.
    delivery = (
        PLUGIN.parents[1] / "libs/copilot-driver-core/src/agent_remote_driver/bridge_extension/delivery.mjs"
    ).read_text(encoding="utf-8")
    assert "agent-bridge session command catalog" in delivery
    assert "`agent-bridge send <reply-to>" not in extension
    assert "`agent-bridge send <reply-to>" not in delivery


def test_list_command_docs_place_global_json_before_subcommand() -> None:
    skill = (
        PLUGIN / "skills" / "agent-bridge" / "SKILL.md"
    ).read_text(encoding="utf-8")
    cli_reference = (
        PLUGIN
        / "skills"
        / "agent-bridge"
        / "references"
        / "cli-commands.md"
    ).read_text(encoding="utf-8")
    combined = skill + "\n" + cli_reference

    for command in ("agents", "machines", "sessions"):
        assert f"<agent-bridge catalog argv[0]> --json {command}" in combined
        assert f"<agent-bridge catalog argv[0]> {command} --json" not in combined
    assert "<agent-bridge catalog argv[0]> --json config show" in combined
    assert "<agent-bridge catalog argv[0]> config show --json" not in combined


@pytest.mark.asyncio
async def test_session_host_strips_parent_payload_context(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_spawn(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/parent/payload")
    monkeypatch.setattr(launcher.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(launcher, "child_preexec", lambda: None)

    await launcher._spawn_child(
        ["copilot"],
        str(tmp_path),
        {"COPILOT_PLUGIN_ROOT": "/explicit/payload", "KEEP": "yes"},
    )

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "COPILOT_PLUGIN_ROOT" not in child_env
    assert child_env["KEEP"] == "yes"
    spawn_kwargs = captured["kwargs"]
    assert isinstance(spawn_kwargs, dict)
    assert spawn_kwargs["creationflags"] == launcher.no_window_flags()
    assert spawn_kwargs["stdin"] is launcher.asyncio.subprocess.PIPE
    assert spawn_kwargs["stdout"] is launcher.asyncio.subprocess.PIPE


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_preserves_stdin(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    bin_dir = plugin / "bin"
    bin_dir.mkdir(parents=True)
    shutil.copy2(PLUGIN / "bin" / "agent-bridge", bin_dir / "agent-bridge")
    shutil.copy2(PLUGIN / "plugin.json", plugin)
    scripts = plugin / "scripts"
    scripts.mkdir()
    (scripts / "runtime-gate.sh").write_text(
        "#!/usr/bin/env bash\nexec \"$(dirname \"$0\")/../fake-python\" -m agent_bridge \"$@\"\n",
        encoding="utf-8",
    )
    (scripts / "runtime-gate.sh").chmod(0o755)
    fake_python = plugin / "fake-python"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    home = tmp_path / "home"
    runtime = home / ".agent-bridge"
    python = runtime / "versions" / "test" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (python.parents[1] / ".install-complete.json").write_text(
        (
            '{"version": "test", '
            '"completed_at": "2026-08-27T00:00:00Z", "pid": 1}'
        ),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("test\n", encoding="utf-8")

    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-bridge"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(plugin / "bin" / "agent-bridge"), "create", "example"],
        input="prompt",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "-m agent_bridge create example|prompt"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD test")
def test_windows_catalog_cmd_preserves_native_stdin(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    bin_dir = plugin / "bin"
    bin_dir.mkdir(parents=True)
    cmd = bin_dir / "agent-bridge.cmd"
    shutil.copy2(PLUGIN / "bin" / "agent-bridge.cmd", cmd)
    shutil.copy2(PLUGIN / "bin" / "agent-bridge.ps1", bin_dir)
    shutil.copy2(PLUGIN / "plugin.json", plugin)

    scripts = plugin / "scripts"
    scripts.mkdir()
    (scripts / "runtime-gate.ps1").write_text(
        "& (Join-Path (Split-Path -Parent $PSScriptRoot) 'fake-python.cmd') -m agent_bridge @args\n",
        encoding="utf-8",
    )
    (plugin / "fake-python.cmd").write_text(
        '@echo off\r\n<nul set /p "=%*::"\r\nmore\r\nexit /b 0\r\n',
        encoding="utf-8",
    )

    comspec = os.environ.get("COMSPEC", "cmd.exe")
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(plugin),
        "PATH": str(Path(comspec).parent),
    }
    result = subprocess.run(
        [
            comspec, "/d", "/s", "/c", str(cmd),
            "create", "target", "--prompt-file", "prompt.txt",
        ],
        input="task body",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace("\r\n", "\n") == (
        "-m agent_bridge create target --prompt-file prompt.txt::task body\n"
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD test")
def test_windows_catalog_cmd_survives_oversized_path(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    bin_dir = plugin / "bin"
    bin_dir.mkdir(parents=True)
    cmd = bin_dir / "agent-bridge.cmd"
    shutil.copy2(PLUGIN / "bin" / "agent-bridge.cmd", cmd)
    shutil.copy2(PLUGIN / "bin" / "agent-bridge.ps1", bin_dir)
    shutil.copy2(PLUGIN / "plugin.json", plugin)

    scripts = plugin / "scripts"
    scripts.mkdir()
    (scripts / "runtime-gate.ps1").write_text(
        "& (Join-Path (Split-Path -Parent $PSScriptRoot) 'fake-python.ps1') -m agent_bridge @args\n",
        encoding="utf-8",
    )
    (plugin / "fake-python.ps1").write_text(
        "[Console]::Out.Write(($args -join ' ') + '::')\n",
        encoding="utf-8",
    )

    comspec = os.environ.get("COMSPEC", "cmd.exe")
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    (host_bin / "pwsh.cmd").write_text(
        '@"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" %*\n',
        encoding="utf-8",
    )
    oversized_path = f"{host_bin}{os.pathsep}{'X' * 9000}"
    assert len(oversized_path) > 8191
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(plugin),
        "PATH": oversized_path,
    }
    result = subprocess.run(
        [comspec, "/d", "/s", "/c", str(cmd), "agents", "--all-projects"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "-m agent_bridge agents --all-projects::"
