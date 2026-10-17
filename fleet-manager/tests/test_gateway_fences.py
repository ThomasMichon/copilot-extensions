"""Negative execution/credential boundaries with genuine signatures, never crypto mocks."""

import copy
import time
from types import SimpleNamespace

import pytest
from fleet_manager.gateway_auth import AuthContext, Signer, thumbprint, verify
from fleet_manager.gateway_broker import Broker
from fleet_manager.gateway_client import Discovery, GatewayError
from fleet_manager.gateway_contract import GatewayConfig, correlation, text
from fleet_manager.gateway_index import ScopedIndexBackend
from fleet_manager.gateway_server import build_server
from gateway_fixtures import Fixtures, credential_request, query
from test_gateway_runtime import controller, running


@pytest.fixture
def fixture(tmp_path):
    return Fixtures(tmp_path)


def backend_api(fixture, calls):
    return ScopedIndexBackend(
        fixture.config.backends[0],
        Signer(fixture.backend_key, "backend", "backend-issuer", "backend-a"),
        gateway_keys=fixture.gateway_keys, gateway_issuer="fixture-issuer",
        gateway_subject="gateway-install", fleet="fleet-a",
        retrieve=lambda scope, parameters: calls.append(scope) or {"hits": []})


def grant(fixture, value, *, scope=None, signer=None, changes=None):
    claims = {"contract": "fleet-index-scoped-v1", "backend": "backend-a", "fleet": "fleet-a",
              "correlation": correlation(value),
              "scope": scope or {"principal": "alice", "corpus": "corpus-a", "sources": ["source-a"]}}
    claims.update(changes or {})
    return (signer or fixture.signer).sign(claims, "backend-a", 30)


@pytest.mark.parametrize("change", [
    {"fleet": "other"}, {"backend": "other"}, {"contract": "index-v1"},
    {"correlation": {}}, {"scope": {"principal": "alice", "corpus": "corpus-b", "sources": ["source-a"]}},
    {"scope": {"principal": "alice", "corpus": "corpus-a", "sources": []}},
    {"scope": {"principal": "alice", "corpus": "corpus-a", "sources": ["unregistered"]}},
])
def test_backend_rejects_before_retrieval(fixture, change):
    calls = []
    api = backend_api(fixture, calls)
    value = query()
    with pytest.raises(GatewayError):
        api.admit({"request": value, "grant": grant(fixture, value, changes=change)})
    assert calls == []


def test_backend_admission_replay_restart_and_wrong_issuer_key(fixture):
    calls = []
    api = backend_api(fixture, calls)
    value = query()
    body = {"request": value, "grant": grant(fixture, value)}
    receipt = api.admit(body)
    admitted = verify(receipt["receipt"], fixture.config.backends[0].jwks,
                      "backend-issuer", "gateway-install")
    execute = body | {"admission": admitted["admission"]}
    api.execute(execute)
    assert len(calls) == 1
    with pytest.raises(GatewayError):
        api.execute(execute)
    with pytest.raises(GatewayError):
        backend_api(fixture, calls).execute(execute)
    # An ordinary access-token issuer is not a Gateway execution signer.
    issuer = Signer(fixture.issuer, "issuer", "fixture-issuer", "gateway-install")
    with pytest.raises(GatewayError):
        api.admit({"request": value, "grant": grant(fixture, value, signer=issuer)})
    assert len(calls) == 1


def test_backend_changed_request_fence(fixture):
    calls = []
    api = backend_api(fixture, calls)
    value = query()
    body = {"request": value, "grant": grant(fixture, value)}
    admitted = verify(api.admit(body)["receipt"], fixture.config.backends[0].jwks,
                      "backend-issuer", "gateway-install")
    altered = copy.deepcopy(body)
    altered["request"]["parameters"]["source"] = "source-b"
    with pytest.raises(GatewayError):
        api.execute(altered | {"admission": admitted["admission"]})
    assert calls == []


@pytest.mark.parametrize("binding_change", [
    {"recipient_jkt": "wrong"}, {"controller_generation": 2}, {"generation": 2},
    {"request_digest": "wrong"}, {"installation_id": "other"}, {"fleet": "other"}, {"epoch": "old"},
])
def test_correlated_controller_reply_must_match_work_order(fixture, binding_change):
    config = fixture.config
    broker = Broker(config, fixture.signer, "epoch-a")
    worker = AuthContext(config.enrollments[2], int(time.time()) + 120)
    provider = AuthContext(config.enrollments[3], int(time.time()) + 120)
    subscription = broker.subscribe(provider, {"lease": 15})["subscription"]
    value = credential_request()
    broker.submit(worker, value)
    dispatch = verify(broker.poll(provider, {"subscription": subscription})["dispatch"],
                      fixture.gateway_keys, "fixture-issuer", "controller", lifetime=60)
    binding = {k: v for k, v in dispatch.items() if k not in {
        "iss", "aud", "sub", "iat", "nbf", "exp", "jti", "recipient_jwk", "deadline", "subscription"}}
    binding.update(binding_change)
    signer = Signer(fixture.proofs["controller"], "proof", "controller", "controller")
    receipt = signer.sign({"binding": binding, "state": "UNSUPPORTED"}, "worker", 10)
    with pytest.raises(GatewayError):
        broker.reply(provider, {"subscription": subscription, "worker": "worker",
                                "request_id": value["request_id"], "receipt": receipt})
    assert broker.result(worker, {"request_id": value["request_id"],
                                 "request_digest": correlation(value)["request_digest"]})["state"] == "DISPATCHED"


def test_controller_queue_budget_and_unsigned_reply(fixture):
    fixture.value["pending_limit"] = 1
    config = fixture.config
    broker = Broker(config, fixture.signer, "epoch-a")
    worker = AuthContext(config.enrollments[2], int(time.time()) + 120)
    provider = AuthContext(config.enrollments[3], int(time.time()) + 120)
    subscription = broker.subscribe(provider, {"lease": 15})["subscription"]
    broker.submit(worker, credential_request())
    with pytest.raises(GatewayError):
        broker.submit(worker, credential_request("second"))
    with pytest.raises(GatewayError):
        broker.reply(provider, {"subscription": subscription, "worker": "worker",
                                "request_id": "unsolicited", "receipt": "not-signed"})
    broker.poll(provider, {"subscription": subscription})
    with pytest.raises(GatewayError):
        broker.reply(provider, {"subscription": subscription, "worker": "worker",
                                "request_id": "credential-1", "receipt": "not-signed"})


def test_revoked_recipient_and_connector_keys(fixture):
    from fleet_manager.gateway_auth import Admission
    for key in (fixture.value["enrollments"][2]["recipient_jwk"],
                fixture.value["enrollments"][2]["proof_jwk"]):
        value = copy.deepcopy(fixture.value)
        value["revoked"] = [thumbprint(key)]
        admission = Admission(GatewayConfig.load(value))
        message = fixture.message("worker", "/v1/session", {}, admission=admission)
        with pytest.raises(GatewayError):
            admission.authenticate(message["access"], message["proof"], "https://127.0.0.1:443/v1/session")


@pytest.mark.parametrize("failure", ["value", "process", "key"])
@pytest.mark.parametrize("synchronous", [False, True])
def test_provider_exceptions_never_cross_the_wire(fixture, capsys, failure, synchronous):
    import subprocess

    error = {
        "value": ValueError("SYNTHETIC-SECRET-NEVER-LOG"),
        "process": subprocess.CalledProcessError(1, "SYNTHETIC-SECRET-NEVER-LOG"),
        "key": KeyError("SYNTHETIC-SECRET-NEVER-LOG"),
    }[failure]
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))

        class BrokenSyntheticProvider:
            async def acquire(self, request, *, timeout):
                raise error

        class BrokenSynchronousProvider:
            def acquire(self, request, *, timeout):
                raise error

        provider.provider = BrokenSynchronousProvider() if synchronous else BrokenSyntheticProvider()
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        assert provider.step() == "UNAVAILABLE"
        assert worker.credential_result(value["request_id"], correlation(value)["request_digest"])["state"] == "UNAVAILABLE"
        provider.close()
    logs = capsys.readouterr()
    assert "SYNTHETIC" not in logs.out + logs.err


def test_resident_controller_survives_unexpected_provider_exception(fixture, capsys):
    import subprocess
    import threading

    stop = threading.Event()
    outcomes, failures = [], []
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))

        class BrokenProvider:
            async def acquire(self, request, *, timeout):
                raise subprocess.CalledProcessError(1, "SYNTHETIC-SECRET-NEVER-LOG")

        provider.provider = BrokenProvider()

        def serve():
            try:
                outcomes.append(provider.serve(stop))
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            deadline = time.monotonic() + 3
            while provider.subscription is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert provider.subscription is not None
            value = credential_request()
            worker.request_credential(value)
            result = {}
            while time.monotonic() < deadline:
                result = worker.credential_result(value["request_id"], correlation(value)["request_digest"])
                if result["state"] == "UNAVAILABLE":
                    break
                time.sleep(0.1)
            assert result["state"] == "UNAVAILABLE"
            assert thread.is_alive()
        finally:
            stop.set()
            thread.join(3)
        assert not thread.is_alive() and outcomes == ["STOPPED"] and failures == []
    logs = capsys.readouterr()
    assert "SYNTHETIC" not in logs.out + logs.err


def test_provider_process_control_cancellation_is_not_normalized(fixture):
    import asyncio

    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))

        class CancelledProvider:
            async def acquire(self, request, *, timeout):
                raise asyncio.CancelledError()

        provider.provider = CancelledProvider()
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        try:
            with pytest.raises(asyncio.CancelledError):
                provider.step()
        finally:
            provider.close()


def test_distinct_python310_asyncio_timeout_is_sanitized(fixture, monkeypatch, capsys):
    import asyncio

    class LegacyAsyncioTimeoutError(Exception):
        pass

    assert not issubclass(LegacyAsyncioTimeoutError, TimeoutError)
    monkeypatch.setattr(asyncio, "TimeoutError", LegacyAsyncioTimeoutError)
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))

        class LegacyTimeoutProvider:
            async def acquire(self, request, *, timeout):
                raise LegacyAsyncioTimeoutError("SYNTHETIC-SECRET-NEVER-LOG")

        provider.provider = LegacyTimeoutProvider()
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        assert provider.step() == "UNAVAILABLE"
        assert worker.credential_result(value["request_id"], correlation(value)["request_digest"])["state"] == "UNAVAILABLE"
        provider.close()
    logs = capsys.readouterr()
    assert "SYNTHETIC" not in logs.out + logs.err


def test_controller_timeout_handler_is_python310_source_compatible():
    import ast
    import inspect
    import textwrap

    from fleet_manager.gateway_controller import Controller

    tree = ast.parse(textwrap.dedent(inspect.getsource(Controller.step)), feature_version=(3, 10))
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    assert any(
        isinstance(error, ast.Attribute) and error.attr == "TimeoutError"
        and isinstance(error.value, ast.Name) and error.value.id == "asyncio"
        for handler in handlers if isinstance(handler.type, ast.Tuple) for error in handler.type.elts
    )


def test_actual_provider_wait_for_deadline_is_unknown_not_success(fixture, capsys):
    import asyncio
    fixture.value["lease_seconds"] = 2
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        provider.lease_seconds = 2
        cancelled = []

        class DeadlineProvider:
            async def acquire(self, request, *, timeout):
                try:
                    await asyncio.sleep(timeout + 10)
                finally:
                    cancelled.append(True)

        provider.provider = DeadlineProvider()
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        started = time.monotonic()
        assert provider.step() == "UNKNOWN"
        assert time.monotonic() - started < 3
        assert cancelled == [True]
        assert worker.credential_result(value["request_id"], correlation(value)["request_digest"])["state"] == "UNKNOWN"
        provider.close()
    logs = capsys.readouterr()
    assert "SYNTHETIC" not in logs.out + logs.err


@pytest.mark.parametrize("field", ["exp", "deadline"])
@pytest.mark.parametrize("invalid", [
    "fractional", "numeric-string", "boolean", "null", "missing", "negative", "overflow",
])
def test_controller_rejects_invalid_signed_deadline_before_provider_or_reply(
        fixture, monkeypatch, field, invalid):
    import jwt

    config = fixture.config
    broker = Broker(config, fixture.signer, "epoch-a")
    worker = AuthContext(config.enrollments[2], int(time.time()) + 120)
    provider = AuthContext(config.enrollments[3], int(time.time()) + 120)
    subscription = broker.subscribe(provider, {"lease": 15})["subscription"]
    broker.submit(worker, credential_request())
    result = broker.poll(provider, {"subscription": subscription})
    claims = verify(result["dispatch"], fixture.gateway_keys,
                    "fixture-issuer", "controller", lifetime=60)
    if invalid == "missing":
        del claims[field]
    else:
        claims[field] = {
            "fractional": claims[field] + 0.5, "numeric-string": str(claims[field]),
            "boolean": True, "null": None, "negative": -1, "overflow": 2**53 + 1,
        }[invalid]
    result["dispatch"] = jwt.encode(
        claims, fixture.gateway, algorithm="EdDSA", headers={"kid": "gateway", "typ": "JWT"},
    )

    def forbidden(*args, **kwargs):
        pytest.fail("invalid signed dispatch reached provider, receipt signer or reply")

    client = SimpleNamespace(
        principal="controller", epoch="epoch-a",
        discovery=fixture.client("controller", 1).discovery,
        subscribe=lambda *args: {"subscription": subscription},
        poll=lambda *args: result, reply=forbidden,
    )
    owner = controller(fixture, client)
    owner.subscription = subscription
    monkeypatch.setattr(owner.provider, "acquire", forbidden)
    monkeypatch.setattr(owner.signer, "sign", forbidden)
    with pytest.raises(GatewayError):
        owner.step()


@pytest.mark.parametrize("outcome", [
    "budget-expired", "result-after-dispatch-expiry", "result-before-dispatch-expiry",
])
def test_controller_acquisition_respects_dispatch_expiry(fixture, monkeypatch, outcome):
    import asyncio

    from fleet_manager import gateway_controller
    from fleet_manager.gateway_client import CredentialMaterial, consume_credential

    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        clock = [time.time()]
        cancelled, replies, expiries = [], [], []
        monkeypatch.setattr(gateway_controller, "time", SimpleNamespace(time=lambda: clock[0]))
        real_sign = gateway.gateway.broker.signer.sign

        def sign_dispatch(payload, audience, ttl=30, *, expires_at=None):
            if "recipient_jwk" not in payload:
                return real_sign(payload, audience, ttl, expires_at=expires_at)
            expiry = expires_at - 1
            expiries.append(expiry)
            return real_sign(payload, audience, ttl, expires_at=expiry)

        monkeypatch.setattr(gateway.gateway.broker.signer, "sign", sign_dispatch)
        real_poll = provider.client.poll

        def poll(subscription):
            result = real_poll(subscription)
            dispatch = verify(result["dispatch"], fixture.gateway_keys,
                              "fixture-issuer", "controller", lifetime=60)
            assert dispatch["exp"] < dispatch["deadline"]
            clock[0] = (dispatch["iat"] if outcome == "result-before-dispatch-expiry"
                        else dispatch["exp"] - 0.05)
            return result

        monkeypatch.setattr(provider.client, "poll", poll)
        real_reply = provider.client.reply

        def reply(*args):
            replies.append(args)
            return real_reply(*args)

        monkeypatch.setattr(provider.client, "reply", reply)

        class ExpiringProvider:
            async def acquire(self, request, *, timeout):
                try:
                    if outcome == "budget-expired":
                        await asyncio.sleep(timeout + 10)
                    if outcome == "result-after-dispatch-expiry":
                        clock[0] = expiries[-1]
                    return CredentialMaterial(
                        "SYNTHETIC-EXPIRY-REGRESSION", int(time.time()) + 300,
                        request["source"], request["account"], request["audience"],
                        tuple(request["scopes"]),
                    )
                finally:
                    cancelled.append(True)

        provider.provider = ExpiringProvider()
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        if outcome == "result-before-dispatch-expiry":
            assert provider.step() == "READY"
            keys = {"keys": [fixture.config.enrollments[3].receipt_jwk]}
            receipt = verify(replies[0][-1], keys, "controller", "worker", lifetime=60)
            assert receipt["exp"] == expiries[-1]
            result = worker.credential_result(
                value["request_id"], correlation(value)["request_digest"],
            )
            material = consume_credential(
                worker, value, result, recipient_key=fixture.recipient,
                controller_keys=keys, controller="controller", controller_generation=1,
            )
            assert material.expires_at == expiries[-1]
        else:
            assert provider.step() == "UNKNOWN"
            assert replies == []
        assert cancelled == [True]
        provider.close()


def test_unicode_secret_error_is_sanitized(fixture):
    with pytest.raises(GatewayError) as error:
        text("SYNTHETIC-SECRET-\ud800")
    assert "SYNTHETIC" not in repr(error.value)


def test_explicit_discovery_remote_and_private_trust_rejected(fixture, tmp_path):
    value = {
        "schema": "fleet-manager.gateway-endpoint", "version": 1,
        "endpoint": "https://example.invalid:443", "ca": str(fixture.root / "tls.pem"),
        "issuer": "fixture-issuer", "audience": "gateway-a", "gateway": "gateway-install",
        "jwks": fixture.gateway_keys, "remote": False,
    }
    import json
    path = tmp_path / "endpoint.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(GatewayError):
        Discovery.load(path)
    value["remote"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    assert Discovery.load(path).remote
    value["jwks"]["keys"][0]["d"] = "private"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(GatewayError):
        Discovery.load(path)


def test_controller_expired_signed_reply_and_no_queue_retry(fixture):
    import jwt
    config = fixture.config
    broker = Broker(config, fixture.signer, "epoch-a")
    worker = AuthContext(config.enrollments[2], int(time.time()) + 120)
    provider = AuthContext(config.enrollments[3], int(time.time()) + 120)
    subscription = broker.subscribe(provider, {"lease": 15})["subscription"]
    value = credential_request()
    broker.submit(worker, value)
    work = verify(broker.poll(provider, {"subscription": subscription})["dispatch"],
                  fixture.gateway_keys, "fixture-issuer", "controller", lifetime=60)
    binding = {k: v for k, v in work.items() if k not in {
        "iss", "aud", "sub", "iat", "nbf", "exp", "jti", "recipient_jwk", "deadline", "subscription"}}
    now = int(time.time())
    receipt = jwt.encode({"iss": "controller", "sub": "controller", "aud": "worker",
                          "iat": now - 30, "nbf": now - 30, "exp": now - 1, "jti": "expired",
                          "binding": binding, "state": "UNSUPPORTED"},
                         fixture.proofs["controller"], algorithm="EdDSA", headers={"kid": "proof"})
    with pytest.raises(GatewayError):
        broker.reply(provider, {"subscription": subscription, "worker": "worker",
                                "request_id": value["request_id"], "receipt": receipt})
    assert broker.poll(provider, {"subscription": subscription})["state"] == "IDLE"
    broker.disconnect(provider, {"subscription": subscription})
    assert broker.result(worker, {"request_id": value["request_id"],
                                 "request_digest": correlation(value)["request_digest"]})["state"] == "UNKNOWN"
