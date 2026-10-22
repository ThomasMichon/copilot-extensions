"""Experimental reference implementation; see the parent README."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_proxy
import gateway_server as gateway


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, value, status=200, raw=None, length_delta=0):
        raw = json.dumps(value).encode() if raw is None else raw
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw) + length_delta))
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()

    def do_GET(self):
        self.server.calls.append((self.path, dict(self.headers)))
        self.respond({
            "status": "ok", "service": "chat", "schema": 1,
            "installation_id": "wrong" if self.server.scenario == "identity" else "test-chat",
        })

    def do_POST(self):
        self.server.calls.append((self.path, dict(self.headers)))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        scenario = self.server.scenario
        if scenario == "conflict":
            self.respond({"error": "chat_cursor_conflict", "message": "not forwarded"}, 409)
        elif scenario == "bare-invalid":
            self.respond({"error": "invalid", "message": "not forwarded"}, 400)
        elif scenario == "invalid-json":
            self.respond({}, raw=b'{"cursor":1,"cursor":2}')
        elif scenario == "surrogate":
            self.respond({}, raw=b'{"text":"\\ud800"}')
        elif scenario == "huge":
            self.respond({}, raw=b"x" * (chat_proxy.MAX_RESPONSE + 1))
        elif scenario == "truncated":
            self.respond({"ready": True}, length_delta=10)
        elif scenario == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://not-an-allowed-target.invalid")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
        elif body["op"] in {"wait", "scan"}:
            self.server.wait_started.set()
            self.server.wait_finish.wait(3)
            self.respond({"ready": True, "next_cursor": 3, "unread_count": 1})
        else:
            self.respond({"channel": body.get("channel"), "members": []})


class ChatProxyTests(unittest.TestCase):
    def setUp(self):
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        self.backend.calls, self.backend.scenario = [], "ok"
        self.backend.wait_started = threading.Event()
        self.backend.wait_finish = threading.Event()
        self.frontend = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        self.threads = [
            threading.Thread(target=self.backend.serve_forever),
            threading.Thread(target=self.frontend.serve_forever),
        ]
        for thread in self.threads:
            thread.start()
        self.env = patch.dict(os.environ, {
            "HGF_CHAT_ENDPOINT": f"http://127.0.0.1:{self.backend.server_port}",
            "HGF_CHAT_INSTALLATION_ID": "test-chat",
        })
        self.env.start()
        self.auth = patch.object(gateway, "validate_bearer", return_value={"oid": "test"})
        self.auth.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.backend.wait_finish.set()
        self.auth.stop()
        self.env.stop()
        for server in (self.frontend, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()

    def request(self, body=None, *, path="/api/v1/chat", method="POST", raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.frontend.server_port, timeout=8)
        try:
            data = json.dumps(body).encode() if raw is None else raw
            headers = {"Content-Type": "application/json", "Authorization": "fake-user-credential"}
            connection.request(method, path, body=data if method == "POST" else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_success_uses_fixed_path_and_never_forwards_credential(self):
        status, result = self.request({"op": "members", "channel": "room"})
        self.assertEqual(status, 200)
        self.assertEqual(result["members"], [])
        self.assertEqual([x[0] for x in self.backend.calls], ["/health", "/v1/chat"])
        for _, headers in self.backend.calls:
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("fake-user-credential", json.dumps(headers))

    def test_auth_failure_never_contacts_backend(self):
        self.auth.stop()
        self.auth = patch.object(gateway, "validate_bearer", side_effect=gateway.AuthError(401, "denied"))
        self.auth.start()
        status, _ = self.request({"op": "members", "channel": "room"})
        self.assertEqual(status, 401)
        self.assertEqual(self.backend.calls, [])

    def test_unconfigured_and_unsafe_destinations_refuse(self):
        cases = ("", "http://user:pass@example", "http://example/path", "http://example/?q=x")
        for value in cases:
            with self.subTest(value=value), patch.dict(os.environ, {"HGF_CHAT_ENDPOINT": value}):
                status, result = self.request({"op": "members", "channel": "room"})
                self.assertEqual(status, 503)
                self.assertTrue(result["error"].startswith("chat_"))
        self.assertEqual(self.backend.calls, [])

    def test_health_identity_mismatch_stops_before_rpc(self):
        self.backend.scenario = "identity"
        status, result = self.request({"op": "members", "channel": "room"})
        self.assertEqual((status, result["error"]), (503, "chat_backend_identity_mismatch"))
        self.assertEqual([x[0] for x in self.backend.calls], ["/health"])

    def test_strict_body_refusals(self):
        for raw in (b'{"op":"members","op":"post"}', b'{"op":"wait","timeout":NaN}',
                    b'{"op":"wait","timeout":1e999}', b'{"op":"members","x":"\\ud800"}',
                    b"[]", b"x" * (chat_proxy.MAX_BODY + 1)):
            with self.subTest(raw=raw[:50]):
                status, _ = self.request(raw=raw)
                self.assertEqual(status, 400)
        self.assertEqual(self.backend.calls, [])

    def test_invalid_wait_types_do_not_contact_backend(self):
        for value in (True, -1, 301, "30", None):
            with self.subTest(value=value):
                status, result = self.request({"op": "wait", "timeout": value})
                self.assertEqual((status, result["error"]), (400, "chat_invalid_timeout"))
        self.assertEqual(self.backend.calls, [])

    def test_backend_conflict_preserves_code_not_body(self):
        self.backend.scenario = "conflict"
        status, result = self.request({"op": "cursor", "channel": "room"})
        self.assertEqual(status, 409)
        self.assertEqual(result, {"error": "chat_cursor_conflict"})

    def test_backend_validation_refusal_is_not_misreported_as_unknown_502(self):
        self.backend.scenario = "bare-invalid"
        status, result = self.request({"op": "join", "channel": "room"})
        self.assertEqual((status, result), (400, {"error": "chat_invalid"}))

    def test_malformed_backend_and_redirect_are_explicit_failures(self):
        for scenario in ("invalid-json", "surrogate", "huge", "truncated", "redirect"):
            with self.subTest(scenario=scenario):
                self.backend.scenario = scenario
                status, result = self.request({"op": "members", "channel": "room"})
                self.assertEqual(status, 502)
                self.assertTrue(result["error"].startswith("chat_backend_"))

    def test_wait_slot_exhaustion_does_not_starve_ordinary_chat(self):
        with patch.object(chat_proxy, "_waiters", threading.BoundedSemaphore(0)):
            status, result = self.request({"op": "wait", "channel": "room", "timeout": 0})
            self.assertEqual((status, result["error"]), (503, "chat_gateway_busy"))
            self.assertEqual(self.request({"op": "members", "channel": "room"})[0], 200)

    def test_scan_is_explicitly_long_poll_admitted_and_auth_gated(self):
        payload = {"op": "scan", "codename": "alice", "epoch": None,
                   "after": [{"channel": "room", "event_id": 0}], "timeout": 0}
        with patch.object(chat_proxy, "_waiters", threading.BoundedSemaphore(0)):
            self.assertEqual(self.request(payload), (503, {"error": "chat_gateway_busy"}))
            self.assertEqual(self.request({"op": "members", "channel": "room"})[0], 200)
        self.assertEqual(self.backend.calls[-1][0], "/v1/chat")
        self.backend.calls.clear()
        for timeout in (True, -1, 301, "30", None):
            self.assertEqual(self.request({**payload, "timeout": timeout}),
                             (400, {"error": "chat_invalid_timeout"}))
        self.assertEqual(self.backend.calls, [])
        self.auth.stop()
        self.auth = patch.object(gateway, "validate_bearer",
                                 side_effect=gateway.AuthError(401, "denied"))
        self.auth.start()
        self.assertEqual(self.request(payload)[0], 401)
        self.assertEqual(self.backend.calls, [])

    def test_scan_preserves_backend_hold_codes_and_long_poll_deadline(self):
        payload = {"op": "scan", "codename": "alice", "timeout": 300}
        with patch.object(chat_proxy, "_forward", return_value={}) as forward:
            self.assertEqual(chat_proxy.forward(payload), {})
            forward.assert_called_once_with(payload, 300)
        for code in ("scan_epoch_mismatch", "scan_gap", "notjoined"):
            with self.subTest(code=code), patch.object(
                    Backend, "do_POST",
                    lambda handler: handler.respond({"error": code, "message": "private"}, 409)):
                self.assertEqual(self.request(payload), (409, {"error": "chat_" + code}))

    def test_held_wait_does_not_block_health_status_or_post(self):
        results = []
        thread = threading.Thread(target=lambda: results.append(self.request({
            "op": "wait", "channel": "room", "timeout": 3,
        })))
        thread.start()
        try:
            self.assertTrue(self.backend.wait_started.wait(3))
            self.assertEqual(self.request(path="/health", method="GET")[0], 200)
            self.assertEqual(self.request(path="/api/v1/status", method="GET")[0], 200)
            self.assertEqual(self.request({"op": "post", "channel": "room"})[0], 200)
            self.assertEqual(self.request({"op": "members", "channel": "room"})[0], 200)
        finally:
            self.backend.wait_finish.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0][0], 200)

    def test_unknown_operation_cannot_select_backend_actions(self):
        self.assertEqual(self.request({"op": "http://other.invalid"})[0], 400)
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
