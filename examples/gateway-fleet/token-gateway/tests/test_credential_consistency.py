import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway_server as gateway
import consumer_ingest
import coordinator_daemon as coordinator
from credential_record import generation_for_expiry, load_credential, store_credential
from agent_vault.cache import PersistentCache


class CredentialConsistencyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = PersistentCache(Path(directory.name))
        environment = patch.dict(os.environ, {"AGENT_VAULT_CACHE": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        self.expiry = time.time() + 7200

    def test_failed_single_record_update_preserves_token_and_expiry(self):
        self.assertTrue(store_credential(self.cache, "entry", "token-a", self.expiry))
        with patch.object(self.cache, "_write_store_unlocked", return_value=False):
            self.assertFalse(store_credential(self.cache, "entry", "token-b", self.expiry + 600))
        self.assertEqual(load_credential(self.cache, "entry"), {
            "token": "token-a", "expires_epoch": self.expiry,
        })

    def test_stale_write_is_not_acknowledged_as_current(self):
        self.assertTrue(store_credential(self.cache, "entry", "token-b", self.expiry + 600))
        self.assertFalse(store_credential(self.cache, "entry", "token-a", self.expiry))
        self.assertEqual(load_credential(self.cache, "entry")["token"], "token-b")

    def test_failed_consumer_update_preserves_the_complete_old_record(self):
        entry = "mesh/gateway-token"
        self.assertTrue(store_credential(self.cache, entry, "token-a", self.expiry))
        payload = json.dumps({"token": "token-b", "expires_epoch": self.expiry + 600})
        with patch.object(self.cache, "_write_store_unlocked", return_value=False), patch.object(
            consumer_ingest, "PersistentCache", return_value=self.cache,
        ), patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(consumer_ingest.main(), 1)
        self.assertIs(json.loads(output.getvalue())["ok"], False)
        self.assertEqual(load_credential(self.cache, entry), {
            "token": "token-a", "expires_epoch": self.expiry,
        })

    def test_gateway_read_uses_one_atomic_snapshot_during_a_new_push(self):
        entry = gateway._cache_entry("https://resource.example.invalid", "")
        self.assertTrue(store_credential(self.cache, entry, "token-a", self.expiry))
        original_get = self.cache.get
        calls = []

        def interleaved_get(*args):
            snapshot = original_get(*args)
            calls.append(args)
            with patch.object(self.cache, "get", original_get):
                store_credential(self.cache, entry, "token-b", self.expiry + 600)
            return snapshot

        with patch.object(gateway, "_relay_cache", self.cache), patch.object(
            self.cache, "get", side_effect=interleaved_get,
        ):
            status, record = gateway._relay_get_azure_token({
                "resource": "https://resource.example.invalid",
            })
        self.assertEqual(len(calls), 1)
        self.assertEqual(status, 200)
        self.assertEqual((record["token"], record["expires_on"]), ("token-a", self.expiry))

    def test_restarted_coordinator_and_consumer_persisted_ordering(self):
        entry = "mesh/gateway-token"
        old_expiry = self.expiry - 600
        self.assertTrue(store_credential(self.cache, entry, "old-token", old_expiry))
        # Simulate the former coordinator's reset counter with the cache intact.
        with patch.dict(coordinator._state, {
            "token": None, "expires_epoch": None, "received_at": None, "generation": 0,
        }, clear=True):
            accepted = coordinator._ingest("new-token", self.expiry)
        self.assertGreater(accepted["generation"], generation_for_expiry(old_expiry))
        incoming = json.dumps({
            "token": "new-token", "expires_epoch": self.expiry,
            "generation": accepted["generation"],
        })
        with patch.object(consumer_ingest, "PersistentCache", return_value=self.cache), patch(
            "sys.stdin", io.StringIO(incoming),
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(consumer_ingest.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["generation"], accepted["generation"])
        self.assertEqual(load_credential(self.cache, entry)["token"], "new-token")

    def test_failed_delivery_is_retried_without_new_token_or_mint(self):
        consumer = {"ssh_target": "example-relay", "remote_script": "/example/consumer.py"}
        attempts = []

        def deliver(_argv, **kwargs):
            attempts.append(json.loads(kwargs["input"]))
            if len(attempts) == 1:
                return SimpleNamespace(returncode=1, stdout="", stderr="offline")
            payload = attempts[-1]
            self.assertTrue(store_credential(
                self.cache, "mesh/gateway-token", payload["token"], payload["expires_epoch"],
            ))
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "ok": True, "generation": payload["generation"],
            }), stderr="")

        with patch.dict(coordinator._state, {
            "token": None, "expires_epoch": None, "received_at": None, "generation": 0,
        }, clear=True), patch.dict(coordinator._delivered, {}, clear=True), patch.object(
            coordinator, "_load_config", return_value={"consumers": [consumer]},
        ), patch.object(coordinator.subprocess, "run", side_effect=deliver):
            coordinator._delivery_stop.clear()
            accepted = coordinator._ingest("new-token", self.expiry)
            coordinator._retry_delivery()
            self.assertTrue(coordinator._status()["has_token"])
            coordinator._retry_delivery()
            coordinator._retry_delivery()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(attempts[1]["generation"], accepted["generation"])
        self.assertEqual(load_credential(self.cache, "mesh/gateway-token")["token"], "new-token")

    def test_new_generation_during_delivery_does_not_receive_old_ack(self):
        consumer = {"ssh_target": "example-relay", "remote_script": "/example/consumer.py"}
        with patch.dict(coordinator._state, {}, clear=True), patch.dict(
            coordinator._delivered, {}, clear=True,
        ), patch.object(coordinator, "_load_config", return_value={"consumers": [consumer]}):
            coordinator._state.update(token=None, expires_epoch=None, received_at=None, generation=0)
            coordinator._delivery_stop.clear()
            old = coordinator._ingest("token-a", self.expiry)

            def changed_generation(*_args, **_kwargs):
                coordinator._ingest("token-b", self.expiry + 600)
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "ok": True, "generation": old["generation"],
                }), stderr="")

            with patch.object(coordinator.subprocess, "run", side_effect=changed_generation):
                coordinator._retry_delivery()
            self.assertFalse(coordinator._delivered)

    def test_owned_delivery_loop_retries_and_joins_without_another_ingest(self):
        attempted = threading.Event()
        calls = []

        def retry():
            calls.append(True)
            if len(calls) == 2:
                attempted.set()
                coordinator._delivery_stop.set()

        with patch.object(coordinator, "_retry_delivery", side_effect=retry), patch.object(
            coordinator, "RETRY_SECONDS", 0.01,
        ):
            coordinator._delivery_stop.clear()
            coordinator._delivery_wakeup.clear()
            worker = threading.Thread(target=coordinator._delivery_loop)
            worker.start()
            try:
                self.assertTrue(attempted.wait(3))
            finally:
                coordinator._delivery_stop.set()
                coordinator._delivery_wakeup.set()
                worker.join(3)
                coordinator._delivery_stop.clear()
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 2)


class CredentialRequestTests(unittest.TestCase):
    def setUp(self):
        auth = patch.object(gateway, "validate_bearer", return_value={"oid": "example"})
        auth.start()
        self.addCleanup(auth.stop)
        self.server = gateway.ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("POST", path, body=json.dumps(payload),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_non_object_requests_return_json400_before_cache_access(self):
        with patch.object(gateway, "_push_credential") as write, patch.object(
            gateway, "_relay_get_azure_token",
        ) as read:
            for path in ("/api/v1/credentials/push", "/api/v1/relay"):
                for body in ([], True, "text", 7, None):
                    with self.subTest(path=path, body=body):
                        status, response = self.post(path, body)
                        self.assertEqual(status, 400)
                        self.assertIn("error", response)
            write.assert_not_called()
            read.assert_not_called()

    def test_valid_push_and_relay_use_the_actual_atomic_cache_contract(self):
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {
            "AGENT_VAULT_CACHE": "1",
        }), patch.object(gateway, "_relay_cache", PersistentCache(name)):
            expiry = time.time() + 7200
            payload = {"resource": "https://resource.example.invalid", "token": "sample-token",
                       "expires_epoch": expiry}
            self.assertEqual(self.post("/api/v1/credentials/push", payload)[0], 200)
            status, record = self.post("/api/v1/relay", {
                "action": "get-azure-token", "fields": {"resource": payload["resource"]},
            })
            self.assertEqual(status, 200)
            self.assertEqual((record["token"], record["expires_on"]), ("sample-token", expiry))

    def test_malformed_object_fields_return_json400_before_cache_access(self):
        valid = {"resource": "https://resource.example.invalid", "token": "sample",
                 "expires_epoch": time.time() + 7200}
        invalid = [
            {**valid, "expires_epoch": value}
            for value in ("invalid", None, True, [], {}, float("nan"), float("inf"), 1e308)
        ]
        invalid += [{**valid, key: value} for key in ("resource", "token", "tenant")
                    for value in ([], {}, 1, None)]
        with patch.object(gateway, "_push_credential") as write:
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(self.post("/api/v1/credentials/push", payload)[0], 400)
            write.assert_not_called()
        with patch.object(gateway, "_relay_get_azure_token") as read:
            invalid = [{"action": "get-azure-token", "fields": value}
                       for value in (1, [], None, "text", True)]
            invalid += [{"action": "get-azure-token", "fields": {"resource": value}}
                        for value in (1, [], {}, None)]
            invalid.append({"action": [], "fields": {}})
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(self.post("/api/v1/relay", payload)[0], 400)
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
