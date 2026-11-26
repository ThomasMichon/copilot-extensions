"""Opt-in observations of the registered sender, never native acceptance."""

from __future__ import annotations

import importlib
import json
import os
import secrets
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from io import BufferedIOBase
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

import pytest

pytest_plugins = ("test_post_tool_shell",)


class PreparedFixture(TypedDict):
    payload_root: Path
    harness: Path
    sidecar: Path


class PostToolFixture(Protocol):
    test_registered_prepared_cell_post_tool_uses_selected_original_owners: Callable[
        [PreparedFixture, pytest.MonkeyPatch, str, str], None
    ]


class FrameObservation(TypedDict):
    size: int
    framing: str
    json: str
    version: bool
    auth: str
    kind: str


def classify_frame(raw: bytes, token: str) -> FrameObservation:
    observation: FrameObservation = {
        "size": len(raw), "framing": "newline" if raw.endswith(b"\n") else "incomplete",
        "json": "invalid", "version": False, "auth": "unchecked", "kind": "unchecked",
    }
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return observation
    if not isinstance(value, dict):
        observation["json"] = "nonobject"
        return observation
    request = cast(dict[str, object], value)
    observation["json"] = "object"
    observation["version"] = request.get("version") == 1
    try:
        authenticated = secrets.compare_digest(str(request.get("token") or ""), token)
    except TypeError:
        observation["auth"] = "invalid"
    else:
        observation["auth"] = "match" if authenticated else "mismatch"
    kind = str(request.get("kind") or "")
    observation["kind"] = kind if kind in {
        "readHookDemand", "nativeToolMutation", "nativeToolBatchMutation",
        "shellToolMutation", "mixedToolBatchMutation", "worktreeChanged",
    } else "other"
    return observation


class FrameReader(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


class TokenServer(Protocol):
    token: str


@dataclass
class ReceiverObservation:
    accepted: int = 0
    read_errors: int = 0
    handler_errors: int = 0
    dispatch_calls: int = 0
    dispatch_errors: int = 0
    frames: list[FrameObservation] = field(default_factory=list)


class ObservedReader:
    def __init__(self, original: FrameReader, observation: ReceiverObservation) -> None:
        self.original = original
        self.observation = observation
        self.returned: list[bytes] = []

    def readline(self, size: int = -1, /) -> bytes:
        try:
            raw = self.original.readline(size)
        except BaseException:
            self.observation.read_errors += 1
            raise
        self.returned.append(raw)
        return raw


def install_receiver_observer(
    monkeypatch: pytest.MonkeyPatch, observation: ReceiverObservation,
) -> None:
    from agent_worktrees import hook_ipc

    original_handle = hook_ipc._Handler.handle
    original_dispatch = hook_ipc.HookIpcServer._on_read_demand

    def handle(handler: hook_ipc._Handler) -> None:
        observation.accepted += 1
        original_reader = handler.rfile
        reader = ObservedReader(original_reader, observation)
        handler.rfile = cast(BufferedIOBase, reader)
        try:
            original_handle(handler)
        except BaseException:
            observation.handler_errors += 1
            raise
        finally:
            handler.rfile = original_reader
            for raw in reader.returned:
                observation.frames.append(classify_frame(raw, cast(TokenServer, handler.server).token))

    def dispatch(server: hook_ipc.HookIpcServer, request: dict[str, object]) -> None:
        observation.dispatch_calls += 1
        try:
            original_dispatch(server, request)
        except BaseException:
            observation.dispatch_errors += 1
            raise

    monkeypatch.setattr(hook_ipc._Handler, "handle", handle)
    monkeypatch.setattr(hook_ipc.HookIpcServer, "_on_read_demand", dispatch)


def report_receiver_observation(
    observation: ReceiverObservation, fixture: PreparedFixture,
) -> None:
    from dataclasses import asdict

    print("receiver_observation=" + json.dumps({
        **asdict(observation), "sidecar_exists": fixture["sidecar"].exists(),
    }, sort_keys=True))


class ReceiverPacketObserver:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        if (
            not isinstance(item, pytest.Function)
            or item.path.name != "test_post_tool_shell.py"
            or item.originalname
            != "test_registered_prepared_cell_post_tool_uses_selected_original_owners"
            or item.callspec.params.get("case") != "read"
        ):
            yield
            return
        fixture = cast(PreparedFixture, item.funcargs["prepared_read_event"])
        monkeypatch = cast(pytest.MonkeyPatch, item.funcargs["monkeypatch"])
        observation = ReceiverObservation()
        install_receiver_observer(monkeypatch, observation)
        try:
            yield
        finally:
            report_receiver_observation(observation, fixture)


@pytest.mark.guard
@pytest.mark.parametrize(("filename", "originalname", "case"), [
    ("test_post_tool_shell.py",
     "test_registered_prepared_cell_post_tool_uses_selected_original_owners", "native"),
    ("test_post_tool_sender_diagnostics.py",
     "test_registered_prepared_cell_post_tool_uses_selected_original_owners", "read"),
    ("test_post_tool_shell.py", "test_registered_read_demand_is_send_only", "read"),
])
def test_packet_receiver_observer_ignores_other_original_items(
    filename: str, originalname: str, case: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    def unexpected_install(*args: object) -> None:
        pytest.fail("A different original item must remain uninstrumented")

    monkeypatch.setattr(
        install_receiver_observer.__module__ + ".install_receiver_observer", unexpected_install,
    )
    item = cast(pytest.Function, Mock(
        spec=pytest.Function, path=Path(filename), originalname=originalname,
        callspec=SimpleNamespace(params={"case": case}),
    ))
    wrapper = ReceiverPacketObserver().pytest_runtest_call(item)
    assert next(wrapper) is None
    with pytest.raises(StopIteration):
        next(wrapper)


@pytest.mark.guard
@pytest.mark.parametrize("fails", [False, True])
def test_packet_receiver_observer_delegates_original_outcome(
    fails: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    installed: list[ReceiverObservation] = []

    def install(selected: pytest.MonkeyPatch, observation: ReceiverObservation) -> None:
        assert selected is monkeypatch
        installed.append(observation)

    monkeypatch.setattr(
        install_receiver_observer.__module__ + ".install_receiver_observer", install,
    )
    fixture: PreparedFixture = {
        "payload_root": tmp_path, "harness": tmp_path / "harness.ps1",
        "sidecar": tmp_path / "sidecar.json",
    }
    item = cast(pytest.Function, Mock(
        spec=pytest.Function, path=Path("test_post_tool_shell.py"),
        originalname="test_registered_prepared_cell_post_tool_uses_selected_original_owners",
        callspec=SimpleNamespace(params={"case": "read"}),
        funcargs={"prepared_read_event": fixture, "monkeypatch": monkeypatch},
    ))
    wrapper = ReceiverPacketObserver().pytest_runtest_call(item)
    assert next(wrapper) is None
    assert len(installed) == 1
    installed[0].accepted = 1
    if fails:
        failure = AssertionError("private-original-failure")
        with pytest.raises(AssertionError) as caught:
            wrapper.throw(failure)
        assert caught.value is failure
    else:
        with pytest.raises(StopIteration):
            next(wrapper)
    output = capsys.readouterr().out
    assert output.startswith("receiver_observation=")
    assert json.loads(output.partition("=")[2]) == {
        "accepted": 1, "read_errors": 0, "handler_errors": 0, "dispatch_calls": 0,
        "dispatch_errors": 0, "frames": [], "sidecar_exists": False,
    }
    assert str(tmp_path) not in output
    assert "private" not in output


@pytest.mark.guard
def test_receiver_observer_preserves_reader_return_and_exception() -> None:
    raw = b"private-frame\n"
    failure = OSError("private-error")
    sizes: list[int] = []

    class Reader:
        def readline(self, size: int = -1, /) -> bytes:
            sizes.append(size)
            if len(sizes) == 1:
                return raw
            raise failure

    observation = ReceiverObservation()
    reader = ObservedReader(Reader(), observation)
    assert reader.readline(2048) is raw
    with pytest.raises(OSError) as caught:
        reader.readline(4096)
    assert caught.value is failure
    assert sizes == [2048, 4096]
    assert reader.returned == [raw]
    assert observation.read_errors == 1


@pytest.mark.guard
def test_receiver_observer_restores_reader_on_handler_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from agent_worktrees import hook_ipc

    failure = OSError("private-error")
    original_reader = BytesIO(b"private-frame\n")
    handler = cast(hook_ipc._Handler, SimpleNamespace(
        rfile=original_reader, server=SimpleNamespace(token="private-token"),
    ))

    def handle(selected: hook_ipc._Handler) -> None:
        assert selected.rfile.readline(2048) == b"private-frame\n"
        raise failure

    monkeypatch.setattr(hook_ipc._Handler, "handle", handle)
    observation = ReceiverObservation()
    install_receiver_observer(monkeypatch, observation)
    with pytest.raises(OSError) as caught:
        hook_ipc._Handler.handle(handler)
    assert caught.value is failure
    assert handler.rfile is original_reader
    assert not original_reader.closed
    assert observation.accepted == observation.handler_errors == 1
    assert observation.frames == [classify_frame(b"private-frame\n", "private-token")]
    original_reader.close()


@pytest.mark.guard
@pytest.mark.parametrize(("raw", "expected"), [
    (b"", ("incomplete", "invalid", False, "unchecked", "unchecked")),
    (b"[]\n", ("newline", "nonobject", False, "unchecked", "unchecked")),
    (b"\xff\n", ("newline", "invalid", False, "unchecked", "unchecked")),
    (b'{"version":2,"token":"wrong","kind":"private-kind"}\n',
     ("newline", "object", False, "mismatch", "other")),
    (b'{"version":1,"token":"private-token","kind":"readHookDemand","payload":"private-data"}\n',
     ("newline", "object", True, "match", "readHookDemand")),
])
def test_receiver_observer_redacts_frames(
    raw: bytes, expected: tuple[str, str, bool, str, str],
) -> None:
    observation = classify_frame(raw, "private-token")
    assert (
        observation["framing"], observation["json"], observation["version"],
        observation["auth"], observation["kind"],
    ) == expected
    assert observation["size"] == len(raw)
    assert "private" not in json.dumps(observation)
    assert "wrong" not in json.dumps(observation)


@pytest.mark.guard
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_notification_sender_preserves_real_clock_and_original_operations(newline: str) -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "hook-facts.ps1").read_text(
        encoding="utf-8",
    ).replace("\n", newline)
    observed = instrument_notification_sender(source)
    assert observed.count("[Diagnostics.Stopwatch]::StartNew()") == source.count(
        "[Diagnostics.Stopwatch]::StartNew()",
    )
    assert observed.count("$clock.ElapsedMilliseconds") == source.count("$clock.ElapsedMilliseconds")
    for operation in (
        "$connect = $client.ConnectAsync([Net.IPAddress]::Loopback, $port)",
        "$write = $client.GetStream().WriteAsync($bytes, 0, $bytes.Length)",
        "$connect.Wait($remaining)", "$write.Wait($remaining)", "$client.Dispose()",
    ):
        assert observed.count(operation) == source.count(operation)
    assert observed.count("$clock.Elapsed.") == source.count("$clock.Elapsed.")
    with pytest.raises(ValueError, match="original sender"):
        instrument_notification_sender(observed)


@pytest.mark.guard
def test_notification_receiver_bounds_classifications_without_retaining_frames() -> None:
    observation = NotificationReceiverObservation()
    raw = b'{"version":1,"token":"private-token","kind":"mixedToolBatchMutation","payload":"private-data"}\n'
    for _ in range(100):
        observation.record(raw, "private-token")
    body = observation.encode()
    decoded = json.loads(body)
    assert 0 < len(decoded["frames"]) <= 64
    assert decoded["truncated"] is True
    assert len(body.encode("utf-8")) <= 4096
    assert decoded["frames"][0]["auth"] == "match"
    assert decoded["frames"][0]["kind"] == "mixedToolBatchMutation"
    assert "private" not in body
    assert all(type(member) is dict for member in observation.frames)
    observation.accepted = observation.handler_started = 2**31 - 1
    observation.read_errors = observation.handler_errors = 2**31 - 1
    observation.dispatch_calls = observation.dispatch_errors = 2**31 - 1
    assert len(observation.encode().encode("utf-8")) <= 4096


@pytest.mark.guard
def test_notification_packet_selection_excludes_frozen_and_foreign_entries() -> None:
    from types import SimpleNamespace

    original = Path(__file__).with_name("test_post_tool_shell.py")
    for filename, name, expected in (
        (original, "test_registered_native_mutation_is_send_only", True),
        (original, "test_registered_read_demand_is_send_only", True),
        (original, "test_registered_prepared_cell_post_tool_uses_selected_original_owners", False),
        (Path("other") / original.name, "test_registered_native_mutation_is_send_only", False),
    ):
        item = cast(pytest.Function, SimpleNamespace(path=filename, originalname=name))
        assert NotificationPacketObserver.accepts(item) is expected


@pytest.mark.guard
def test_notification_packet_is_inert_without_its_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    monkeypatch.delenv("AGENT_WORKTREES_NOTIFICATION_DIAGNOSTIC", raising=False)
    item = Mock(spec=pytest.Function)
    wrapper = NotificationPacketObserver().pytest_runtest_call(cast(pytest.Function, item))
    assert next(wrapper) is None
    with pytest.raises(StopIteration):
        next(wrapper)
    assert item.mock_calls == []


@pytest.mark.guard
def test_notification_reader_preserves_original_bytes_errors_and_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from agent_worktrees import hook_ipc

    raw = b'{"version":1,"token":"private-token","kind":"readHookDemand"}\n'
    failure = OSError("private-error")
    sizes: list[int] = []

    class Reader:
        def readline(self, size: int = -1, /) -> bytes:
            sizes.append(size)
            if len(sizes) == 1:
                return raw
            raise failure

    reader = Reader()
    handler = cast(hook_ipc._Handler, SimpleNamespace(
        rfile=reader, server=SimpleNamespace(token="private-token"),
    ))

    def handle(selected: hook_ipc._Handler) -> None:
        assert selected.rfile.readline(65536) is raw
        selected.rfile.readline(32)

    monkeypatch.setattr(hook_ipc._Handler, "handle", handle)
    observation = NotificationReceiverObservation()
    observation.install(monkeypatch)
    with pytest.raises(OSError) as caught:
        hook_ipc._Handler.handle(handler)
    assert caught.value is failure
    assert cast(object, handler.rfile) is reader
    assert sizes == [65536, 32]
    assert observation.accepted == 0
    assert observation.handler_started == observation.read_errors == observation.handler_errors == 1
    assert observation.frames == [classify_frame(raw, "private-token")]
    assert "private" not in observation.encode()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(os.name != "nt", reason="Original Windows notification diagnostics control")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["current", "mixed-targets", "old-provider"])
def test_notification_packet_preserves_original_delivery(
    prepared_read_event: PreparedFixture, monkeypatch: pytest.MonkeyPatch,
    shell: str, case: str, capsys: pytest.CaptureFixture[str],
) -> None:
    from unittest.mock import Mock
    import test_post_tool_shell as original

    monkeypatch.setenv("AGENT_WORKTREES_NOTIFICATION_DIAGNOSTIC", "1")
    original_test = (
        original.test_registered_read_demand_is_send_only if case == "current"
        else original.test_registered_native_mutation_is_send_only
    )
    item = cast(pytest.Function, Mock(
        spec=pytest.Function, path=Path(original.__file__),
        originalname=original_test.__name__,
        funcargs={"prepared_read_event": prepared_read_event, "monkeypatch": monkeypatch},
    ))
    wrapper = NotificationPacketObserver().pytest_runtest_call(item)
    assert next(wrapper) is None
    try:
        original_test(cast(Any, prepared_read_event), shell, case)
    finally:
        with pytest.raises(StopIteration):
            next(wrapper)
    lines = capsys.readouterr().out.splitlines()
    sender = json.loads(next(
        line.removeprefix("notification_sender=")
        for line in lines if line.startswith("notification_sender=")
    ))
    receiver = json.loads(next(
        line.removeprefix("notification_receiver=")
        for line in lines if line.startswith("notification_receiver=")
    ))
    assert len(sender) <= 16
    assert sender["reason"] == (2 if case == "old-provider" else 10)
    assert receiver["accepted"] == (0 if case == "old-provider" else 1)
    assert receiver["handler_started"] == receiver["accepted"]
    if case != "old-provider":
        assert sender["write_complete"] is True
        assert receiver["dispatch_calls"] == 1
        assert receiver["frames"][0]["auth"] == "match"


def observe_after_entry(harness: Path, expression: str) -> None:
    """Append a bounded observation after the original entry has returned."""
    entry = harness.read_text(encoding="utf-8")
    original = ")); exit 0"
    assert entry.count(original) == 1
    harness.write_text(
        entry.replace(original, ")); " + expression + "; exit 0"), encoding="utf-8",
    )


def instrument_notification_sender(source: str) -> str:
    start = source.index("function Send-PostToolReadDemand")
    end = source.index("function Get-PostToolRuntimeRoot", start)
    original_sender = source[start:end]
    newline = "\r\n" if "\r\n" in original_sender else "\n"
    sender = original_sender.replace("\r\n", "\n")
    if "notificationSender" in sender or sender.count(
        "$clock = [Diagnostics.Stopwatch]::StartNew()",
    ) != 1:
        raise ValueError("Notification diagnostics require the original sender")

    def replace(original: str, replacement: str) -> None:
        nonlocal sender
        if sender.count(original) != 1:
            raise ValueError("Notification diagnostics require the original sender")
        sender = sender.replace(original, replacement)

    replace("    $client = $null", (
        "    $script:notificationSender = @{ phase=0; reason=0; r1=$null; r2=$null;"
        " r3=$null; r4=$null; error=0; hresult=0; socket_error=0; write_complete=$false }\n"
        "    $client = $null"
    ))
    replace("') { return }\n        $port =", (
        "') { $script:notificationSender.reason=2; return }\n        $port ="
    ))
    for original, reason in (
        ("if ($port -lt 1 -or $port -gt 65535) { return }", 3),
        ("if (-not [Net.Sockets.Socket]::OSSupportsIPv4) { return }", 4),
        ("if ($bytes.Length -gt $limit) { return }", 5),
        ("if ($remaining -le 0 -or -not $connect.Wait($remaining)) { return }", 7),
    ):
        replace(original, original.replace("{ return }", (
            "{ $script:notificationSender.reason=" + str(reason) + "; return }"
        )))
    replace("        $request = @{", "        $script:notificationSender.phase=1\n        $request = @{")
    replace("        $bytes = ", "        $script:notificationSender.phase=2\n        $bytes = ")
    replace(
        "        $client = [Net.Sockets.TcpClient]::new",
        "        $script:notificationSender.phase=3\n        $client = [Net.Sockets.TcpClient]::new",
    )
    budget = "        $remaining = 100 - [int]$clock.ElapsedMilliseconds"
    if sender.count(budget) != 4:
        raise ValueError("Notification diagnostics require the original sender")
    pieces = sender.split(budget)
    sender = pieces[0] + "".join(
        budget + f"\n        $script:notificationSender.r{index}=$remaining\n"
        f"        $script:notificationSender.phase={index + 3}" + piece
        for index, piece in enumerate(pieces[1:], 1)
    )
    empty_budget = "if ($remaining -le 0) { return }"
    if sender.count(empty_budget) != 3:
        raise ValueError("Notification diagnostics require the original sender")
    for reason in (6, 8, 9):
        sender = sender.replace(empty_budget, (
            "if ($remaining -le 0) { $script:notificationSender.reason="
            + str(reason) + "; return }"
        ), 1)
    replace("$null = $write.Wait($remaining)", (
        "$script:notificationSender.write_complete = $write.Wait($remaining)\n"
        "        $script:notificationSender.phase=8\n"
        "        $script:notificationSender.reason = if "
        "($script:notificationSender.write_complete) { 10 } else { 11 }"
    ))
    replace("    } catch {} finally {", r"""    } catch {
        $script:notificationSender.reason=12
        $failure = $_.Exception.GetBaseException()
        $script:notificationSender.hresult=$failure.HResult
        $script:notificationSender.error = if ($failure -is [Net.Sockets.SocketException]) { 1 }
            elseif ($failure -is [IO.IOException]) { 2 }
            elseif ($failure -is [InvalidOperationException]) { 3 }
            elseif ($failure -is [ArgumentException]) { 4 } else { 5 }
        if ($failure -is [Net.Sockets.SocketException]) {
            $script:notificationSender.socket_error=[int]$failure.SocketErrorCode
        }
    } finally {""")
    return source[:start] + sender.replace("\n", newline) + source[end:]


@dataclass
class NotificationReceiverObservation:
    accepted: int = 0
    handler_started: int = 0
    read_errors: int = 0
    handler_errors: int = 0
    dispatch_calls: int = 0
    dispatch_errors: int = 0
    frames: list[FrameObservation] = field(default_factory=list)
    truncated: bool = False

    def encode(self) -> str:
        from dataclasses import asdict

        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    def record(self, raw: bytes, token: str) -> None:
        if len(self.frames) >= 64 or self.truncated:
            self.truncated = True
            return
        self.frames.append(classify_frame(raw, token))
        if len(self.encode().encode("utf-8")) > 3968:
            self.frames.pop()
            self.truncated = True

    def increment(self, name: str) -> None:
        value = getattr(self, name)
        setattr(self, name, min(value + 1, 2**31 - 1))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_worktrees import hook_ipc

        observation = self
        original_handle = hook_ipc._Handler.handle
        original_accept = hook_ipc.HookIpcServer._on_request_accepted

        class Reader:
            def __init__(self, original: FrameReader, token: str) -> None:
                self.original, self.token = original, token

            def readline(self, size: int = -1, /) -> bytes:
                try:
                    raw = self.original.readline(size)
                except BaseException:
                    observation.increment("read_errors")
                    raise
                observation.record(raw, self.token)
                return raw

        def accepted(server: hook_ipc.HookIpcServer) -> None:
            self.increment("accepted")
            original_accept(server)

        def handle(handler: hook_ipc._Handler) -> None:
            self.increment("handler_started")
            original_reader = handler.rfile
            handler.rfile = cast(BufferedIOBase, Reader(original_reader, handler.server.token))
            try:
                original_handle(handler)
            except BaseException:
                self.increment("handler_errors")
                raise
            finally:
                handler.rfile = original_reader

        def dispatch_wrapper(
            original: Callable[[hook_ipc.HookIpcServer, dict[str, Any]], None],
        ) -> Callable[[hook_ipc.HookIpcServer, dict[str, Any]], None]:
            def dispatch(server: hook_ipc.HookIpcServer, request: dict[str, Any]) -> None:
                self.increment("dispatch_calls")
                try:
                    original(server, request)
                except BaseException:
                    self.increment("dispatch_errors")
                    raise
            return dispatch

        monkeypatch.setattr(hook_ipc._Handler, "handle", handle)
        monkeypatch.setattr(hook_ipc.HookIpcServer, "_on_request_accepted", accepted)
        for name in ("_on_read_demand", "_on_native_mutation"):
            monkeypatch.setattr(
                hook_ipc.HookIpcServer, name,
                dispatch_wrapper(getattr(hook_ipc.HookIpcServer, name)),
            )


class NotificationPacketObserver:
    @staticmethod
    def accepts(item: pytest.Function) -> bool:
        return (
            item.path.resolve() == Path(__file__).with_name("test_post_tool_shell.py").resolve()
            and item.originalname in {
                "test_registered_read_demand_is_send_only",
                "test_registered_native_mutation_is_send_only",
            }
        )

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        if (
            os.environ.get("AGENT_WORKTREES_NOTIFICATION_DIAGNOSTIC") != "1"
            or not isinstance(item, pytest.Function) or not self.accepts(item)
        ):
            yield
            return
        fixture = cast(PreparedFixture, item.funcargs["prepared_read_event"])
        monkeypatch = cast(pytest.MonkeyPatch, item.funcargs["monkeypatch"])
        facts = fixture["payload_root"] / "scripts" / "hook-facts.ps1"
        original = (Path(__file__).resolve().parents[1] / "scripts" / "hook-facts.ps1").read_bytes()
        if facts.read_bytes() != original:
            raise ValueError("Notification diagnostics require the original sender")
        facts.write_bytes(instrument_notification_sender(original.decode("utf-8")).encode("utf-8"))
        observe_after_entry(fixture["harness"], r"""
$notificationSummary = if ($script:notificationSender -is [Collections.IDictionary]) {
    ConvertTo-Json -Compress -InputObject $script:notificationSender
} else { '{"unavailable":1}' }
if ([Text.Encoding]::UTF8.GetByteCount($notificationSummary) -gt 4096) {
    $notificationSummary='{"unavailable":1}'
}
[Console]::Error.WriteLine('notification_sender=' + $notificationSummary)
""")
        observation = NotificationReceiverObservation()
        observation.install(monkeypatch)
        try:
            yield
        finally:
            print("notification_receiver=" + observation.encode())


def instrument_sender(source: str) -> str:
    """Observe fixture-owned finalization without changing timed operations."""
    start = source.index("function Send-PostToolReadDemand")
    end = source.index("function Get-PostToolRuntimeRoot", start)
    sender = source[start:end]
    original = (
        "    } catch {} finally {\n"
        "        if ($null -ne $client) { $client.Dispose() }\n"
        "    }\n"
    )
    replacement = r"""    } catch {
        $caught = $_.Exception.GetBaseException()
    } finally {
        try {
            $script:senderObservation = @{
                elapsed_ms = if ($null -ne $clock) { $clock.Elapsed.TotalMilliseconds } else { $null }
                remaining_ms = $remaining
                client_exists = $null -ne $client
                connected = $null -ne $client -and $client.Connected
                caught_type = if ($null -ne $caught) { $caught.GetType().FullName } else { $null }
                caught_hresult = if ($null -ne $caught) { $caught.HResult } else { $null }
                socket_error = if ($caught -is [Net.Sockets.SocketException]) { $caught.SocketErrorCode.ToString() } else { $null }
            }
        } finally {
            if ($null -ne $client) { $client.Dispose() }
        }
    }
"""
    assert sender.count(original) == 1
    sender = sender.replace(original, replacement)
    return source[:start] + sender + source[end:]


@pytest.mark.guard
def test_sender_observer_leaves_timed_operations_unchanged() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "hook-facts.ps1").read_text(
        encoding="utf-8",
    )
    observed = instrument_sender(source)
    before, _, after = source.partition("function Send-PostToolReadDemand")
    after_sender = "function Get-PostToolRuntimeRoot"
    assert observed.startswith(before)
    assert observed.partition(after_sender)[2] == source.partition(after_sender)[2]
    original_operations = after.partition("    } catch {} finally {")[0]
    observed_operations = observed.partition("function Send-PostToolReadDemand")[2].partition(
        "    } catch {",
    )[0]
    assert observed_operations == original_operations


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("AGENT_WORKTREES_SENDER_DIAGNOSTIC") != "1",
    reason="Explicit native Windows sender observation; not acceptance",
)
@pytest.mark.parametrize("prepared_read_event", ["cell"], indirect=True)
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["read"])
def test_registered_prepared_sender_stage_observation(
    prepared_read_event: PreparedFixture, monkeypatch: pytest.MonkeyPatch, shell: str, case: str,
) -> None:
    facts = prepared_read_event["payload_root"] / "scripts" / "hook-facts.ps1"
    facts.write_text(instrument_sender(facts.read_text(encoding="utf-8")), encoding="utf-8")
    observe_after_entry(
        prepared_read_event["harness"],
        "[Console]::Error.WriteLine('sender_observation=' + "
        "(ConvertTo-Json -Compress -InputObject $script:senderObservation))",
    )
    fixture = cast(PostToolFixture, importlib.import_module("test_post_tool_shell"))
    fixture.test_registered_prepared_cell_post_tool_uses_selected_original_owners(
        prepared_read_event, monkeypatch, shell, case,
    )
    pytest.fail("Sender observation is diagnostic only, not uninstrumented native acceptance")


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("AGENT_WORKTREES_SENDER_DIAGNOSTIC") != "1",
    reason="Explicit post-entry error observation; not acceptance",
)
@pytest.mark.parametrize("prepared_read_event", ["cell"], indirect=True)
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["read"])
def test_registered_prepared_post_entry_error_observation(
    prepared_read_event: PreparedFixture, monkeypatch: pytest.MonkeyPatch, shell: str, case: str,
) -> None:
    facts = prepared_read_event["payload_root"] / "scripts" / "hook-facts.ps1"
    assert facts.read_bytes() == (
        Path(__file__).resolve().parents[1] / "scripts" / "hook-facts.ps1"
    ).read_bytes()
    observe_after_entry(prepared_read_event["harness"], r"""
$observedErrors = @(
    $observedCount = 0
    foreach ($entryError in $Error) {
        if ($observedCount -ge 32) { break }
        $baseError = $entryError.Exception.GetBaseException()
        @{ type = $baseError.GetType().FullName; hresult = $baseError.HResult }
        $observedCount++
    }
)
[Console]::Error.WriteLine('post_entry_errors=' +
    (ConvertTo-Json -Compress -InputObject $observedErrors))
""")
    fixture = cast(PostToolFixture, importlib.import_module("test_post_tool_shell"))
    fixture.test_registered_prepared_cell_post_tool_uses_selected_original_owners(
        prepared_read_event, monkeypatch, shell, case,
    )
    pytest.fail("Post-entry observation is diagnostic only, not uninstrumented native acceptance")


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("AGENT_WORKTREES_SENDER_DIAGNOSTIC") != "1",
    reason="Explicit receiver-only observation; not acceptance",
)
@pytest.mark.parametrize("prepared_read_event", ["cell"], indirect=True)
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["read"])
def test_registered_prepared_receiver_observation(
    prepared_read_event: PreparedFixture, monkeypatch: pytest.MonkeyPatch, shell: str, case: str,
) -> None:
    observation = ReceiverObservation()
    install_receiver_observer(monkeypatch, observation)
    fixture = cast(PostToolFixture, importlib.import_module("test_post_tool_shell"))
    try:
        fixture.test_registered_prepared_cell_post_tool_uses_selected_original_owners(
            prepared_read_event, monkeypatch, shell, case,
        )
    finally:
        report_receiver_observation(observation, prepared_read_event)
    pytest.fail("Receiver observation is diagnostic only, not uninstrumented native acceptance")
