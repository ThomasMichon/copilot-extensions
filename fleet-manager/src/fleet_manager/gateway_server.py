"""Opt-in role-selected NEW Gateway; bounded TLS runtime independent of index/store."""

from __future__ import annotations

import secrets
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .gateway_auth import Admission, Signer, thumbprint
from .gateway_broker import Broker
from .gateway_contract import (
    INDEX_CONTRACT,
    MAX_WIRE,
    GatewayConfig,
    GatewayError,
    encode,
    fields,
    parse,
    require,
    endpoint,
)
from .gateway_index import IndexRouter
from .planning import digest

ERROR_CODES = frozenset({
    "gateway-restarted", "unsupported-operation", "backend-unsupported", "offer-unavailable",
    "offer-generation-mismatch", "corpus-denied", "source-denied", "role-denied",
    "controller-unavailable", "provider-policy-denied", "duplicate-request", "broker-unavailable",
    "session-capacity-exhausted", "subscription-unavailable", "subscription-already-held",
    "reply-out-of-order", "unsolicited-reply", "reply-binding-mismatch", "correlation-mismatch",
    "ack-uncertain", "credential-scope-denied", "recipient-denied", "worker-binding-mismatch",
    "transport-unavailable",
})
REQUEST_TIMEOUT = 5


class Gateway:
    def __init__(self, config: GatewayConfig, signer: Signer, *, origin: str):
        self.config, self.signer = config, signer
        self.origin = endpoint(origin, remote=True)
        self.epoch = secrets.token_hex(16)
        self.admission = Admission(config)
        self.broker = Broker(config, signer, self.epoch)
        self.index = IndexRouter(config, signer)

    def dispatch(self, route: str, message: dict, *, deadline: float | None = None) -> dict:
        if route == "/v1/challenge":
            fields(message, set())
            self.admission.budget()
            return {"profile": "jwt-pop-v1", "nonce": self.admission.verifier.nonce}
        require(route in ("/v1/session", "/v1/index/query", "/v1/credentials/request",
                          "/v1/credentials/result", "/v1/credentials/ack",
                          "/v1/controllers/subscribe", "/v1/controllers/poll",
                          "/v1/controllers/reply", "/v1/controllers/disconnect"),
                "unsupported-operation")
        fields(message, {"access", "proof", "body"})
        body = fields(message["body"], {"epoch", "payload"})
        require(type(body["payload"]) is dict)
        context = self.admission.authenticate(message["access"], message["proof"], self.origin + route)
        try:
            if route == "/v1/session":
                fields(body["payload"], set())
                supported = []
                if context.peer.role == "query" and any(
                    b.service == context.peer.service and b.contract == INDEX_CONTRACT
                    and b.corpus in context.peer.corpora
                    and set(b.sources) & set(context.peer.sources)
                    and b.identity not in self.config.revoked
                    and b.expires_at > time.time() for b in self.config.backends
                ):
                    supported.append("index.search")
                if context.peer.role == "index-worker" and any(
                    r["worker"] == context.peer.principal for r in self.config.credential_rules
                ):
                    supported.append("credential.get")
                response = {"state": "ADMITTED", "epoch": self.epoch, "supported": supported,
                            "unsupported": ["index.similar", "index.clusters", "index.status",
                                            "reindex", "drain", "deploy", "node.serve"]}
            else:
                require(body["epoch"] == self.epoch, "gateway-restarted")
                routes = {
                    "/v1/index/query": lambda context, payload: self.index.execute(
                        context, payload, deadline=deadline),
                    "/v1/credentials/request": self.broker.submit,
                    "/v1/credentials/result": self.broker.result,
                    "/v1/credentials/ack": self.broker.ack,
                    "/v1/controllers/subscribe": self.broker.subscribe,
                    "/v1/controllers/poll": self.broker.poll,
                    "/v1/controllers/reply": self.broker.reply,
                    "/v1/controllers/disconnect": self.broker.disconnect,
                }
                require(route in routes, "unsupported-operation")
                response = routes[route](context, body["payload"])
        except GatewayError as exc:
            code = str(exc) if str(exc) in ERROR_CODES else "request-denied"
            response = {"state": "UNAVAILABLE" if code in ("transport-unavailable", "controller-unavailable")
                        else "DENIED", "error": code}
        receipt = self.signer.sign({"route": route, "request_digest": digest(body),
                                    "epoch": self.epoch, "response": response},
                                   context.peer.principal, 30)
        return {"receipt": receipt}


class _Server(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 16

    def __init__(self, address, config: GatewayConfig, signer: Signer, tls: ssl.SSLContext):
        self.tls = tls
        self._slots = threading.BoundedSemaphore(16)
        self._request_deadline = threading.local()
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        super().__init__(address, _Handler)
        host = f"[{address[0]}]" if ":" in address[0] else address[0]
        origin = config.tls.get("origin", f"https://{host}:{self.server_port}")
        try:
            self.gateway = Gateway(config, signer, origin=origin)
        except (GatewayError, ValueError, TypeError):
            self.server_close()
            raise

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(5)
        return connection, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except RuntimeError:
            self._slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        connection = request
        deadline = time.monotonic() + REQUEST_TIMEOUT
        self._request_deadline.value = deadline

        def expire():
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # An already disconnected peer needs no further shutdown.

        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.start()
        try:
            connection = self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
            connection.do_handshake()
            super().process_request_thread(connection, client_address)
        except (OSError, ssl.SSLError):
            return
        finally:
            timer.cancel()
            timer.join()
            self.shutdown_request(connection)
            self._slots.release()

    def handle_error(self, request, client_address):
        print("fleet-manager: request unavailable", file=sys.stderr)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "fleet-manager"

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._write(code, {"state": "DENIED"})

    def _write(self, code: int, value: dict):
        raw = encode(value)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            require(self.headers.get("Content-Type") == "application/json"
                    and self.headers.get("Transfer-Encoding") is None, "invalid-framing")
            lengths = self.headers.get_all("Content-Length") or []
            require(len(lengths) == 1 and lengths[0].isdigit(), "invalid-framing")
            length = int(lengths[0])
            require(0 < length <= MAX_WIRE, "wire-limit")
            raw = self.rfile.read(length)
            require(len(raw) == length, "invalid-framing")
            result = self.server.gateway.dispatch(self.path, parse(raw),
                                                  deadline=self.server._request_deadline.value)
        except (GatewayError, UnicodeError, KeyError, TypeError, ValueError):
            self._write(403, {"state": "DENIED"})
            return
        self._write(200, result)


def build_server(config: GatewayConfig, signer: Signer) -> _Server:
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config.tls["certificate"], config.tls["key"])
    return _Server((config.tls["host"], config.tls["port"]), config, signer, tls)


def serve(config_path: Path) -> int:
    """Foreground hosting only. No deployment, background spawn or discovered-state writes."""
    try:
        with config_path.open("rb") as stream:
            config = GatewayConfig.load(parse(stream.read(MAX_WIRE + 1)))
        with Path(config.signing_key).open("rb") as stream:
            raw = stream.read(16385)
        require(len(raw) <= 16384, "invalid-signing-key")
        key = serialization.load_pem_private_key(raw, password=None)
        require(isinstance(key, Ed25519PrivateKey), "invalid-signing-key")
        configured = next((j for j in config.jwks["keys"] if j["kid"] == config.signing_kid), None)
        require(configured is not None and config.signing_kid not in config.revoked, "signing-trust-required")
        from jwcrypto import jwk

        public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo)
        require(thumbprint(jwk.JWK.from_pem(public).export_public(as_dict=True)) == thumbprint(configured),
                "signing-key-mismatch")
        signer = Signer(key, config.signing_kid, config.issuer, config.installation)
        server = build_server(config, signer)
    except (OSError, ValueError, TypeError, ssl.SSLError):
        print("fleet-manager: Gateway configuration unavailable", file=sys.stderr)
        return 1
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
