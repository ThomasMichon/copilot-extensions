"""Actor-free full-command selection for the legacy attachment branches."""

from __future__ import annotations

import base64
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from .test_vault_mutation_selection import ordinary as ordinary
from .test_vault_read_selection import PACKAGE, peer as peer, production_functions

attachments = importlib.import_module(f"{PACKAGE}.legacy_attachment_cli")
selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")
invocation = importlib.import_module(f"{PACKAGE}.invocation")


@pytest.fixture
def legacy(ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = ordinary
    files = {
        "fixture.pem": b"fixture-private", "fixture.pem.pub": b"fixture-public",
        "fixture-key": b"fixture-private", "fixture-key.pub": b"fixture-public",
    }
    state.files = files
    state.file_reads = []
    state.file_writes = []
    state.modes = []
    state.destination_exists = True

    class MemoryPath:
        def __init__(self, value: str | MemoryPath) -> None:
            self.value = str(value)

        def __str__(self) -> str:
            return self.value

        def __truediv__(self, name: str) -> MemoryPath:
            return MemoryPath(str(Path(self.value) / name))

        @property
        def name(self) -> str:
            return Path(self.value).name

        @property
        def suffix(self) -> str:
            return Path(self.value).suffix

        def with_suffix(self, suffix: str) -> MemoryPath:
            return MemoryPath(str(Path(self.value).with_suffix(suffix)))

        def exists(self) -> bool:
            return self.value in state.files

        def is_dir(self) -> bool:
            return state.destination_exists and self.value == "destination"

        def read_bytes(self) -> bytes:
            state.file_reads.append(self.value)
            return files[self.value]

        def write_bytes(self, value: bytes) -> int:
            state.file_writes.append((self.value, value))
            return len(value)

        def chmod(self, mode: int) -> None:
            state.modes.append((self.value, mode))

    monkeypatch.setattr(attachments, "Path", MemoryPath)
    original = state.cli.send_command

    def send(request: dict[str, Any], timeout: float | None = 5.0, **options: Any) -> Any:
        response = original(request, timeout, **options)
        if (
            request.get("action") == "export-key"
            and isinstance(response, dict) and response.get("ok") is True
        ):
            response.update(
                key_data=base64.b64encode(b"fixture-private").decode(),
                pub_data=base64.b64encode(b"fixture-public").decode(),
            )
        return response

    monkeypatch.setattr(state.cli, "send_command", send)
    state.cli.IS_WINDOWS = False
    production_functions("cli", {"cmd_import_key", "cmd_export_key"}, state.cli.__dict__)
    return state


def arguments() -> SimpleNamespace:
    return SimpleNamespace(
        entry="direct", key_file="fixture.pem", dest_dir="destination",
        key_name="fixture.pem", create_only=False, exclusive=False, require_running=False,
    )


def test_legacy_attachment_commands_retain_payload_files_source_and_existing_pings(
    legacy: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    args = arguments()
    for key_file in ("fixture.pem", "fixture-key"):
        args.key_file = key_file
        legacy.requests.clear()
        assert legacy.cli.cmd_import_key(args) == 0
        output = capsys.readouterr()
        assert output.out == "fixture-created\n" and not output.err
        assert [request["action"] for _, request, _ in legacy.requests] == [
            "ping", "ping", "import-key",
        ]
        request = legacy.requests[-1][1]
        assert request[selection.FIELD]["root"] == legacy.root
        assert request[selection.FIELD]["native_default"] is True
        assert request["kpdb"] == legacy.context.kpdb and request["entry"] == "direct"
        assert request["key_name"] == key_file
        assert base64.b64decode(request["key_data"]) == b"fixture-private"
        assert base64.b64decode(request["pub_data"]) == b"fixture-public"
    legacy.requests.clear()
    assert legacy.cli.cmd_export_key(args) == 0
    output = capsys.readouterr()
    assert output.out == "Exported fixture.pem to destination\n" and not output.err
    assert [request["action"] for _, request, _ in legacy.requests] == [
        "ping", "ping", "export-key",
    ]
    assert legacy.requests[-1][1][selection.FIELD]["root"] == legacy.root
    assert legacy.file_writes == [
        (str(Path("destination") / "fixture.pem"), b"fixture-private"),
        (str(Path("destination") / "fixture.pem.pub"), b"fixture-public"),
    ]
    assert legacy.modes == [
        (str(Path("destination") / "fixture.pem"), 0o600),
        (str(Path("destination") / "fixture.pem.pub"), 0o644),
    ]
    assert invocation.configuration_root() is None


def test_legacy_attachment_terminal_unlock_and_file_operation_keep_original_recipient(
    legacy: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    legacy.unlocked = False
    legacy.unlock_mode = "password"
    legacy.redirect_on_prompt = True
    assert legacy.cli.cmd_export_key(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "Exported fixture.pem to destination\n" and not output.err
    assert [request["action"] for _, request, _ in legacy.requests] == [
        "ping", "ping", "unlock", "unlock", "export-key",
    ]
    assert all(port == 19071 and root == legacy.root for port, _, root in legacy.requests)
    assert all(
        request[selection.FIELD]["root"] == legacy.root
        for _, request, _ in legacy.requests[2:]
    )
    assert len(legacy.password_reads) == 1 and len(legacy.file_writes) == 2
    assert invocation.configuration_root() is None


def test_legacy_attachment_requires_its_action_not_old_ordinary_mutation_support(
    legacy: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    legacy.mutation_actions = ["add", "set-username", "copy", "get", "has"]
    for command in (legacy.cli.cmd_import_key, legacy.cli.cmd_export_key):
        assert command(arguments()) == 0
        output = capsys.readouterr()
        assert output.out and "unsupported" in output.err
        assert selection.FIELD not in legacy.requests[-1][1]


def test_legacy_attachment_unknown_outcomes_never_write_files_print_success_or_replay(
    legacy: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for mode in ("lost", "invalid", "refused"):
        legacy.mutation_mode = mode
        for command, action in (
            (legacy.cli.cmd_import_key, "import-key"), (legacy.cli.cmd_export_key, "export-key"),
        ):
            legacy.requests.clear()
            assert command(arguments()) == 1
            output = capsys.readouterr()
            assert not output.out and "Error:" in output.err and "must-not-print" not in output.err
            assert [request["action"] for _, request, _ in legacy.requests] == ["ping", "ping", action]
            assert not legacy.file_writes and not legacy.modes
            assert invocation.configuration_root() is None


def test_legacy_attachment_optional_receipt_and_pinned_provenance_preserve_success(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for receipt, state in ((False, "idle"), (True, {})):
        legacy.receipt, legacy.warm_state = receipt, state
        legacy.requests.clear()
        legacy.file_writes.clear()
        assert legacy.cli.cmd_export_key(arguments()) == 0
        output = capsys.readouterr()
        assert output.out and "not confirmed" in output.err
        assert len(legacy.file_writes) == 2
        assert [request["action"] for _, request, _ in legacy.requests] == [
            "ping", "ping", "export-key",
        ]
    monkeypatch.setenv("VAULT_READ_TEST_PIN", "immutable-fixture-context")
    owner_reads = legacy.owner_reads
    legacy.requests.clear()
    assert legacy.cli.cmd_import_key(arguments()) == 0
    output = capsys.readouterr()
    assert output.out == "fixture-created\n" and "unsupported" in output.err
    assert legacy.owner_reads == owner_reads
    assert all(selection.FIELD not in request for _, request, _ in legacy.requests)
    assert invocation.configuration_root() is None


def test_legacy_attachment_unknown_unlock_stops_before_reading_input_or_writing_files(
    legacy: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    for mode in ("lost", "invalid", "refused"):
        legacy.unlocked = False
        legacy.unlock_mode = mode
        for command in (legacy.cli.cmd_import_key, legacy.cli.cmd_export_key):
            legacy.requests.clear()
            assert command(arguments()) == 1
            output = capsys.readouterr()
            assert not output.out and "Error:" in output.err
            assert [request["action"] for _, request, _ in legacy.requests] == ["ping", "ping", "unlock"]
    assert not legacy.file_reads and not legacy.file_writes and not legacy.password_reads
    assert invocation.configuration_root() is None


def test_legacy_attachment_input_validation_precedes_context_selection_and_activation(
    legacy: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    args = arguments()
    args.key_file = "missing"
    assert legacy.cli.cmd_import_key(args) == 1
    assert "key file not found" in capsys.readouterr().err
    legacy.files.pop("fixture.pem.pub")
    args.key_file = "fixture.pem"
    assert legacy.cli.cmd_import_key(args) == 1
    assert "public key not found" in capsys.readouterr().err
    legacy.destination_exists = False
    assert legacy.cli.cmd_export_key(args) == 1
    assert "destination directory not found" in capsys.readouterr().err
    assert not legacy.requests and legacy.owner_reads == 0 and not legacy.file_reads


def test_nonactivating_attachment_branches_do_not_enter_ordinary_scope(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    for module_name in ("attachment_import_cli", "attachment_export_cli"):
        module = ModuleType(f"{PACKAGE}.{module_name}")

        def command(_args: Any, name: str = module_name) -> int:
            calls.append(name)
            return 7

        module.__dict__["command"] = command
        monkeypatch.setitem(sys.modules, module.__name__, module)
    args = arguments()
    args.create_only = True
    assert legacy.cli.cmd_import_key(args) == 7
    args.exclusive = True
    assert legacy.cli.cmd_export_key(args) == 7
    assert calls == ["attachment_import_cli", "attachment_export_cli"]
    args.create_only = args.exclusive = False
    args.require_running = True
    assert legacy.cli.cmd_import_key(args) == legacy.cli.cmd_export_key(args) == 1
    assert "requires" in capsys.readouterr().err
    assert not legacy.requests and legacy.owner_reads == 0
    assert not legacy.file_reads and not legacy.file_writes
