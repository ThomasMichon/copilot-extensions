"""Accepted HTTP work retains daemon admission through interrupted shutdown."""

import http.client
import json
import signal
import ssl
import threading

import pytest
from agent_procutil.daemon_instances import inspect_instances
from fleet_manager import gateway_server
from gateway_fixtures import Fixtures


@pytest.fixture(autouse=True)
def private_registry(tmp_path, monkeypatch):
    from agent_procutil import _daemon_instance_store as store

    monkeypatch.setenv(store.REGISTRY_ENV, str(tmp_path / "registry"))
    monkeypatch.delenv(store.INSTANCE_ENV, raising=False)
    monkeypatch.delenv(store.TEST_CONTEXT_ENV, raising=False)


def test_idle_gateway_closes_its_listener_before_finishing_admission(tmp_path, monkeypatch):
    fixture = Fixtures(tmp_path / "gateway")
    config = fixture.root / "gateway.json"
    config.write_text(json.dumps(fixture.value))
    server = gateway_server.build_server(fixture.config, fixture.signer)
    monkeypatch.setattr(gateway_server, "build_server", lambda *_args: server)
    monkeypatch.setattr(server, "serve_forever", lambda **_kwargs: None)
    try:
        assert gateway_server.serve(config) == 0
        assert server.fileno() == -1
        assert [row["state"] for row in inspect_instances(tmp_path / "registry")] == ["finished"]
    finally:
        server.server_close()


@pytest.mark.parametrize("ending", ["return", "interrupt", "error", "close-error"])
@pytest.mark.parametrize("interrupt_start", [False, True])
def test_accepted_handler_retires_before_admission_despite_interrupts(
    tmp_path, monkeypatch, ending, interrupt_start,
):
    registry = tmp_path / "registry"
    fixture = Fixtures(tmp_path / "gateway")
    config = fixture.root / "gateway.json"
    config.write_text(json.dumps(fixture.value))
    entered, release, joining = threading.Event(), threading.Event(), threading.Event()
    observed, client_errors, clients = [], [], []
    original_build = gateway_server.build_server
    original_join = threading.Thread.join
    original_start = threading.Thread.start
    original_wait = threading.Event.wait
    main = threading.current_thread()
    request_threads = []
    interruptions = []

    def dispatch(*_args, **_kwargs):
        request_threads.append(threading.current_thread())
        entered.set()
        assert release.wait(10)
        return {"nonce": "synthetic"}

    def request(server):
        context = ssl.create_default_context(cafile=fixture.value["tls"]["certificate"])
        client = http.client.HTTPSConnection("127.0.0.1", server.server_port,
                                            context=context, timeout=10)
        try:
            client.request("POST", "/v1/challenge", "{}", {"Content-Type": "application/json"})
            response = client.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"nonce": "synthetic"}
        except (OSError, AssertionError, ValueError) as error:
            client_errors.append(error)
        finally:
            client.close()

    def build(*args):
        server = original_build(*args)
        server.gateway.dispatch = dispatch

        def serve_forever(**_kwargs):
            client = threading.Thread(target=request, args=(server,))
            clients.append(client)
            client.start()
            server.handle_request()
            assert entered.wait(5)
            if ending == "interrupt":
                raise KeyboardInterrupt
            if ending == "error":
                raise ValueError("original serve failure")

        server.serve_forever = serve_forever
        if ending == "close-error":
            def server_close():
                server.socket.close()
                raise OSError("original close failure")

            server.server_close = server_close
        return server

    def start(thread):
        original_start(thread)
        if interrupt_start and thread.name == "gateway-close":
            signal.raise_signal(signal.SIGINT)

    def join(thread, *args, **kwargs):
        if thread in request_threads:
            joining.set()
            if threading.current_thread() is main:
                interruptions.append("join")
                signal.raise_signal(signal.SIGINT)
        return original_join(thread, *args, **kwargs)

    def wait(event, timeout=None):
        if (threading.current_thread() is main and joining.is_set()
                and timeout == 0.1 and len(interruptions) < 3):
            interruptions.append("wait")
            signal.raise_signal(signal.SIGINT)
        return original_wait(event, timeout)

    def observe():
        assert joining.wait(5)
        # Allow the interrupted caller to escape on the old implementation.
        original_wait(release, 0.15)
        observed.extend(row["state"] for row in inspect_instances(registry))
        release.set()

    monkeypatch.setattr(gateway_server, "build_server", build)
    monkeypatch.setattr(threading.Thread, "join", join)
    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(threading.Event, "wait", wait)
    observer = threading.Thread(target=observe)
    observer.start()
    failure = None
    try:
        try:
            result = gateway_server.serve(config)
        except BaseException as error:  # noqa: BLE001 - assert interrupted exits without aborting pytest.
            failure = error
        original_join(observer, 10)
        assert observed == ["running"]
        assert len(interruptions) == 3
        assert all(not thread.is_alive() for thread in request_threads)
        if ending == "error":
            assert isinstance(failure, ValueError) and str(failure) == "original serve failure"
        elif ending == "close-error":
            assert isinstance(failure, OSError) and str(failure) == "original close failure"
        else:
            assert failure is None
            assert result == 0
        assert [row["state"] for row in inspect_instances(registry)] == ["finished"]
    finally:
        release.set()
        original_join(observer, 10)
        for thread in clients + request_threads:
            original_join(thread, 10)
            assert not thread.is_alive()
    assert not observer.is_alive()
    assert client_errors == []
