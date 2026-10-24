"""Experimental reference implementation; see the parent README."""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gateway_server as gateway
import index_search

REAL_VALIDATE_BEARER = gateway.validate_bearer


class FakeBackend(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        self.server.calls.append((self.path, dict(self.headers)))
        scenario = self.server.scenario
        if self.path == "/health":
            payload = {
                "status": "draining" if scenario == "health-draining" else "ok",
                "installationId": (
                    "other-installation" if scenario == "health-mismatch"
                    else "test-installation"
                ),
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        params = parse_qs(urlsplit(self.path).query)
        payload = {
            "query": params["q"][0], "available": True,
            "hits": [{"id": "chunk-1", "score": 0.9, "text": "matched",
                      "metadata": {"trust_domain": "operator"}}],
        }
        if scenario == "timeout":
            time.sleep(0.2)
        elif scenario == "unavailable":
            payload.update(available=False, hits=[])
        elif scenario == "empty":
            payload["hits"] = []
        elif scenario == "wrong-query":
            payload["query"] = "different query"
        elif scenario == "wrong-available":
            payload["available"] = 1
        elif scenario == "wrong-hits":
            payload["hits"] = ["not an object"]
        elif scenario == "too-many-hits":
            payload["hits"] *= 101
        elif scenario == "missing-hits":
            del payload["hits"]
        elif scenario == "top-level-array":
            payload = []
        raw = json.dumps(payload).encode("utf-8")
        if scenario == "invalid-json":
            raw = b"not JSON"
        elif scenario == "invalid-utf8":
            raw = b"\xff"
        elif scenario == "duplicate-json":
            raw = b'{"query":"search","available":true,"available":false,"hits":[]}'
        elif scenario == "nonfinite":
            raw = b'{"query":"search","available":true,"hits":[{"score":NaN}]}'
        elif scenario == "overflow":
            raw = b'{"query":"search","available":true,"hits":[{"score":1e999}]}'
        elif scenario == "surrogate":
            raw = b'{"query":"search","available":true,"hits":[{"text":"\\ud800"}]}'
        elif scenario == "deep-json":
            raw = b"[" * 1100 + b"]" * 1100
        elif scenario in {"oversized", "stream-oversized"}:
            raw = b" " * (index_search.MAX_RESPONSE_BYTES + 1)
        elif scenario == "serialized-oversized":
            payload["hits"] = [{"text": "\u00e9" * 200000}]
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status = {"rejection": 409, "down": 503, "redirect": 302}.get(scenario, 200)
        self.send_response(status)
        self.send_header(
            "Content-Type", "text/html" if scenario == "wrong-content-type"
            else "application/json; charset=utf-8",
        )
        if scenario == "encoded":
            self.send_header("Content-Encoding", "gzip")
        if scenario == "redirect":
            self.send_header("Location", "/should-not-follow")
        if scenario == "bad-length":
            self.send_header("Content-Length", "-1")
        elif scenario == "huge-length":
            self.send_header("Content-Length", "9" * 5000)
        elif scenario == "truncated":
            self.send_header("Content-Length", str(len(raw) + 5))
        elif scenario not in {"stream-oversized", "drip", "chunked", "bad-chunk"}:
            self.send_header("Content-Length", str(len(raw)))
        if scenario in {"chunked", "bad-chunk"}:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            if scenario == "chunked":
                self.wfile.write(f"{len(raw):x}\r\n".encode("ascii") + raw + b"\r\n0\r\n\r\n")
            elif scenario == "bad-chunk":
                self.wfile.write(b"not-a-chunk-size\r\n")
            elif scenario == "drip":
                for _ in range(30):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.015)
            else:
                self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):

            pass


def authenticated(header: str | None) -> dict:
    if header != "test-authorized":
        raise gateway.AuthError(401, "test auth denied")
    return {"oid": "test-principal", "roles": ["GatewayClient"], "exp": 9999999999}


class IndexSearchHandlerTests(unittest.TestCase):
    def _start_servers(self) -> None:
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.threads = []
        for server in (self.backend, self.gateway):
            server.daemon_threads = False
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def tearDown(self) -> None:
        for server in (self.gateway, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def setUp(self) -> None:
        self._start_servers()
        self.backend.calls = []
        self.backend.scenario = "success"
        self.env = patch.dict(os.environ, {
            "HGF_INDEX_ENDPOINT": f"http://127.0.0.1:{self.backend.server_port}",
            "HGF_INDEX_INSTALLATION_ID": "test-installation",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        verifier = patch.object(gateway, "validate_bearer", side_effect=authenticated)
        self.verifier = verifier.start()
        self.addCleanup(verifier.stop)
        warnings = patch.object(index_search.log, "warning")
        self.warnings = warnings.start()
        self.addCleanup(warnings.stop)

    def request(self, path: str = "/api/v1/index/search?q=search", *,
                authorized: bool = True, headers: dict | None = None,
                method: str = "GET", body: str | None = None) -> tuple[int, dict]:
        sent_headers = {"Authorization": "test-authorized"} if authorized else {}
        sent_headers.update(headers or {})
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_port, timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=sent_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def assert_error(self, status: int, code: str, **kwargs) -> None:
        self.assertEqual(self.request(**kwargs), (status, {"error": code}))

    def test_unauthenticated_request_cannot_touch_backend(self) -> None:
        self.assert_error(401, "test auth denied", authorized=False)
        self.assertFalse(self.backend.calls)
        self.verifier.assert_called_once_with(None)

    def test_role_denial_and_auth_precedes_parameter_validation(self) -> None:
        self.verifier.side_effect = gateway.AuthError(403, "role denied")
        self.assert_error(403, "role denied", path="/api/v1/index/search?backend_url=x")
        self.assertFalse(self.backend.calls)

    def test_real_jwt_verification_is_preserved(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        import jwt

        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {
            "iss": gateway.ISSUER, "aud": gateway.APP_ID, "exp": time.time() + 120,
            "roles": ["GatewayClient"], "tid": gateway.TENANT_ID,
            "oid": "00000004-0000-4000-8000-000000000000",
        }
        scheme = "Bear" + "er "
        with patch.object(
            gateway, "validate_bearer", REAL_VALIDATE_BEARER,
        ), patch.object(
            gateway._jwks_client, "get_signing_key_from_jwt",
            return_value=SimpleNamespace(key=signing_key.public_key()),
        ):
            token = jwt.encode(claims, signing_key, algorithm="RS256")
            self.assertEqual(self.request(headers={"Authorization": scheme + token})[0], 200)
            cases = [
                ({"exp": time.time() - 60}, 401),
                ({"aud": "wrong-audience"}, 401),
                ({"iss": "https://wrong-issuer.invalid/"}, 401),
                ({"roles": []}, 403),
            ]
            for changes, expected in cases:
                with self.subTest(changes=changes):
                    token = jwt.encode({**claims, **changes}, signing_key, algorithm="RS256")
                    self.assertEqual(
                        self.request(headers={"Authorization": scheme + token})[0], expected,
                    )
            missing_exp = {key: value for key, value in claims.items() if key != "exp"}
            token = jwt.encode(missing_exp, signing_key, algorithm="RS256")
            self.assertEqual(self.request(headers={"Authorization": scheme + token})[0], 401)
            different_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            token = jwt.encode(claims, different_key, algorithm="RS256")
            self.assertEqual(self.request(headers={"Authorization": scheme + token})[0], 401)
            token = jwt.encode(claims, "test-only-key-material-for-HS256-case", algorithm="HS256")
            self.assertEqual(self.request(headers={"Authorization": scheme + token})[0], 401)
        self.assertEqual(len(self.backend.calls), 2)

    def test_backend_identity_mismatch_prevents_search(self) -> None:
        self.backend.scenario = "health-mismatch"
        self.assert_error(503, "index_identity_mismatch")
        self.assertEqual([call[0] for call in self.backend.calls], ["/health"])

    def test_backend_draining_prevents_search(self) -> None:
        self.backend.scenario = "health-draining"
        self.assert_error(503, "index_unavailable")
        self.assertEqual([call[0] for call in self.backend.calls], ["/health"])

    def test_success_forwards_supported_fields_and_only_installation_header(self) -> None:
        parameters = {
            "q": "search & repo=x /? # \u00e9", "source": "adopter-config",
            "language": "python", "repo": "owner/repo", "limit": "3",
        }
        status, result = self.request(
            "/api/v1/index/search?" + urlencode(parameters),
            headers={"X-Agent-Index-Installation-Id": "caller-override",
                     "X-Untrusted": "do-not-forward"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"query", "available", "hits"})
        self.assertEqual(result["query"], parameters["q"])
        self.assertIs(result["available"], True)
        self.assertEqual(result["hits"][0]["metadata"]["trust_domain"], "operator")
        path, headers = self.backend.calls[-1]
        self.assertEqual(urlsplit(path).path, "/search")
        self.assertEqual(parse_qs(urlsplit(path).query),
                         {key: [value] for key, value in parameters.items()})
        self.assertEqual(headers["X-Agent-Index-Installation-Id"], "test-installation")
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("X-Untrusted", headers)

    def test_default_limit_and_empty_available_index(self) -> None:
        self.backend.scenario = "empty"
        self.assertEqual(self.request(), (200, {
            "query": "search", "available": True, "hits": [],
        }))
        self.assertEqual(parse_qs(urlsplit(self.backend.calls[-1][0]).query)["limit"], ["10"])

    def test_chunked_backend_success(self) -> None:
        self.backend.scenario = "chunked"
        status, payload = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(payload["query"], "search")
        self.assertEqual(len(payload["hits"]), 1)

    def test_parameter_rejections_do_not_touch_backend(self) -> None:
        queries = [
            "", "q=", "q=++", "q=search&url=http://127.0.0.1/",
            "q=x&backend_url=http://elsewhere", "q=x&headers=anything",
            "q=x&path=/health", "q=x&action=exec", "q=x&q=y",
            "q=x&%71=y", "q=x&limit=2&limit=3",
            "q=x&source=a&source=b", "q=x&source=",
            "q=" + "x" * 2049, "q=x&repo=" + "r" * 257,
            "q=x&language=" + "l" * 257, "q=x&source=" + "s" * 257,
            "q=x&limit=0", "q=x&limit=101", "q=x&limit=-1",
            "q=x&limit=1.5", "q=x&limit=1e2", "q=x&limit=",
            "q=x&limit=%D9%A1", "q=x&limit=+1", "q=x&limit=0001",
            "q=x%0d%0aX-Header:injected", "q=x%00", "q=x%7f",
            "q=x&source=%0a", "q=%FF", "q=%", "q=%GG",
            "q=x&repo", "q=x&repo=a&language=b&source=c&limit=1&other=d",
            "q=" + "%78" * 6000,
        ]
        for query in queries:
            with self.subTest(query=query[:80]):
                self.assert_error(
                    400, "index_invalid_parameters", path="/api/v1/index/search?" + query,
                )
        self.assertFalse(self.backend.calls)

    def test_limit_boundaries_and_query_bound(self) -> None:
        for limit in ("1", "100"):
            with self.subTest(limit=limit):
                self.assertEqual(self.request(
                    "/api/v1/index/search?" + urlencode({"q": "x" * 2048, "limit": limit}),
                )[0], 200)

    def test_injection_shaped_query_stays_data_at_fixed_path(self) -> None:
        query = "http://evil.invalid/search?url=x; $(echo nope) & source=other"
        self.assertEqual(self.request(
            "/api/v1/index/search?" + urlencode({"q": query}),
        )[0], 200)
        path, _ = self.backend.calls[-1]
        self.assertEqual(urlsplit(path).path, "/search")
        self.assertEqual(parse_qs(urlsplit(path).query), {"q": [query], "limit": ["10"]})

    def test_missing_configuration(self) -> None:
        for name in ("HGF_INDEX_ENDPOINT", "HGF_INDEX_INSTALLATION_ID"):
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                self.assert_error(503, "index_not_configured")
        self.assertFalse(self.backend.calls)

    def test_invalid_configuration(self) -> None:
        endpoints = [
            "file:///tmp/index", "ftp://localhost", "http://user:password@localhost",
            "http://localhost/search", "http://localhost?x=1", "http://localhost#x",
            "http://localhost:invalid", "http://localhost:0", "http://localhost:65536",
            "http://localhost\r\nX:injected", "http://[", "http://",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint), patch.dict(
                os.environ, {"HGF_INDEX_ENDPOINT": endpoint},
            ):
                self.assert_error(503, "index_configuration_invalid")
        for identity in ("x\r\nHeader:bad", "x" * 257, "\u00e9", "with space"):
            with self.subTest(identity=identity), patch.dict(
                os.environ, {"HGF_INDEX_INSTALLATION_ID": identity},
            ):
                self.assert_error(503, "index_configuration_invalid")
        self.assertFalse(self.backend.calls)

    def test_unavailable_is_not_empty_success(self) -> None:
        self.backend.scenario = "unavailable"
        self.assert_error(503, "index_unavailable")

    def test_backend_rejections_and_redirect_are_not_followed(self) -> None:
        for scenario in ("rejection", "down", "redirect"):
            with self.subTest(scenario=scenario):
                self.backend.scenario = scenario
                self.assert_error(502, "index_backend_rejected")
        self.assertEqual(len(self.backend.calls), 6)

    def test_unreachable_backend(self) -> None:

        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with patch.dict(os.environ, {"HGF_INDEX_ENDPOINT": f"http://127.0.0.1:{port}"}):
            self.assert_error(503, "index_backend_unreachable")

    def test_backend_timeout(self) -> None:
        self.backend.scenario = "timeout"
        with patch.object(index_search, "READ_TIMEOUT", 0.04):
            self.assert_error(504, "index_backend_timeout")
        self.warnings.assert_called_once_with(
            "index search failed: status=%d code=%s", 504, "index_backend_timeout",
        )

    def test_total_body_deadline_defeats_drip_response(self) -> None:
        self.backend.scenario = "drip"
        start = time.monotonic()
        with patch.object(index_search, "REQUEST_TIMEOUT", 0.08):
            self.assert_error(504, "index_backend_timeout")
        self.assertLess(time.monotonic() - start, 0.5)

    def test_response_size_bound_with_and_without_content_length(self) -> None:
        for scenario in ("oversized", "stream-oversized", "serialized-oversized"):
            with self.subTest(scenario=scenario):
                self.backend.scenario = scenario
                self.assert_error(502, "index_response_too_large")

    def test_invalid_backend_response(self) -> None:
        for scenario in (
            "invalid-json", "invalid-utf8", "wrong-content-type", "encoded", "bad-length",
            "huge-length", "bad-chunk",
            "wrong-query", "wrong-available", "wrong-hits", "too-many-hits", "missing-hits",
            "top-level-array", "duplicate-json", "nonfinite", "overflow", "surrogate",
            "deep-json", "truncated",
        ):
            with self.subTest(scenario=scenario):
                self.backend.scenario = scenario
                self.assert_error(502, "index_invalid_response")

    def test_capacity_limit_and_release_after_error(self) -> None:
        with patch.object(index_search, "_slots", threading.BoundedSemaphore(1)) as slots:
            slots.acquire()
            try:
                self.assert_error(503, "index_busy")
                self.assertFalse(self.backend.calls)
            finally:
                slots.release()
            self.backend.scenario = "rejection"
            self.assert_error(502, "index_backend_rejected")
            self.backend.scenario = "success"
            self.assertEqual(self.request()[0], 200)

    def test_existing_health_status_relay_and_unknown_routes(self) -> None:
        status, health = self.request("/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertIsInstance(health["time"], float)
        self.verifier.assert_not_called()
        status, claims = self.request("/api/v1/status")
        self.assertEqual(status, 200)
        self.assertTrue(claims["authenticated"])
        self.assertEqual(claims["oid"], "test-principal")
        self.assert_error(401, "test auth denied", path="/api/v1/status", authorized=False)
        self.assertEqual(self.request(
            "/api/v1/relay", method="POST", body='{"action":"capabilities"}',
        ), (200, {"capabilities": ["get-azure-token"]}))
        self.assert_error(404, "not found", path="/api/v1/index/health")
        self.assertFalse(self.backend.calls)


if __name__ == "__main__":
    unittest.main()
