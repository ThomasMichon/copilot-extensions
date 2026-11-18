"""Membership listing keeps native identity without a second pane-observation pass."""

from pathlib import Path
from contextlib import nullcontext
import socket
import threading
from types import SimpleNamespace

import pytest

from agent_remote_driver import mux_inventory as inventory, mux_observation as observation
from agent_remote_driver import windows_mux_processes
from agent_remote_driver.mux_observation_types import ObservationUnavailable


def register(root: Path, base: str, pid: int, port: int = 12345) -> None:
    (root / f"{base}.pid").write_text(f"{pid}:100")
    (root / f"{base}.port").write_text(str(port))
    (root / f"{base}.key").write_text("a" * 16)


@pytest.fixture
def native_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("COPILOT_DRIVER_PSMUX_NAMESPACE", raising=False)
    monkeypatch.setattr(windows_mux_processes, "servers", lambda: {101: "100"})
    register(tmp_path, "wt-fixture", 101)
    return tmp_path


def test_listing_uses_one_membership_query_within_the_original_budget(
    native_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    queries: list[float] = []
    census: list[None] = []
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(inventory, "time", clock)
    monkeypatch.setattr(observation, "time", clock)

    def servers() -> dict[int, str]:
        census.append(None)
        return {101: "100"}

    def query(port: int, key: str, deadline: float) -> tuple[str, int, int, str]:
        assert port == 12345 and key == "a" * 16
        queries.append(deadline)
        now[0] += 0.75
        return "wt-fixture", 2, 101, "$7:100"

    monkeypatch.setattr(windows_mux_processes, "servers", servers)
    monkeypatch.setattr(inventory, "_query_membership", query, raising=False)
    monkeypatch.setattr(
        inventory, "_query", lambda *args: pytest.fail("legacy count-only query"),
    )
    monkeypatch.setattr(
        observation, "observe_psmux_many", lambda *args, **kwargs: pytest.fail("second pane pass"),
    )
    observed = observation.list_psmux()
    assert observed is not None
    assert set(observed) == {"wt-fixture"}
    assert observed["wt-fixture"].status == "present"
    assert observed["wt-fixture"].clients == 2
    assert observed["wt-fixture"].incarnation == "$7:100"
    assert observed["wt-fixture"].panes == ()
    assert queries == [1.0] and len(census) == 2


@pytest.mark.parametrize("namespace", ["", "selected"])
def test_membership_filters_namespaces_without_dropping_literal_double_underscores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: str,
) -> None:
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", namespace)
    name = "wt-fixture__one"
    selected = f"{namespace}__{name}" if namespace else name
    register(tmp_path, selected, 101)
    register(tmp_path, f"foreign__{name}", 202, 12346)
    monkeypatch.setattr(
        windows_mux_processes, "servers", lambda: {101: "100", 202: "100"},
    )

    def query(port: int, key: str, deadline: float) -> tuple[str, int, int, str]:
        return (name, 0, 101, "$7:100") if port == 12345 else (name, 3, 202, "$8:200")

    monkeypatch.setattr(inventory, "_query_membership", query, raising=False)
    observed = observation.list_psmux()
    assert observed is not None and set(observed) == {name}
    assert observed[name].clients == 0 and observed[name].incarnation == "$7:100"


@pytest.mark.parametrize("failure", ["birth", "uncovered", "registration", "duplicate"])
def test_membership_does_not_weaken_complete_native_coverage(
    native_root: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    def query(port: int, key: str, deadline: float) -> tuple[str, int, int, str]:
        if failure == "birth":
            monkeypatch.setattr(windows_mux_processes, "servers", lambda: {101: "200"})
        elif failure == "uncovered":
            monkeypatch.setattr(
                windows_mux_processes, "servers", lambda: {101: "100", 202: "100"},
            )
        elif failure == "registration":
            (native_root / "wt-fixture.port").write_text("12346")
        return "wt-fixture", 1, 101, "$7:100"

    if failure == "duplicate":
        (native_root / "wt-fixture.pid").unlink()
        (native_root / "other__wt-fixture.port").write_text("12346")
        (native_root / "other__wt-fixture.key").write_text("b" * 16)
    monkeypatch.setattr(inventory, "_query_membership", query, raising=False)
    assert observation.list_psmux() is None


@pytest.mark.parametrize("reply", [
    "wt-fixture:2:101:$7:100", "wt-fixture:0:101:$0:0", "name:with:colon:3:101:$8:200",
])
def test_membership_reply_retains_native_identity(
    monkeypatch: pytest.MonkeyPatch, reply: str,
) -> None:
    expected_name, expected_count, expected_pid, session, created = reply.rsplit(":", 4)
    clients = "\n".join(
        f"C\t{expected_name}" for _ in range(int(expected_count))
    )
    monkeypatch.setattr(
        inventory, "_reply",
        lambda port, key, deadline, query: clients if query.startswith(b"list-clients ") else reply,
    )
    name, count, pid, incarnation = inventory._query_membership(12345, "a" * 16, 1)
    assert (name, count, pid, incarnation) == (
        expected_name, int(expected_count), int(expected_pid), f"{session}:{created}",
    )


@pytest.mark.parametrize("reply", [
    "wt-fixture:2:101", "wt-fixture:2:101:7:100", "wt-fixture:2:101:$7:bad",
    "wt-fixture:-1:101:$7:100", "wt-fixture:2:bad:$7:100", ":2:101:$7:100",
])
def test_membership_reply_refuses_missing_or_malformed_identity(
    monkeypatch: pytest.MonkeyPatch, reply: str,
) -> None:
    monkeypatch.setattr(inventory, "_reply", lambda *args: reply, raising=False)
    with pytest.raises(ObservationUnavailable):
        inventory._query_membership(12345, "a" * 16, 1)


def test_membership_counts_client_rows_not_the_legacy_attachment_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter([
        "wt-fixture:1:101:$7:100", "C\twt-fixture\nC\twt-fixture", "wt-fixture:1:101:$7:100",
    ])
    monkeypatch.setattr(inventory, "_reply", lambda *args: next(replies))
    assert inventory._query_membership(12345, "a" * 16, 1) == ("wt-fixture", 2, 101, "$7:100")


@pytest.mark.parametrize("reply", [b"OK", b"OK\r"])
def test_empty_client_rows_require_a_complete_authentication_line(
    monkeypatch: pytest.MonkeyPatch, reply: bytes,
) -> None:
    chunks = iter([reply, b""])
    stream = SimpleNamespace(
        settimeout=lambda value: None, sendall=lambda value: None,
        shutdown=lambda value: None, recv=lambda count: next(chunks),
    )
    monkeypatch.setattr(
        socket, "create_connection", lambda *args, **kwargs: nullcontext(stream),
    )
    with pytest.raises(ObservationUnavailable):
        inventory._reply(12345, "a" * 16, 10**12, b"list-clients\n")


@pytest.mark.parametrize("clients,identity", [
    ("C\twt-fixture\t$8:200", "wt-fixture:1:101:$7:100"),
    ("C\tforeign\t$7:100", "wt-fixture:1:101:$7:100"),
    ("", "wt-fixture:1:101:$7:100"),
    ("C\twt-fixture", "wt-fixture:0:101:$7:100"),
    ("C\twt-fixture\n" * 550, "wt-fixture:1:101:$7:100"),
])
def test_membership_retains_client_identity_and_aggregate_byte_fences(
    monkeypatch: pytest.MonkeyPatch, clients: str, identity: str,
) -> None:
    replies = iter([identity, clients, identity])
    monkeypatch.setattr(inventory, "_reply", lambda *args: next(replies))
    with pytest.raises(ObservationUnavailable):
        inventory._query_membership(12345, "a" * 16, 1)


@pytest.mark.parametrize("final", [
    "wt-fixture:1:101:$8:100", "wt-fixture:1:202:$7:100", "wt-fixture:0:101:$7:100",
])
def test_membership_client_rows_cannot_cross_native_identity_changes(
    monkeypatch: pytest.MonkeyPatch, final: str,
) -> None:
    replies = iter(["wt-fixture:1:101:$7:100", "C\twt-fixture", final])
    monkeypatch.setattr(inventory, "_reply", lambda *args: next(replies))
    with pytest.raises(ObservationUnavailable):
        inventory._query_membership(12345, "a" * 16, 1)


def test_membership_wire_fences_legacy_client_rows_with_the_same_incarnation(
    native_root: Path,
) -> None:
    captured: list[bytes] = []
    failures: list[Exception] = []
    replies = [
        b"OK\nwt-fixture:1:101:$7:100\n",
        b"OK\nC\twt-fixture\nC\twt-fixture\n",
        b"OK\nwt-fixture:1:101:$7:100\n",
    ]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(3)
        listener.settimeout(2)
        port = listener.getsockname()[1]
        (native_root / "wt-fixture.port").write_text(str(port))

        def serve() -> None:
            try:
                for reply in replies:
                    stream, _ = listener.accept()
                    with stream:
                        stream.settimeout(2)
                        with stream.makefile("rb") as reader:
                            captured.append(reader.readline())
                            captured.append(reader.readline())
                            stream.sendall(reply)
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            observed = observation.list_psmux()
        finally:
            worker.join(timeout=3)
        assert not worker.is_alive() and not failures
    assert observed is not None
    assert observed["wt-fixture"].clients == 2
    assert observed["wt-fixture"].incarnation == "$7:100"
    assert len(captured) == 6
    assert all(value.partition(b" ")[-1] == b"a" * 16 + b"\n" for value in captured[::2])
    assert captured[1] == captured[5]
    assert captured[1].startswith(b"display-message ")
    assert captured[3].startswith(b"list-clients ")
    assert not any(b"list-panes" in value for value in captured)
