"""Join the Worktrees argument producer to Bridge's consumer without process I/O."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    package_name = "promotion_launch_worktrees"
    source = ROOT / "plugins/agent-worktrees/src/agent_worktrees"
    package = ModuleType(package_name)
    package.__path__ = [str(source)]
    monkeypatch.setitem(sys.modules, package_name, package)
    installer = ModuleType(f"{package_name}.installer")
    installer.install_dir = lambda: tmp_path
    monkeypatch.setitem(sys.modules, installer.__name__, installer)

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    load(f"{package_name}.native_arguments", source / "native_arguments.py")
    producer = load(f"{package_name}.process_launcher", source / "process_launcher.py")
    consumer = load(
        "promotion_launch_bridge",
        ROOT / "plugins/agent-bridge/src/agent_bridge/local_launch_arguments.py",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(consumer, "_worktrees_setup_script", lambda: scripts / "default-setup.ps1")
    return producer, consumer


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("mode", ["fresh", "resume", "literal-tail", "terminator-as-value"])
@pytest.mark.parametrize("shape", ["direct", "wrapped", "deferred", "prepared"])
def test_owner_produced_setup_preserves_native_and_setup_arguments(boundary, tmp_path, shell, mode, shape):
    producer, consumer = boundary
    native = ["--model", "configured-model"] if mode == "fresh" else ["--resume=original-head"]
    if mode == "terminator-as-value":
        native += ["--name", "--"]
    tail = ["--", "literal-tail"] if mode in {"literal-tail", "terminator-as-value"} else []
    setup = [
        shell, "-NoProfile", "-File", str(tmp_path / "scripts/default-setup.ps1"),
        "-Recovery", "-ExecutionDirectory", str(tmp_path / "workspace with spaces"),
        "-CopilotPath", str(tmp_path / "backend.ps1"),
    ]
    template = [*setup, *native, *tail]
    original_template = list(template)
    command = producer.quote_powershell_command(template, RuntimeError)
    record = tmp_path / "records/test.yaml"
    if shape == "prepared":
        copied = record.parent / "launch-attempts" / ("a" * 32) / ("b" * 32 + ".shell")
        command[3:4] = [
            str(copied / "default-setup.ps1"),
            "-LaunchSeedPreparation", "admission", "-LaunchSeedNativeRoot", str(tmp_path),
            "-LaunchSeedRecord", str(record),
        ]
    if shape != "direct":
        wrapper = (copied if shape == "prepared" else tmp_path / "scripts") / "launch-command.ps1"
        command = [shell, "-NoProfile", "-File", str(wrapper), "--recovery", "--", *command]
    if shape == "deferred":
        command = [
            "python", "-m", "agent_worktrees.launch_seed_exec", "--record", str(record),
            "--native-resume-session", "--", "--admission-only", "--", *command,
        ]
    original_command = list(command)
    additions = [
        "--acp", "--stdio", "--no-auto-update", "--plugin-dir", "prepared path",
        "--name", "literal ' \" $value; &", "--prompt", "", "--prompt", "--resume=not-an-option",
    ]
    original_additions = list(additions)

    result = consumer.append_local_launch_arguments(command, additions, record_path=str(record))

    assert result[:-2] == command[:-2]
    assert result[-2] == "-NativeArgumentsJson"
    assert json.loads(result[-1]) == {"args": [*native, *additions, *tail]}
    assert command == original_command
    assert template == original_template
    assert additions == original_additions
    assert json.loads(command[-1]) == {"args": [*native, *tail]}


@pytest.mark.parametrize("mismatch", [
    "missing-record", "relative-record", "other-record", "other-root", "other-attempt",
    "invalid-attempt", "invalid-shell", "wrong-mode", "custom-wrapper", "short-controls",
])
def test_unowned_prepared_launch_plan_retains_append_semantics(boundary, tmp_path, mismatch):
    _, consumer = boundary
    record = tmp_path / "records/test.yaml"
    copied = record.parent / "launch-attempts" / ("a" * 32) / ("b" * 32 + ".shell")
    wrapper = copied / "launch-command.ps1"
    script = copied / "default-setup.ps1"
    selected_record = str(record)
    root = tmp_path
    mode = "admission"
    if mismatch == "missing-record":
        selected_record = None
    elif mismatch == "relative-record":
        selected_record = "records/test.yaml"
    elif mismatch == "other-record":
        selected_record = str(record.with_name("other.yaml"))
    elif mismatch == "other-root":
        root = tmp_path / "other-installation"
    elif mismatch == "other-attempt":
        script = copied.with_name("c" * 32 + ".shell") / script.name
    elif mismatch == "invalid-attempt":
        script = copied.parent.parent / "custom" / copied.name / script.name
        wrapper = script.with_name("launch-command.ps1")
    elif mismatch == "invalid-shell":
        script = copied.with_name("custom.shell") / script.name
        wrapper = script.with_name("launch-command.ps1")
    elif mismatch == "wrong-mode":
        mode = "custom"
    elif mismatch == "custom-wrapper":
        wrapper = tmp_path / "custom/launch-command.ps1"
    controls = [
        "-LaunchSeedPreparation", mode, "-LaunchSeedNativeRoot", str(root),
        "-LaunchSeedRecord", str(record),
    ]
    if mismatch == "short-controls":
        # A marker-looking value is not proof that the required controls exist.
        controls = ["-LaunchSeedPreparation", "seed", "-LaunchSeedNativeRoot", "-NativeArgumentsJson"]
    command = [
        "pwsh", "-File", str(wrapper), "--", "pwsh", "-File", str(script),
        *controls,
    ]
    if mismatch != "short-controls":
        command += ["-NativeArgumentsJson", '{"args":[]}']
    assert consumer.append_local_launch_arguments(
        command, ["--acp"], record_path=selected_record,
    ) == [*command, "--acp"]


@pytest.mark.parametrize("prefix", [
    ["python", "-m", "custom.launch", "--record", "same", "--"],
    ["python", "-m", "agent_worktrees.launch_seed_exec", "--record", "other", "--"],
    ["python", "-m", "agent_worktrees.launch_seed_exec", "--record", "same", "--custom", "--"],
    ["python", "-m", "agent_worktrees.launch_seed_exec", "--record", "same", "--seed-id", "--"],
])
def test_unrecognized_deferred_prefix_keeps_inner_envelope_opaque(boundary, tmp_path, prefix):
    _, consumer = boundary
    record = tmp_path / "record.yaml"
    command = [
        *(str(record) if value == "same" else value for value in prefix),
        "pwsh", "-File", str(tmp_path / "scripts/default-setup.ps1"),
        "-NativeArgumentsJson", '{"args":[]}',
    ]
    assert consumer.append_local_launch_arguments(
        command, ["--acp"], record_path=str(record),
    ) == [*command, "--acp"]


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_owner_produced_envelope_corruption_is_not_legacy_fallback(boundary, tmp_path, shell):
    producer, consumer = boundary
    command = producer.quote_powershell_command(
        [shell, "-File", str(tmp_path / "scripts/default-setup.ps1"), "--resume=original-head"],
        RuntimeError,
    )
    command[-1] = '{"args":"not-an-array"}'
    with pytest.raises(RuntimeError, match="Invalid worktree native argument envelope"):
        consumer.append_local_launch_arguments(command, ["--acp", "--stdio"])


@pytest.mark.parametrize("command", [
    ["copilot", "--resume=original-head"],
    ["bash", "/launch/default-setup.sh", "--recovery"],
], ids=["direct", "bash-setup"])
def test_other_owner_launch_forms_preserve_append_semantics(boundary, command):
    producer, consumer = boundary
    additions = ["--acp", "--stdio", "--no-auto-update"]
    assert consumer.append_local_launch_arguments(
        producer.quote_powershell_command(command, RuntimeError), additions,
    ) == [*command, *additions]


@pytest.mark.parametrize("payload", ["opaque", '{"args":["custom-data"]}'])
def test_same_named_custom_script_remains_opaque(boundary, tmp_path, payload):
    producer, consumer = boundary
    script = tmp_path / "custom/default-setup.ps1"
    script.parent.mkdir()
    script.write_text(
        "# agent-worktrees:launch-seed-boundary-v1\nparam([string]$NativeArgumentsJson)\n",
        encoding="utf-8",
    )
    command = ["pwsh", "-File", str(script), "-NativeArgumentsJson", payload]
    assert producer.quote_powershell_command(command, RuntimeError) == command
    assert consumer.append_local_launch_arguments(command, ["--acp"]) == [*command, "--acp"]


def test_adapter_vocabulary_matches_owner(boundary):
    producer, consumer = boundary
    assert consumer.NATIVE_VALUE_OPTIONS == producer._NATIVE_VALUE_OPTIONS
    for option in sorted(consumer.NATIVE_VALUE_OPTIONS):
        assert consumer._insert_native_options(
            [option, "--", "--", "literal-tail"], ["--stdio"],
        ) == [option, "--", "--stdio", "--", "literal-tail"]
