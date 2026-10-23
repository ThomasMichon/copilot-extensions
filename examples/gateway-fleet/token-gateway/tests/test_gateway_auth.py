"""Experimental reference implementation; see the parent README."""

import http.client
import io
import json
import os
import socket
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_server as gateway


class FleetAuthorizationTests(unittest.TestCase):
    operator = "00000004-0000-4000-8000-000000000000"
    other = "00000005-0000-4000-8000-000000000000"

    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.keys = patch.object(
            gateway._jwks_client, "get_signing_key_from_jwt",
            return_value=SimpleNamespace(key=self.key.public_key()),
        )
        self.key_resolver = self.keys.start()
        self.addCleanup(self.keys.stop)

    def header(self, **changes):
        claims = {
            "iss": gateway.ISSUER, "aud": gateway.APP_ID, "exp": time.time() + 120,
            "tid": gateway.TENANT_ID, "oid": self.operator,
            "roles": [gateway.REQUIRED_ROLE],
        }
        claims.update(changes)
        token = jwt.encode(claims, self.key, algorithm="RS256")
        scheme = "Bear" + "er "
        header = scheme + token
        self.assertEqual(header[len(scheme):], token)
        return header

    def test_dedicated_fleet_role_accepts_different_verified_principals(self):
        for oid in (self.operator, self.other, None, 1, ""):
            with self.subTest(oid=oid):
                self.assertEqual(gateway.validate_bearer(self.header(oid=oid))["oid"], oid)

    def test_explicit_tenant_and_dedicated_fleet_role_remain_required(self):
        for changes in (
            {"tid": self.other}, {"tid": None},
            {"roles": []}, {"roles": "GatewayClient"}, {"roles": None},
            {"roles": ["OtherRole"]}, {"roles": {"GatewayClient": True}},
        ):
            with self.subTest(changes=changes), self.assertRaises(gateway.AuthError) as error:
                gateway.validate_bearer(self.header(**changes))
            self.assertEqual(error.exception.status, 403)

    def test_key_lookup_and_connection_failures_have_explicit_auth_responses(self):
        cases = (
            (gateway.PyJWKClientConnectionError, 503, "signing keys unavailable"),
            (gateway.PyJWKClientError, 401, "signing key not found"),
        )
        for exception, status, message in cases:
            with self.subTest(exception=exception):
                self.key_resolver.side_effect = exception("synthetic lookup failure")
                with self.assertRaises(gateway.AuthError) as error:
                    gateway.validate_bearer(self.header())
                self.assertEqual((error.exception.status, error.exception.message), (status, message))

    def test_default_http_listener_does_not_expose_credentials_to_the_network(self):
        self.assertEqual(gateway.LISTEN_HOST, "127.0.0.1")

    def test_signature_issuer_audience_and_expiration_remain_required(self):
        for changes in (
            {"exp": time.time() - 60}, {"aud": "other-audience"},
            {"iss": "https://other-issuer.invalid/"},
        ):
            with self.subTest(changes=changes), self.assertRaises(gateway.AuthError) as error:
                gateway.validate_bearer(self.header(**changes))
            self.assertEqual(error.exception.status, 401)
        self.key_resolver.return_value = SimpleNamespace(
            key=rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key(),
        )
        with self.assertRaises(gateway.AuthError) as error:
            gateway.validate_bearer(self.header())
        self.assertEqual(error.exception.status, 401)

    def test_legacy_object_id_configuration_is_not_required_for_requests_or_startup(self):
        for value in ("", "not-an-object-id", "00000006-0000-4000-8000-000000000000",
                      " " + self.operator, "{" + self.operator + "}"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"HGF_OPERATOR_OBJECT_ID": value},
            ):
                self.assertEqual(gateway.validate_bearer(self.header())["oid"], self.operator)
                with patch.object(gateway, "_setup_logging"), patch.object(
                    gateway, "ThreadingHTTPServer",
                ) as server:
                    gateway.main()
                    server.assert_called_once_with(
                        (gateway.LISTEN_HOST, gateway.LISTEN_PORT), gateway.Handler,
                    )
                    server.return_value.serve_forever.assert_called_once_with()
                    server.return_value.server_close.assert_called_once_with()
        with patch.dict(os.environ, clear=True):
            self.assertEqual(gateway.validate_bearer(self.header())["oid"], self.operator)

    def test_missing_signed_claims_are_denied(self):
        for missing in ("exp", "aud", "iss", "tid", "roles"):
            claims = {
                "iss": gateway.ISSUER, "aud": gateway.APP_ID, "exp": time.time() + 120,
                "tid": gateway.TENANT_ID, "roles": [gateway.REQUIRED_ROLE],
            }
            del claims[missing]
            token = jwt.encode(claims, self.key, algorithm="RS256")
            scheme = "Bear" + "er "
            with self.subTest(missing=missing), self.assertRaises(gateway.AuthError) as error:
                gateway.validate_bearer(scheme + token)
            self.assertEqual(error.exception.status, 401 if missing in ("exp", "aud", "iss") else 403)

    def test_denial_half_closes_before_bounded_rejected_body_drain(self):
        handler = object.__new__(gateway.Handler)
        handler.wfile = io.BytesIO()
        connection = Mock()
        connection.recv.side_effect = lambda size: b"x" * size
        handler.connection = connection
        with patch.object(handler, "_json") as response, patch.object(
            gateway, "time", SimpleNamespace(monotonic=lambda: 0.0),
        ):
            handler._reject_auth(gateway.AuthError(403, "denied"))
        response.assert_called_once_with(403, {"error": "denied"})
        self.assertTrue(handler.close_connection)
        self.assertEqual(connection.method_calls[0], ("shutdown", (socket.SHUT_WR,), {}))
        reads = [call.args[0] for call in connection.recv.call_args_list]
        self.assertEqual(sum(reads), 64 * 1024)
        self.assertTrue(all(size <= 4096 for size in reads))
        self.assertTrue(all(0 < call.args[0] <= 0.1
                            for call in connection.settimeout.call_args_list))

    def test_denial_disconnect_logs_only_its_error_type(self):
        handler = object.__new__(gateway.Handler)
        handler.wfile = io.BytesIO()
        handler.connection = Mock()
        handler.connection.recv.side_effect = OSError("sensitive rejected body")
        with patch.object(handler, "_json"), self.assertLogs(gateway.log, level="DEBUG") as logs:
            handler._reject_auth(gateway.AuthError(401, "denied"))
        self.assertTrue(handler.close_connection)
        self.assertIn("OSError", " ".join(logs.output))
        self.assertNotIn("sensitive rejected body", " ".join(logs.output))

    def test_all_protected_routes_reject_unauthorized_tokens_before_effects(self):
        server = gateway.ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(gateway, "search_index") as index, patch(
                "chat_proxy.forward",
            ) as chat, patch.object(gateway, "_push_credential") as push, patch.object(
                gateway, "_relay_get_azure_token",
            ) as relay:
                for method, path in (
                    ("GET", "/api/v1/status"),
                    ("GET", "/api/v1/index/search?backend_url=untrusted"),
                    ("POST", "/api/v1/chat"),
                    ("POST", "/api/v1/credentials/push"),
                    ("POST", "/api/v1/relay"),
                ):
                    for header, status in (
                        (None, 401), ("invalid", 401),
                        (self.header(oid=self.other, roles=[]), 403),
                        (self.header(tid=self.other), 403),
                        (self.header(aud="other-audience"), 401),
                        (self.header(exp=time.time() - 60), 401),
                        (self.header(iss="https://other-issuer.invalid/"), 401),
                    ):
                        with self.subTest(path=path, status=status):
                            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                            try:
                                connection.request(
                                    method, path, body="{",
                                    headers={} if header is None else {"Authorization": header},
                                )
                                response = connection.getresponse()
                                self.assertEqual(response.status, status)
                                self.assertIn("error", json.loads(response.read()))
                            finally:
                                connection.close()
                for effect in (index, chat, push, relay):
                    effect.assert_not_called()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("GET", "/api/v1/status", headers={
                    "Authorization": self.header(oid=self.other),
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["oid"], self.other)
            finally:
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
