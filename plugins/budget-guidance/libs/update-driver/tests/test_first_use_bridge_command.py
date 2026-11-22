"""Actual Bridge commands observe the fixed queued provision owner."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import RequestSpec, TrustedRunner, drain, enqueue, foundation_status, status

from test_first_use import primitive
from test_first_use_bridge import (
    assert_bridge_registration, bridge_service_adapter,
    materialized_bridge as materialized_bridge, native_recipe_runner as native_recipe_runner,
)
from test_first_use_bridge_snapshot import qualify_source
from test_first_use_genesis import observe
from test_first_use_pull_requests_command import command_for, files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import receipt_completed_count


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux Bridge commands",
)


@pytest.fixture(autouse=True)
def discard_read_only_command_case(request, area):
    yield
    if request.node.originalname == "test_bridge_command_refusal_precedes_cold_writes":
        assert not (area / "profile/.copilot-extensions/update-driver").exists()
        shutil.rmtree(area)


def owning_command(fixture, entry, *, qualified=True):
    payload, installation, project = fixture
    source = payload
    if entry != "payload":
        if qualified:
            qualify_source(payload)
        installer = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
        stamp = command_for(installer)[:-1] + (
            ["-Action", "stamp", "-InstallDir", str(installation)] if os.name == "nt"
            else ["stamp", "--install-dir", str(installation)]
        )
        environment = dict(os.environ)
        if os.name == "nt":
            environment["PATH"] = os.pathsep.join((
                str(Path(sys.executable).parent),
                str(Path(os.environ["SystemRoot"]) / "System32"),
            ))
        result = subprocess.run(
            stamp, env=environment, cwd=project, capture_output=True, text=True,
            timeout=90, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if qualified:
            source = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
        script = installation.parent / ".local/bin" / (
            "agent-bridge.ps1" if os.name == "nt" else "agent-bridge"
        )
    else:
        script = payload / "bin" / ("agent-bridge.ps1" if os.name == "nt" else "agent-bridge")
    command = (
        [os.environ["ComSpec"], "/d", "/c", str(script.with_suffix(".cmd")), "--help"]
        if entry == "cmd" else command_for(script)
    )
    return source, installation, project, command


OUTER_RECORDS = (".provision-status", ".provision.lock", ".provision.lock.pid", ".provision.reap.mutex")


def assert_no_outer_owner(installation, previous=None):
    expected = {} if previous is None else previous
    assert {
        name: (installation / name).read_bytes()
        for name in OUTER_RECORDS if (installation / name).exists()
    } == expected


@pytest.mark.parametrize("reason", [
    "missing-driver", "old-driver", "no-self", "pinned", "nested", "managed",
    "malformed-policy", "old-resolver", "custom-root", "cell", "old-dispatcher",
    "unselected", "unavailable", "missing-provenance", "malformed-provenance", "ordinary-copy",
])
def test_bridge_command_refusal_precedes_cold_writes(materialized_bridge, monkeypatch, reason):
    source, installation, project, primary = owning_command(materialized_bridge, "primary")
    suffix = ".ps1" if os.name == "nt" else ""
    commands = [primary, command_for(source / "bin" / ("agent-bridge" + suffix))]
    if reason in {"missing-driver", "old-driver", "old-dispatcher", "old-resolver"}:
        target = (
            source / "libs/update-driver/scripts" / ("first-use.ps1" if os.name == "nt" else "first-use.sh")
            if reason.endswith("driver") else
            source / "scripts" / (
                ("runtime-gate" if reason == "old-dispatcher" else "resolve-runtime") +
                (".ps1" if os.name == "nt" else ".sh")
            )
        )
        if reason == "missing-driver":
            target.unlink()
        else:
            target.write_text(
                "throw 'Unqualified command support executed'\n" if os.name == "nt" else
                "printf 'Unqualified command support executed' >&2\nexit 99\n", encoding="utf-8",
            )
        if reason == "old-dispatcher":
            commands = [primary]
    elif reason in {"no-self", "pinned", "nested", "custom-root", "cell"}:
        key, value = {
            "no-self": ("AGENT_BRIDGE_NO_SELFPROVISION", "0"),
            "pinned": ("AGENT_BRIDGE_PINNED_CONTEXT", "{}"),
            "nested": ("UPDATE_DRIVER_WITNESS", "{}"),
            "custom-root": ("AGENT_BRIDGE_INSTALL_DIR", str(installation.with_name("custom-bridge"))),
            "cell": ("AGENT_BRIDGE_INSTALLATION_ID", "foreign"),
        }[reason]
        monkeypatch.setenv(key, value)
        if reason == "custom-root":
            commands = commands[1:]
    elif reason in {"managed", "malformed-policy"}:
        api = primitive(source)
        arguments = dict(payload_root=source, plugin_id="agent-bridge",
                         legacy_root=installation, project_root=project)
        resolution = api.resolve_installation_mode(**arguments)
        (installation / "runtime-deployment.json").write_text(json.dumps({
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}), encoding="utf-8")
    elif reason == "ordinary-copy":
        source = Path(shutil.copytree(source, source.parents[3] / "ordinary-copy"))
        commands = [command_for(source / "bin" / ("agent-bridge" + suffix))]
    elif reason == "unselected":
        (installation / "payload-dir").write_text(str(source.with_name("missing")), encoding="utf-8")
    elif reason == "unavailable":
        source.rename(source.with_name("unavailable"))
        commands = [primary]
    elif reason == "missing-provenance":
        (source / "snapshot-provenance.json").unlink()
    else:
        (source / "snapshot-provenance.json").write_text("{", encoding="utf-8")
    before, source_before, installed_before = (
        files(installation), files(source), files(materialized_bridge[0]),
    )
    root = installation.parent / ".copilot-extensions/update-driver"
    for command in commands:
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=90, **no_window_kwargs(),
        )
        assert result.returncode != 0, (command, result.stdout, result.stderr)
        assert "Unqualified command support executed" not in result.stderr
        assert not root.exists(), result.stdout + result.stderr
        assert files(installation) == before and files(source) == source_before
        assert files(materialized_bridge[0]) == installed_before
        assert_no_outer_owner(installation)


def command_environment(installation, project, area, entry):
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    if entry == "cmd":
        env["PATH"] += os.pathsep + str(
            Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0"
        )
    return env


def diagnostic_for(root, area):
    return lambda: "\n".join(
        f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
        for path in (*area.glob("native-recipe-*.log"), *root.rglob("*.log")) if path.is_file()
    )


def assert_installed_bridge(source, installation, project, env, command, count):
    root = installation.parent / ".copilot-extensions/update-driver"
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    before = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_bridge,json;from importlib.metadata import version;"
         "print(json.dumps({'origin':agent_bridge.__file__,'version':version('agent-bridge')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    actual = json.loads(identity.stdout)
    assert Path(actual["origin"]).is_relative_to(slot)
    assert Version(actual["version"]) == Version(json.loads((source / "plugin.json").read_text())["version"])
    assert len(status(root=root)) == count
    runner = TrustedRunner((sys.executable, "-c", "pass"), str(project))
    failed = enqueue(RequestSpec("retained-command-failure"), runner=runner, root=root)
    drain(root=root, executor=lambda _transaction: 23)
    assert status(failed.id, root=root).exit_code == 23
    pending = enqueue(RequestSpec("retained-command-pending"), runner=runner, root=root)
    history = status(root=root)
    suffix = ".ps1" if os.name == "nt" else ""
    try:
        for script in (
            source / "bin" / ("agent-bridge" + suffix),
            installation.parent / ".local/bin" / ("agent-bridge" + suffix),
        ):
            result = subprocess.run(
                command_for(script), env={**env, "AGENT_BRIDGE_NO_SELFPROVISION": "1",
                                          "AGENT_BRIDGE_PINNED_CONTEXT": "{}"},
                cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
            )
            assert result.returncode == 0 and "usage:" in result.stdout.lower(), result.stdout + result.stderr
        invalid = subprocess.run(
            command[:-1] + ["invalid-command"], env=env, cwd=project,
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert invalid.returncode == 2 and invalid.stderr, invalid.stdout + invalid.stderr
        assert status(root=root) == history and completion.read_bytes() == before
    finally:
        # Settle only this fixture's deliberately retained pending control.
        assert [item.id for item in status(root=root) if item.state == "pending"] == [pending.id]
        drain(root=root, executor=lambda _transaction: 0)
    assert_no_outer_owner(installation)


@native_genesis
@pytest.mark.parametrize("entry", [
    "payload", "primary", "ordinary",
    pytest.param("cmd", marks=pytest.mark.skipif(os.name != "nt", reason="Windows cmd")),
])
def test_bridge_command_original_operation_survives_caller_loss(
    materialized_bridge, area, native_recipe_runner, native_window_observation, entry,
):
    source, installation, project, command = owning_command(
        materialized_bridge, entry, qualified=entry != "ordinary",
    )
    if entry in {"primary", "cmd"}:
        materialized_bridge[0].rename(materialized_bridge[0].with_name("unavailable-installed-payload"))
    env = command_environment(installation, project, area, entry)
    adapter = bridge_service_adapter(env)
    root = installation.parent / ".copilot-extensions/update-driver"
    diagnostic = diagnostic_for(root, area)
    caller, log = native_recipe_runner.start(
        command, env=env, cwd=project, root=root, diagnostic=diagnostic, contain_caller=True,
    )
    observe(lambda: foundation_status(root=root) is not None, 120, diagnostic)
    assert foundation_status(root=root).state == "running", diagnostic()
    native_recipe_runner.lose_caller(caller)
    # No follower may replace an unaccepted operation or hide loss of its owner.
    def original_request_completed():
        operation = foundation_status(root=root)
        if operation is not None and operation.state == "completed":
            assert operation.exit_code == 0, diagnostic()
        logs = list(root.glob("first-use-*.log"))
        assert len(logs) <= 1, diagnostic()
        if native_recipe_runner.owners_finished():
            assert logs and receipt_completed_count(root, 1, logs[0]), diagnostic()
        return bool(logs) and receipt_completed_count(root, 1, logs[0])

    observe(original_request_completed, 900, diagnostic)
    result, = status(root=root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert result.spec.argv == ()
    admitted = json.loads(result.spec.source)
    assert (admitted["payload"], admitted["runtime_root"], admitted["project"]) == (
        str(source), str(installation), str(project),
    )
    cue = log.read_text(errors="replace")
    assert "::agent-provisioning:: plugin=agent-bridge reason=first-use status=" in cue
    assert str(root) in cue and "eta_seconds" not in cue
    assert_bridge_registration(installation, adapter)
    assert_installed_bridge(source, installation, project, env, command, 1)


@native_genesis
def test_bridge_concurrent_commands_observe_one_ordinary_request(
    materialized_bridge, area, native_recipe_runner, native_window_observation,
):
    source, installation, project, command = owning_command(materialized_bridge, "payload")
    env = command_environment(installation, project, area, "payload")
    adapter = bridge_service_adapter(env)
    root = installation.parent / ".copilot-extensions/update-driver"
    diagnostic = diagnostic_for(root, area)
    first, _ = native_recipe_runner.start(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    second, _ = native_recipe_runner.start(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    assert first.wait(timeout=900) == second.wait(timeout=900) == 0, diagnostic()
    result, = status(root=root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert_bridge_registration(installation, adapter)
    assert_installed_bridge(source, installation, project, env, command, 1)


@native_genesis
def test_bridge_command_failed_candidate_is_not_selected(
    materialized_bridge, area, native_recipe_runner, native_window_observation,
):
    source, installation, project, command = owning_command(materialized_bridge, "payload")
    (source / "src/agent_bridge/__init__.py").write_text(
        'raise RuntimeError("fixture failed installed Bridge candidate")\n', encoding="utf-8",
    )
    installation.mkdir(exist_ok=True)
    previous = {name: b"retained historical command record\n" for name in OUTER_RECORDS}
    for name, content in previous.items():
        (installation / name).write_bytes(content)
    env = command_environment(installation, project, area, "payload")
    bridge_service_adapter(env)
    root = installation.parent / ".copilot-extensions/update-driver"
    diagnostic = diagnostic_for(root, area)
    result = native_recipe_runner.run(
        command, env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert result.returncode != 0, diagnostic()
    failed, = status(root=root)
    assert failed.state == "completed" and failed.exit_code != 0, diagnostic()
    assert not (installation / "current-version").exists()
    assert not (installation / "last-known-good").exists()
    assert not list((installation / "versions").glob("*/.install-complete.json"))
    assert_no_outer_owner(installation, previous)


@native_genesis
def test_bridge_command_cold_wait_preserves_native_transport(
    materialized_bridge, area, native_recipe_runner, native_window_observation,
):
    """Synthetic application protocol, real queued construction and native fronts."""
    payload = materialized_bridge[0]
    (payload / "src/agent_bridge/__main__.py").write_text(
        """import json
import struct
import sys

if sys.argv[1:] == ["--help"]:
    print("usage: synthetic Bridge transport consumer")
    raise SystemExit(0)
if sys.argv[-2:] == ["--prompt-file", "-"]:
    sys.stdout.buffer.write(sys.stdin.buffer.read())
    sys.stdout.buffer.flush()
else:
    sys.stdout.buffer.write(struct.pack("!I", 5) + b"ready")
    sys.stdout.buffer.flush()
    while True:
        header = sys.stdin.buffer.read(4)
        if not header:
            break
        if len(header) != 4:
            raise EOFError("partial header")
        remaining = struct.unpack("!I", header)[0]
        frame = bytearray()
        while len(frame) < remaining:
            part = sys.stdin.buffer.read(remaining - len(frame))
            if not part:
                raise EOFError("partial body")
            frame.extend(part)
        sys.stdout.buffer.write(header + frame)
        sys.stdout.buffer.flush()
sys.stderr.write("transport-arguments:" + json.dumps(sys.argv[1:]) + "\\n")
raise SystemExit(17)
""", encoding="utf-8",
    )
    source, installation, project, primary = owning_command(materialized_bridge, "primary")
    env = command_environment(installation, project, area, "cmd" if os.name == "nt" else "primary")
    bridge_service_adapter(env)
    root = installation.parent / ".copilot-extensions/update-driver"
    diagnostic = diagnostic_for(root, area)
    suffix = ".ps1" if os.name == "nt" else ""
    binary_arguments = ["carrier", "--stdio", 'quoted "body" & (not a command)', "tail\\"]
    binary_command = command_for(source / "bin" / ("agent-bridge" + suffix))[:-1] + binary_arguments
    if os.name == "nt":
        binary_command = [
            os.environ["ComSpec"], "/d", "/c", str(Path(primary[-2]).with_suffix(".cmd")),
            *binary_arguments,
        ]
    binary, binary_log = native_recipe_runner.start(
        binary_command,
        env=env, cwd=project, root=root, diagnostic=diagnostic,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    assert not (installation / "current-version").exists(), diagnostic()
    prompt_arguments = ["send", 'quoted "name" & (not a command)', "--prompt-file", "-"]
    prompt_command = primary[:-1] + prompt_arguments
    if os.name == "nt":
        host = shutil.which("pwsh")
        assert host is not None
        prompt_command = [
            host, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(source / "bin/agent-bridge.ps1"), *prompt_arguments,
        ]
    prompt, prompt_log = native_recipe_runner.start(
        prompt_command, env=env, cwd=project, root=root, diagnostic=diagnostic,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    prompt_bytes = b'quoted "body" & (not a command)\nsecond line'
    objects = objects_log = None
    if os.name == "nt":
        shim = str(Path(primary[-2])).replace("'", "''")
        invocation = (
            "'quoted \"body\" & (not a command)\nsecond line' | & '" + shim +
            "' send 'quoted \"name\" & (not a command)' --prompt-file -; exit $LASTEXITCODE"
        )
        host = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        objects, objects_log = native_recipe_runner.start(
            [str(host), "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(invocation.encode("utf-16-le")).decode("ascii")],
            env=env, cwd=project, root=root, diagnostic=diagnostic, stdout=subprocess.PIPE,
        )
    frame = struct.pack("!I", 65536) + bytes(range(256)) * 256
    expected = struct.pack("!I", 5) + b"ready" + frame
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending_prompt = pool.submit(prompt.communicate, prompt_bytes, timeout=900)
        pending_objects = (
            pool.submit(objects.communicate, timeout=900) if objects is not None else None
        )
        pending_frame = pool.submit(binary.stdout.read, len(expected))
        binary.stdin.write(frame)
        binary.stdin.flush()
        assert pending_frame.result(timeout=900) == expected, diagnostic()
        assert binary.poll() is None, diagnostic()
        binary.stdin.close()
        binary.stdin = None
        stdout, _ = binary.communicate(timeout=30)
        assert stdout == b"" and binary.returncode == 17, diagnostic()
        stdout, _ = pending_prompt.result(timeout=900)
        assert stdout == prompt_bytes and prompt.returncode == 17, diagnostic()
        if pending_objects is not None:
            stdout, _ = pending_objects.result(timeout=900)
            assert stdout == prompt_bytes + os.linesep.encode() and objects.returncode == 17, diagnostic()
    logs = [(binary_log, binary_arguments), (prompt_log, prompt_arguments)]
    if objects_log is not None:
        logs.append((objects_log, prompt_arguments))
    for log, arguments in logs:
        lines = log.read_text(errors="replace").splitlines()
        records = [json.loads(line.removeprefix("transport-arguments:"))
                   for line in lines if line.startswith("transport-arguments:")]
        assert records == [arguments], diagnostic()
        assert any(line.startswith("::agent-provisioning::") for line in lines), diagnostic()
    result, = status(root=root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert_no_outer_owner(installation)
