"""Owned test-only process roles with synthetic secrets and a test issuer/CA."""

import sys

print("fixture: entered", file=sys.stderr, flush=True)

import json
import ssl
import time
import jwt
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from fleet_manager.gateway_auth import Signer, thumbprint
from fleet_manager.gateway_client import Discovery, GatewayClient, consume_credential
from fleet_manager.gateway_contract import (
    MAX_WIRE,
    GatewayConfig,
    GatewayError,
    encode,
    parse,
)
from fleet_manager.gateway_controller import Controller
from fleet_manager.gateway_index import ScopedIndexBackend
from fleet_manager.gateway_server import build_server
from fleet_manager.planning import digest
from gateway_fixtures import (
    BoundedTLSFixtureServer,
    SyntheticScopedProvider,
    credential_request,
    query,
)
from jwcrypto import jwk

print("fixture: imports ready", file=sys.stderr, flush=True)


def key(root, principal):
    return serialization.load_pem_private_key((root / f"{principal}.pem").read_bytes(), password=None)


def client(root, value, principal):
    peer = next(e for e in value["enrollments"] if e["principal"] == principal)
    ready = json.loads((root / "gateway-ready.json").read_text(encoding="utf-8"))
    discovery = Discovery(f'https://127.0.0.1:{ready["port"]}', str(root / "tls.pem"),
                          value["issuer"], value["audience"], value["installation"],
                          {"keys": [j for j in value["jwks"]["keys"] if j["kid"] == value["signing_kid"]]})

    def access():
        grant = {k: peer[k] for k in ("role", "service", "installation", "generation",
                                     "sources", "corpora", "accounts", "audiences", "scopes", "jobs")}
        grant |= {"fleet": value["fleet"], "cnf": {"jkt": thumbprint(peer["proof_jwk"])}}
        now = int(time.time())
        claims = grant | {"iss": value["identity"]["issuer"], "aud": value["audience"],
                          "sub": principal, "iat": now, "nbf": now, "exp": now + 120}
        return jwt.encode(claims, key(root, "identity-issuer"), algorithm="ES256",
                          headers={"kid": "issuer", "typ": "at+jwt"})

    return GatewayClient(discovery, principal, key(root, "identity-" + principal), access)


def ready(root, role, value):
    (root / f"{role}-ready.json").write_text(json.dumps(value), encoding="utf-8")


def main():
    role, directory = sys.argv[1:]
    root = Path(directory)
    value = parse((root / "config.json").read_bytes())
    config = GatewayConfig.load(value)
    print("fixture: config ready", file=sys.stderr, flush=True)
    if role == "gateway":
        server = build_server(config, Signer(key(root, "gateway"), "gateway", value["issuer"],
                                             value["installation"]))
        ready(root, role, {"port": server.server_port, "epoch": server.gateway.epoch})
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.server_close()
    elif role == "backend":
        def retrieve(scope, parameters):
            rows = [{"source": "source-a", "corpus": "corpus-a", "content": "allowed"},
                    {"source": "source-b", "corpus": "corpus-a", "content": "denied"}]
            candidates = [r for r in rows if r["corpus"] == scope.corpus and r["source"] in scope.sources]
            return {"hits": candidates[:parameters["limit"]]}

        backend = ScopedIndexBackend(
            config.backends[0], Signer(key(root, "backend"), "backend", "backend-issuer", "backend-a"),
            gateway_keys={"keys": [j for j in config.jwks["keys"] if j["kid"] == config.signing_kid]},
            gateway_issuer=config.issuer, gateway_subject=config.installation,
            fleet=config.fleet, retrieve=retrieve)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.connection.settimeout(3)
                length = int(self.headers["Content-Length"])
                if not 0 < length <= MAX_WIRE:
                    self.send_error(403)
                    return
                body = parse(self.rfile.read(length))
                if self.path == "/v1/index/admit":
                    response = backend.admit(body)
                elif self.path == "/v1/index/query":
                    response = backend.execute(body)
                else:
                    self.send_error(403)
                    return
                raw = encode(response)
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(root / "tls.pem"), str(root / "tls-key.pem"))
        server = BoundedTLSFixtureServer(("127.0.0.1", 0), Handler, context)
        print("fixture: backend bound", file=sys.stderr, flush=True)
        ready(root, role, {"port": server.server_port})
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.server_close()
    elif role == "controller":
        controller = Controller(
            client(root, value, "controller"), SyntheticScopedProvider(),
            Signer(key(root, "controller"), "proof", "controller", "controller"),
            fleet=config.fleet, generation=1, lease_seconds=config.lease_seconds)
        controller.start()
        ready(root, role, {"state": "SUBSCRIBED"})
        try:
            while True:
                controller.step()
                time.sleep(0.15)
        except GatewayError:
            print('{"state":"UNKNOWN"}')
    elif role == "query":
        sdk = client(root, value, "alice")
        sdk.connect()
        result = sdk.query(query())
        assert result["result"]["hits"] == [{"source": "source-a", "corpus": "corpus-a", "content": "allowed"}]
        print('{"state":"SCOPED"}')
    elif role == "worker":
        sdk = client(root, value, "worker")
        sdk.connect()
        request = credential_request()
        sdk.request_credential(request)
        deadline = time.monotonic() + config.lease_seconds
        while time.monotonic() < deadline:
            result = sdk.credential_result(request["request_id"], digest(request))
            if result["state"] == "READY":
                recipient = jwk.JWK.from_json((root / "recipient.json").read_text(encoding="utf-8"))
                material = consume_credential(
                    sdk, request, result, recipient_key=recipient,
                    controller_keys={"keys": [config.enrollments[3].receipt_jwk]},
                    controller="controller", controller_generation=1)
                assert material.token == "SYNTHETIC-SECRET-NEVER-PRODUCTION"
                print('{"state":"ACKED"}')
                return
            if result["state"] not in ("PENDING", "DISPATCHED"):
                raise GatewayError("credential-unavailable")
            time.sleep(0.1)
        raise GatewayError("credential-unavailable")
    elif role == "probe":
        sdk = client(root, value, "worker")
        sdk.connect()
        request = credential_request()
        print(json.dumps(sdk.credential_result(request["request_id"], digest(request))))
    else:
        raise GatewayError("fixture-role-unsupported")


if __name__ == "__main__":
    try:
        main()
    except GatewayError:
        print('{"state":"UNAVAILABLE"}')
        raise SystemExit(2) from None
