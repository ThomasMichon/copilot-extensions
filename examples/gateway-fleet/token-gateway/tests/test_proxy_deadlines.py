import os
import json
from pathlib import Path
import socketserver
import sys
import threading
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_proxy
import index_search


class SlowHeaders(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(1)
        try:
            request = self.request.recv(8192)
            if self.server.fast_health and request.startswith(b"GET /health "):
                body = json.dumps({
                    "status": "ok", "installationId": "example-index",
                    "service": "chat", "schema": 1, "installation_id": "example-chat",
                }).encode()
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body,
                )
                return
            self.request.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: ",
            )
            for _ in range(120):
                if self.server.stop.wait(0.025):
                    return
                self.request.sendall(b"x")
            self.request.sendall(b"\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass


class ProxyDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SlowHeaders)
        self.server.stop = threading.Event()
        self.server.fast_health = False
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.server.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())

    def assert_slots_available(self, semaphore, count):
        acquired = 0
        try:
            for _ in range(count):
                self.assertTrue(semaphore.acquire(blocking=False))
                acquired += 1
        finally:
            for _ in range(acquired):
                semaphore.release()

    def test_index_header_drip_cannot_hold_a_slot_past_absolute_deadline(self):
        endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        for fast_health in (False, True):
            self.server.fast_health = fast_health
            with self.subTest(fast_health=fast_health), patch.dict(os.environ, {
                "HGF_INDEX_ENDPOINT": endpoint, "HGF_INDEX_INSTALLATION_ID": "example-index",
            }), patch.object(index_search, "REQUEST_TIMEOUT", 0.2):
                started = time.monotonic()
                with self.assertRaises(index_search.IndexSearchError) as error:
                    index_search.search_index("q=example")
            self.assertEqual((error.exception.status, error.exception.code), (504, "index_backend_timeout"))
            self.assertLess(time.monotonic() - started, 1.5)
            self.assert_slots_available(index_search._slots, 8)

    def test_chat_ordinary_and_wait_header_drips_release_their_owned_slots(self):
        endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        for fast_health, operation in (
            (fast, operation) for fast in (False, True)
            for operation in ("members", "wait", "scan")
        ):
            self.server.fast_health = fast_health
            with self.subTest(operation=operation, fast_health=fast_health), patch.dict(os.environ, {
                "HGF_CHAT_ENDPOINT": endpoint, "HGF_CHAT_INSTALLATION_ID": "example-chat",
            }), patch.object(chat_proxy, "REQUEST_OVERHEAD", 0.2):
                started = time.monotonic()
                with self.assertRaises(chat_proxy.ChatProxyError) as error:
                    chat_proxy.forward({"op": operation, "timeout": 0})
                self.assertEqual(
                    (error.exception.status, error.exception.code),
                    (504, "chat_backend_timeout"),
                )
                self.assertLess(time.monotonic() - started, 1.5)
                self.assert_slots_available(chat_proxy._ordinary, 8)
                self.assert_slots_available(chat_proxy._waiters, 16)


if __name__ == "__main__":
    unittest.main()
