import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, call, patch
import uuid
import zipfile

SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_mesh", SERVICE / "windows.py")
mesh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mesh
spec.loader.exec_module(mesh)
KEY = "ssh-ed25519 " + base64.b64encode(
    struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"a" * 32).decode()
WHO = {"sid": "S-1-5-21-123-456-789-1001", "user": "operator",
       "domain": "machine", "session": 2, "admin": False}


class WindowsTests(unittest.TestCase):
    def setUp(self):

        self.root = SERVICE / "tests" / ("_windows-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        for name in ("config", "state", "logs", "bin", "versions"):
            (self.root / name).mkdir()
        self.config = mesh.read_json(SERVICE / "windows.yaml")
        self.config.pop("gateway_http", None)
        self.config = mesh.validate_config(self.config)
        mesh.write_json(self.root / "config" / "windows.yaml", self.config)
        self.enrollment = {
            "schema": 1, "backend": "openssh-reverse", "machine": "operator-node-e",
            "gateway_user": "operator", "host_key": KEY, "relay_port": 23005,
            "server_port": 22, "peers": [
                {"machine": "operator-" + name, "relay_port": port}
                for name, port in mesh.PEERS.items()]}

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_http_opt_in_is_boolean_and_legacy_routes_remain_unchanged(self):
        enrollment = mesh.validate_enrollment(self.enrollment, self.config)
        legacy = mesh.relay_command(Path("ssh.exe"), self.root, enrollment)
        disabled = mesh.validate_config(dict(self.config, gateway_http=False))
        enabled = mesh.validate_config(dict(self.config, gateway_http=True))
        self.assertEqual(mesh.relay_command(Path("ssh.exe"), self.root, enrollment, disabled),
                         legacy)
        command = mesh.relay_command(Path("ssh.exe"), self.root, enrollment, enabled)
        self.assertEqual(command[:-3], legacy[:-1])
        self.assertEqual(command[-3:], [
            "-L", "127.0.0.1:18787:127.0.0.1:8787", legacy[-1]])
        self.assertEqual(mesh.local_forward_ports(enabled), list(range(24001, 24006)) + [18787])
        self.assertEqual(mesh.local_forward_ports(), list(range(24001, 24006)))
        for bad in (None, 0, 1, "true", {"host": "other", "port": 22}):
            with self.subTest(value=bad), self.assertRaises(mesh.MeshError):
                mesh.validate_config(dict(self.config, gateway_http=bad))
        self.assertIs(type(mesh.validate_config(
            mesh.read_json(SERVICE / "windows.yaml"))["gateway_http"]), bool)

    def test_http_probe_uses_exact_data_free_contract_and_never_follows_redirects(self):
        requests = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(inner):
                request = bytearray()
                while b"\r\n\r\n" not in request and len(request) < 512:
                    request.extend(inner.request.recv(512))
                requests.append(bytes(request))
                try:
                    inner.request.sendall(inner.server.response)
                except OSError:
                    pass

        body = b'{"status":"ok","time":123.5}'
        prefix = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
        valid = prefix + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        cases = [
            (valid, True),
            (valid.replace(b"200 OK", b"302 Found"), False),
            (valid.replace(b"application/json", b"text/plain"), False),
            (valid.replace(b"Content-Length:", b"Content-Encoding: gzip\r\nContent-Length:"), False),
            (valid.replace(b"Content-Length:", b"Transfer-Encoding: chunked\r\nContent-Length:"), False),
            (valid.replace(b"Content-Length:", b"Content-Length: 1\r\nContent-Length:"), False),
            (valid[:-1], False),
            (prefix + b"Content-Length: 1\r\n\r\n" + body, False),
            (prefix + b"Content-Length: 257\r\n\r\n" + b" " * 257, False),
            (b"x" * 4097, False),
        ]
        for value in ({"status": "other", "time": 123}, {"status": "ok", "time": True},
                      {"status": "ok", "time": float("inf")}, {"status": "ok"}):
            raw = json.dumps(value).encode()
            cases.append((prefix + b"Content-Length: " + str(len(raw)).encode()
                          + b"\r\n\r\n" + raw, False))
        server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            for response, expected in cases:
                with self.subTest(response=response[:60]):
                    server.response = response
                    self.assertIs(mesh.gateway_http_health(server.server_address[1]), expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(requests), len(cases))
        self.assertTrue(all(request == (
            b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            for request in requests))

    def test_http_probe_absolute_deadline_bounds_slow_drip_headers(self):
        class Handler(socketserver.BaseRequestHandler):
            def handle(inner):
                inner.request.recv(512)
                try:
                    for char in b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n":
                        inner.request.sendall(bytes([char]))
                        time.sleep(0.1)
                except OSError:
                    pass

        server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            start = time.monotonic()
            self.assertFalse(mesh.gateway_http_health(server.server_address[1]))
            self.assertLess(time.monotonic() - start, 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_http_probe_cache_requires_current_listener_ownership(self):
        child = Mock(process=object())
        probe = Mock(return_value=True)
        checks = mesh.BannerProbes(probe=probe)
        child.owns_port.return_value = False
        self.assertFalse(checks.ready(child, 18787))
        probe.assert_not_called()
        child.owns_port.return_value = True
        self.assertTrue(checks.ready(child, 18787))
        self.assertTrue(checks.ready(child, 18787))
        self.assertEqual(probe.call_count, 1)
        child.owns_port.return_value = False
        self.assertFalse(checks.ready(child, 18787))
        child.owns_port.return_value = True
        self.assertTrue(checks.ready(child, 18787))
        self.assertEqual(probe.call_count, 2)
        child.process = object()
        self.assertTrue(checks.ready(child, 18787))
        self.assertEqual(probe.call_count, 3)

    def test_http_foreign_port_refuses_before_any_relay_start(self):
        config = dict(self.config, gateway_http=True)
        relay = Mock(process=None, retry_at=0)
        tunnel = Mock()
        tunnel.owns_port.return_value = True
        with patch.object(mesh, "port_open", side_effect=lambda port: port == 18787):
            with self.assertRaisesRegex(mesh.MeshError, "unrelated listener"):
                mesh.reconcile_relay(relay, tunnel, self.root, self.root,
                                     self.enrollment, 1, 0, config)
        relay.start.assert_not_called()
        relay.cleanup.assert_not_called()

    def test_http_foreign_port_refuses_before_cold_daemon_launch(self):
        mesh.write_json(self.root / "config" / "windows.yaml",
                        dict(self.config, gateway_http=True))
        with patch.object(mesh, "control", return_value=mesh.status_unavailable()), \
                patch.object(mesh, "active_slot", return_value=self.root / "versions" / ("a" * 64)), \
                patch.object(mesh, "port_open", side_effect=lambda port: port == 18787), \
                patch.object(mesh, "spawn_daemon") as spawn:
            with self.assertRaisesRegex(mesh.MeshError, "port occupied"):
                mesh.ensure(self.root)
        spawn.assert_not_called()

    def test_http_receipt_requires_owned_health_and_never_claims_authentication(self):
        mesh.write_json(self.root / "config" / "windows.yaml",
                        dict(self.config, gateway_http=True))
        slot = self.root / "versions" / ("a" * 64)
        for health in ({"ready": True}, {"gateway_http": {"ready": True, "owned": False}}):
            with self.subTest(health=health), self.assertRaisesRegex(mesh.MeshError, "owned health"):
                mesh.deployment_receipt(self.root, slot, None, WHO, health)
        self.assertFalse((self.root / "state" / "deploy-manifest.json").exists())
        state = {"ready": True, "owned": True, "authenticated": "not-probed"}
        mesh.deployment_receipt(self.root, slot, None, WHO, {"gateway_http": state})
        receipt = mesh.read_json(self.root / "state" / "deploy-manifest.json")
        self.assertEqual(receipt["gateway_http"], state)
        self.assertFalse(receipt["authenticated_ssh_verified"])

    def test_http_candidate_failure_restores_prior_config_selection_and_receipt(self):
        old, new = "a" * 64, "b" * 64
        mesh.atomic(self.root / "active", old)
        receipt = self.root / "state" / "deploy-manifest.json"
        receipt.write_bytes(b"preserve-prior-receipt")
        desired = self.root / "desired.yaml"
        mesh.write_json(desired, dict(self.config, gateway_http=True))
        candidate = {"running": True, "ready": False, "bootstrap_ready": True,
                     "gateway_http": {"ready": False, "owned": True}}
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value={"running": True, "ready": True}), \
                patch.object(mesh, "build_slot", return_value=self.root / "versions" / new), \
                patch.object(mesh, "write_launcher"), patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "verify_durable_launch"), \
                patch.object(mesh, "stop") as stop, \
                patch.object(mesh, "load_enrollment", return_value=self.enrollment), \
                patch.object(mesh, "ensure", side_effect=[candidate, {"ready": True}]), \
                patch.object(mesh, "wait_mesh", side_effect=[
                    mesh.MeshError("HTTP health unavailable"), {"ready": True}]) as wait, \
                patch.object(mesh, "register_autostart") as registration:
            with self.assertRaisesRegex(mesh.MeshError, "predecessor restored and health-verified"):
                mesh.deploy(self.root, desired, accept_config=True)
        self.assertEqual(mesh.read_json(self.root / "config" / "windows.yaml"), self.config)
        self.assertEqual((self.root / "active").read_text(), old + "\n")
        self.assertEqual(receipt.read_bytes(), b"preserve-prior-receipt")
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(wait.call_count, 2)
        registration.assert_not_called()

    def test_first_enrolled_http_install_waits_for_the_actual_enabled_route(self):
        desired = self.root / "desired.yaml"
        config = dict(self.config, gateway_http=True)
        mesh.write_json(desired, config)
        initial = {"running": True, "ready": False, "bootstrap_ready": True}
        ready = {"running": True, "ready": True,
                 "gateway_http": {"configured": True, "ready": True, "owned": True,
                                  "authenticated": "not-probed"}}
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value=mesh.status_unavailable()), \
                patch.object(mesh, "build_slot",
                             return_value=self.root / "versions" / ("b" * 64)), \
                patch.object(mesh, "write_launcher"), patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "verify_durable_launch"), \
                patch.object(mesh, "load_enrollment", return_value=self.enrollment), \
                patch.object(mesh, "ensure", return_value=initial), \
                patch.object(mesh, "wait_mesh", return_value=ready) as wait, \
                patch.object(mesh, "register_autostart"):
            result = mesh.deploy(self.root, desired, accept_config=True)
        self.assertTrue(result["gateway_http"]["ready"])
        wait.assert_called_once_with(self.root)
        receipt = mesh.read_json(self.root / "state" / "deploy-manifest.json")
        self.assertEqual(receipt["gateway_http"]["authenticated"], "not-probed")

    def test_banner_waits_for_delayed_gateway_handshake(self):
        failures = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(10)

            def delayed_banner():
                try:
                    with listener.accept()[0] as connection:
                        threading.Event().wait(1.2)
                        connection.sendall(b"SSH-2.0-delayed-gateway\r\n")
                except OSError as error:
                    failures.append(error)

            server = threading.Thread(target=delayed_banner)
            server.start()
            try:
                self.assertTrue(mesh.banner(listener.getsockname()[1]))
            finally:
                server.join(timeout=10)
            self.assertFalse(server.is_alive())
            self.assertEqual(failures, [])

    def test_banner_timeout_remains_bounded_and_non_ssh_is_not_ready(self):
        connection = Mock()
        connection.recv.side_effect = socket.timeout()
        with patch.object(mesh.socket, "create_connection") as connect:
            connect.return_value.__enter__.return_value = connection
            self.assertFalse(mesh.banner(18444))
            connection.settimeout.assert_called_once_with(15)
            connection.recv.side_effect = None
            connection.recv.return_value = b"not an SSH server"
            self.assertFalse(mesh.banner(18444))

    def test_azure_msi_uses_selected_native_entrypoint_without_shell(self):
        package = self.root / "Program Files (x86)" / "Azure CLI"
        shim = package / "wbin" / "az.CMD"
        shim.parent.mkdir(parents=True)
        shim.write_text('"%~dp0\\..\\python.exe" -IBm azure.cli %*\n', encoding="utf-8")
        (package / "python.exe").write_bytes(b"fixture")
        arguments = ["account", "show", "--output", "none"]
        with patch.object(mesh.shutil, "which", return_value=str(shim)):
            self.assertEqual(mesh.az_command(arguments),
                             [str(package / "python.exe"), "-IBm", "azure.cli"] + arguments)
        self.assertEqual(mesh.child_environment("azure")["AZ_INSTALLER"], "MSI")

    def test_banner_probes_limit_and_deduplicate_unauthenticated_connections(self):
        child = Mock(process=object())
        child.owns_port.return_value = True
        probes = mesh.BannerProbes()
        with patch.object(mesh, "banner", return_value=True) as banner, \
                patch.object(mesh.time, "monotonic") as clock:
            for now in range(30):
                clock.return_value = now
                self.assertTrue(probes.ready(child, 2223))
                self.assertTrue(probes.ready(child, 24003))
                self.assertTrue(probes.ready(child, 24003))
            self.assertEqual(banner.call_args_list, [call(2223), call(24003)])
            clock.return_value = 30
            self.assertTrue(probes.ready(child, 24003))
            self.assertEqual(banner.call_args_list, [call(2223), call(24003), call(24003)])
        self.assertEqual(child.owns_port.call_count, 91)

    def test_failed_banner_is_also_rate_limited_and_retried(self):
        child = Mock(process=object())
        child.owns_port.return_value = True
        probes = mesh.BannerProbes()
        with patch.object(mesh, "banner", side_effect=[False, True]) as banner, \
                patch.object(mesh.time, "monotonic") as clock:
            clock.return_value = 0
            self.assertFalse(probes.ready(child, 24003))
            clock.return_value = 29
            self.assertFalse(probes.ready(child, 24003))
            self.assertEqual(banner.call_count, 1)
            clock.return_value = 30
            self.assertTrue(probes.ready(child, 24003))
            self.assertEqual(banner.call_count, 2)

    def test_banner_cache_never_survives_lost_ownership_or_a_new_child(self):
        child = Mock(process=object())
        child.owns_port.return_value = True
        probes = mesh.BannerProbes()
        with patch.object(mesh, "banner", return_value=True) as banner, \
                patch.object(mesh.time, "monotonic", return_value=0):
            self.assertTrue(probes.ready(child, 24003))
            child.owns_port.return_value = False
            self.assertFalse(probes.ready(child, 24003))
            self.assertNotIn(24003, probes.results)
            self.assertEqual(banner.call_count, 1)
            child.owns_port.return_value = True
            self.assertTrue(probes.ready(child, 24003))
            child.process = object()
            self.assertTrue(probes.ready(child, 24003))
            self.assertEqual(banner.call_count, 3)

    def test_five_participants_stay_below_default_noauth_penalty_threshold(self):
        participants = [(mesh.BannerProbes(), Mock(process=object()))
                        for _ in mesh.PEERS]
        for _, child in participants:
            child.owns_port.return_value = True
        with patch.object(mesh, "banner", return_value=True) as banner, \
                patch.object(mesh.time, "monotonic") as clock:
            for now in range(30):
                clock.return_value = now
                for index, (probes, child) in enumerate(participants):
                    probes.ready(child, 2223)
                    probes.ready(child, 18444)
                    probes.ready(child, 24001 + index)
                    for port in range(24001, 24006):
                        probes.ready(child, port)


            counts = {port: banner.call_args_list.count(call(port))
                      for port in (2223, 18444, *range(24001, 24006))}
            self.assertEqual(counts, {2223: 5, 18444: 5,
                                     **{port: 5 for port in range(24001, 24006)}})
            self.assertEqual(mesh.BANNER_PROBE_INTERVAL, 30)

    def test_banner_probe_budget_with_real_loopback_connections(self):
        connections = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                connections.append(self.client_address)
                self.request.sendall(b"SSH-2.0-probe-fixture\r\n")

        with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                port = server.server_address[1]
                child = Mock(process=object())
                child.owns_port.return_value = True
                probes = mesh.BannerProbes()
                with patch.object(mesh.time, "monotonic") as clock:
                    for now in range(60):
                        clock.return_value = now
                        self.assertTrue(probes.ready(child, port))
                        self.assertTrue(probes.ready(child, port))
                self.assertEqual(len(connections), 2)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_probe_interval_starts_at_each_endpoint_not_the_supervisor_tick(self):
        child = Mock(process=object())
        child.owns_port.return_value = True
        probes = mesh.BannerProbes()
        with patch.object(mesh, "banner", return_value=True) as banner, \
                patch.object(mesh.time, "monotonic") as clock:
            clock.return_value = 0
            probes.ready(child, 2223)
            clock.return_value = 25
            probes.ready(child, 24003)
            clock.return_value = 30
            probes.ready(child, 2223)
            probes.ready(child, 24003)
            self.assertEqual(banner.call_args_list, [call(2223), call(24003), call(2223)])
            clock.return_value = 55
            probes.ready(child, 24003)
            self.assertEqual(banner.call_count, 4)

    def test_azure_unknown_or_incomplete_wrapper_refuses(self):
        shim = self.root / "az.cmd"
        shim.write_text("@echo arbitrary wrapper\n", encoding="utf-8")
        with patch.object(mesh.shutil, "which", return_value=str(shim)):
            with self.assertRaises(mesh.MeshError):
                mesh.az_command(["account", "show"])

    def test_sshd_allowlist_uses_exact_domain_qualified_current_user(self):
        self.assertEqual(mesh.sshd_principal({**WHO, "domain": "REDMOND"}),
                         "redmond\\operator")
        self.assertEqual(mesh.sshd_principal(WHO), "machine\\operator")
        for domain in ("*", "machine\nAllowUsers *", ""):
            with self.assertRaises(mesh.MeshError):
                mesh.sshd_principal({**WHO, "domain": domain})

    def test_canonical_wsl_jump_permits_only_existing_loopback_2200(self):
        for name in ("host_ed25519", "client_ed25519"):
            (self.root / "config" / name).write_bytes(b"preserved-private-fixture")
            (self.root / "config" / (name + ".pub")).write_text(KEY, encoding="ascii")
        with patch.object(mesh, "require_user", return_value=WHO):
            mesh.ensure_keys(self.root, self.root)
        config = (self.root / "config" / "sshd_config").read_text()
        self.assertIn("AllowTcpForwarding local\n", config)
        self.assertIn("PermitOpen localhost:2200 127.0.0.1:2200\n", config)
        self.assertIn("AllowAgentForwarding no\n", config)
        self.assertEqual(mesh.authorized_entry(KEY, "node-e"),
                         'restrict,pty,port-forwarding,permitopen="localhost:2200",'
                         'permitopen="127.0.0.1:2200" %s gateway-node-e' % KEY)

    def test_fixed_routes_and_no_secret_in_argv(self):
        enrollment = mesh.validate_enrollment(self.enrollment, self.config)
        command = mesh.relay_command(Path("ssh.exe"), self.root, enrollment)
        self.assertNotIn("--auth", command)
        self.assertIn("-N", command)
        self.assertIn("-T", command)
        self.assertEqual(command[command.index("-R") + 1], "127.0.0.1:23005:127.0.0.1:2223")
        forwards = [command[index + 1] for index, arg in enumerate(command) if arg == "-L"]
        self.assertEqual(forwards, [
            "127.0.0.1:%d:127.0.0.1:%d" % (24001 + index, port)
            for index, port in enumerate(mesh.PEERS.values())])
        self.assertNotIn("socks", " ".join(command))
        for option in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes",
                       "HostKeyAlias=gateway-relay", "ExitOnForwardFailure=yes",
                       "ServerAliveInterval=15", "ServerAliveCountMax=3", "TCPKeepAlive=no"):
            self.assertIn(option, command)
        self.assertIn("UserKnownHostsFile=" + str(self.root / "config" / "gateway_known_hosts"),
                      command)
        self.assertEqual(command[-1], "operator@127.0.0.1")

    def test_enrollment_rejects_unknown_duplicate_wrong_ports(self):
        for mutate in (
                lambda v: v.update(extra=True),
                lambda v: v.update(relay_port=23004),
                lambda v: v.update(relay_port=True),
                lambda v: v.update(machine="node-d"),
                lambda v: v.update(server_port=2222),
                lambda v: v.update(host_key="not-a-public-key"),
                lambda v: v.update(peers=[v["peers"][0]] * 5),
                lambda v: v.update(auth="must-be-rejected"),
                lambda v: v.update(backend="chisel"),
                lambda v: v.update(schema=True)):
            value = json.loads(json.dumps(self.enrollment))
            mutate(value)
            with self.assertRaises(mesh.MeshError):
                mesh.validate_enrollment(value, self.config)

    def test_malformed_config_is_not_defaulted(self):
        with self.assertRaises(mesh.MeshError):
            mesh.json_object('{"machine":"node-e","machine":"node-c"}')
        for value in ({}, dict(self.config, unknown=1),
                      dict(self.config, autostart="true"),
                      dict(self.config, backend="chisel"),
                      dict(self.config, bastion="name & command"),
                      dict(self.config, resource="/other/resource")):
            with self.assertRaises(mesh.MeshError):
                mesh.validate_config(value)

    def test_digest_before_extraction_and_archive_traversal(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escape", "never")
        payload = buffer.getvalue()
        destination = self.root / "archive"
        destination.mkdir()
        with self.assertRaisesRegex(mesh.MeshError, "digest"):
            mesh.extract_zip(payload, "0" * 64, destination)
        self.assertEqual(list(destination.iterdir()), [])
        with self.assertRaisesRegex(mesh.MeshError, "Unsafe"):
            mesh.extract_zip(payload, mesh.hashlib.sha256(payload).hexdigest(), destination)
        self.assertFalse((self.root / "escape").exists())

    def test_all_pins_are_exact_sha256_and_official_urls(self):
        for pins in mesh.PINS.values():
            for kind, (_, digest) in pins.items():
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                self.assertTrue(mesh.URLS[kind].startswith("https://github.com/"))

    def test_public_import_pins_gateway_and_carries_no_relay_secret(self):
        mesh.import_client(self.root, self.enrollment)
        stored = mesh.read_json(self.root / "config" / "enrollment.json")
        self.assertNotIn("auth", stored)
        self.assertNotIn("sealed_auth", stored)
        self.assertFalse(hasattr(mesh, "dpapi"))
        self.assertEqual((self.root / "config" / "gateway_known_hosts").read_text(),
                         "gateway-relay " + KEY + "\n")
        self.assertEqual(mesh.load_enrollment(self.root)["machine"], "node-e")
        (self.root / "config" / "gateway_known_hosts").write_text("incorrect")
        with self.assertRaisesRegex(mesh.MeshError, "host-key pin"):
            mesh.load_enrollment(self.root)
        with patch.dict(os.environ, {"AUTH": "ambient-secret", "SSH_TEST_ENVIRONMENT": "bad",
                                     "AZURE_AUTO_UPGRADE_ENABLE": "yes",
                                     "AZURE_AUTO_UPGRADE_PROMPT": "yes"}):
            env = mesh.child_environment("relay")
            self.assertNotIn("AUTH", env)
            self.assertEqual(os.environ["AUTH"], "ambient-secret")
            self.assertNotIn("SSH_TEST_ENVIRONMENT", env)
            azure = mesh.child_environment("azure")
            self.assertNotIn("AUTH", azure)
            self.assertEqual(azure["AZURE_EXTENSION_USE_DYNAMIC_INSTALL"], "no")
            self.assertEqual(azure["AZURE_AUTO_UPGRADE_ENABLE"], "no")
            self.assertEqual(azure["AZURE_AUTO_UPGRADE_PROMPT"], "no")
            self.assertEqual(os.environ["AZURE_AUTO_UPGRADE_ENABLE"], "yes")
            self.assertEqual(os.environ["AZURE_AUTO_UPGRADE_PROMPT"], "yes")
            self.assertEqual(mesh.child_environment("ssh")["SSH_TEST_ENVIRONMENT"], "1")

    def test_elevation_system_and_session_zero_refused(self):
        self.assertEqual(mesh.require_user(WHO), WHO)
        for who in (dict(WHO, admin=True), dict(WHO, session=0),
                    dict(WHO, sid="S-1-5-18")):
            with self.assertRaises(mesh.MeshError):
                mesh.require_user(who)

    def test_private_keys_preserved_across_candidate_validation(self):
        for name in ("host_ed25519", "client_ed25519"):
            (self.root / "config" / name).write_bytes(b"existing-private-key")
            (self.root / "config" / (name + ".pub")).write_text(KEY, encoding="ascii")
        with patch.object(mesh, "bounded") as bounded, \
                patch.object(mesh, "require_user", return_value=WHO):
            mesh.ensure_keys(self.root, Path("candidate"))
            mesh.ensure_keys(self.root, Path("candidate"))
        bounded.assert_not_called()
        self.assertEqual((self.root / "config" / "client_ed25519").read_bytes(),
                         b"existing-private-key")
        text = (self.root / "config" / "sshd_config").read_text()
        for required in ("Port 2223", "ListenAddress 127.0.0.1", "PasswordAuthentication no",
                         "KbdInteractiveAuthentication no", "AllowUsers machine\\operator"):
            self.assertIn(required, text)
        self.assertNotIn("dtssh", text)
        self.assertNotIn("2222", text)

    def test_authorization_preserves_legacy_fragment_and_allows_pty(self):
        fragment = self.root / "config" / "60-gateway-ssh-mesh.conf"
        fragment.write_text("legacy artifact must remain untouched\n")
        peers = [{"machine": name, "user": "operator", "public_key": KEY, "host_key": KEY}
                 for name in mesh.PEERS]
        mesh.authorize(self.root, {"peers": peers})
        self.assertEqual(fragment.read_text(), "legacy artifact must remain untouched\n")
        rendered_keys = (self.root / "config" / "authorized_keys").read_text().splitlines()
        self.assertEqual(rendered_keys, [
            mesh.authorized_entry(KEY, name) for name in mesh.PEERS])
        with self.assertRaises(mesh.MeshError):
            mesh.authorize(self.root, {"peers": [peers[0], peers[0]]})
        with self.assertRaises(mesh.MeshError):
            mesh.public_key("-----BEGIN OPENSSH PRIVATE KEY-----")

    def test_unavailable_does_not_report_success(self):
        result = mesh.control(self.root)
        self.assertFalse(result["running"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["transport"], "down")

    def test_stale_pid_record_not_contacted(self):
        mesh.write_json(self.root / "state" / "daemon.json",
                        {"pid": 123, "birthtime": 456, "port": 12345,
                         "version": "a" * 64, "sid": WHO["sid"]})
        with patch.object(mesh, "process_birth", return_value=999), \
                patch.object(mesh.socket, "create_connection") as connect:
            self.assertFalse(mesh.control(self.root)["running"])
        connect.assert_not_called()

    def test_missing_birthtime_cannot_authenticate_dead_pid(self):
        mesh.write_json(self.root / "state" / "daemon.json",
                        {"pid": 123, "birthtime": None, "port": 12345,
                         "version": "a" * 64, "sid": WHO["sid"]})
        with patch.object(mesh, "process_birth", return_value=None), \
                patch.object(mesh.socket, "create_connection") as connect:
            with self.assertRaises(mesh.MeshError):
                mesh.control(self.root)
        connect.assert_not_called()

    def test_control_refuses_unrelated_listener_before_sending_token(self):
        mesh.write_json(self.root / "state" / "daemon.json",
                        {"pid": 123, "birthtime": 456, "port": 12345,
                         "version": "a" * 64, "sid": WHO["sid"]})
        with patch.object(mesh, "process_birth", return_value=456), \
                patch.object(mesh, "listener_pid", return_value=999), \
                patch.object(mesh.socket, "create_connection") as connect:
            self.assertFalse(mesh.control(self.root)["running"])
        connect.assert_not_called()

    def test_stop_refuses_live_unresponsive_pid(self):
        mesh.write_json(self.root / "state" / "daemon.json",
                        {"pid": 123, "birthtime": 456, "port": 12345,
                         "version": "a" * 64, "sid": WHO["sid"]})
        with patch.object(mesh, "control", return_value=mesh.status_unavailable()), \
                patch.object(mesh, "process_birth", return_value=456):
            with self.assertRaisesRegex(mesh.MeshError, "unverified PID"):
                mesh.stop(self.root)

    def test_children_have_required_nonbreakaway_job_and_cleanup(self):
        process, job = Mock(), Mock()
        process.poll.return_value = None
        with patch.object(mesh, "spawn_sync_in_kill_on_close_job",
                          return_value=(process, job)) as spawn:
            child = mesh.Child("ssh")
            child.start(["sshd.exe", "-D"], self.root, {"SSH_TEST_ENVIRONMENT": "1"})
            self.assertTrue(spawn.call_args.kwargs["require_job"])
            self.assertFalse(spawn.call_args.kwargs["allow_breakaway"])
            self.assertEqual(spawn.call_args.kwargs["cwd"], str(self.root))
            child.cleanup()
        job.terminate_and_wait.assert_called_once_with(10)
        process.wait.assert_called_once()

    def test_open_port_is_not_owned_child_readiness(self):
        child = mesh.Child("ssh")
        child.process = Mock(pid=123)
        child.process.poll.return_value = None
        with patch.object(mesh, "listener_pid", return_value=999):
            self.assertFalse(child.owns_port(2223))
        with patch.object(mesh, "listener_pid", return_value=123):
            self.assertTrue(child.owns_port(2223))

    def test_ensure_uses_durable_primitive_without_contained_fallback(self):
        slot = self.root / "versions" / ("a" * 64)
        mesh.write_json(self.root / "bin" / "interpreter.json", {"python": sys.executable})
        process = Mock()
        process.poll.return_value = None
        health = {"running": True, "ready": False, "listener_ready": True,
                  "bastion_ready": True, "bootstrap_ready": True}
        with patch.object(mesh, "control", side_effect=[mesh.status_unavailable(), health]), \
                patch.object(mesh, "active_slot", return_value=slot), \
                patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "spawn_daemon", return_value=process) as spawn:
            self.assertEqual(mesh.ensure(self.root), health)
        args = spawn.call_args.kwargs
        self.assertTrue(args["console_children"])
        self.assertTrue(args["breakaway"])
        self.assertNotIn("contained_fallback", args)
        self.assertEqual(args["cwd"], self.root)

    def test_startup_wait_covers_a_slow_successful_azure_prerequisite(self):
        slot = self.root / "versions" / ("a" * 64)
        mesh.write_json(self.root / "bin" / "interpreter.json", {"python": sys.executable})
        health = {"running": True, "ready": False, "bootstrap_ready": False}
        ready = dict(health, ready=True, bootstrap_ready=True)
        process = Mock()
        process.poll.return_value = None
        with patch.object(mesh, "control",
                          side_effect=[mesh.status_unavailable(), health, ready]), \
                patch.object(mesh, "active_slot", return_value=slot), \
                patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "spawn_daemon", return_value=process), \
                patch.object(mesh.time, "monotonic", side_effect=[0, 40, 75]), \
                patch.object(mesh.time, "sleep"), \
                patch.object(mesh, "stop") as stop:
            self.assertEqual(mesh.ensure(self.root), ready)
        stop.assert_not_called()
        self.assertLessEqual(75, mesh.STARTUP_TIMEOUT)

    def test_startup_wait_still_stops_an_unready_candidate_at_its_bound(self):
        mesh.write_json(self.root / "bin" / "interpreter.json", {"python": sys.executable})
        process = Mock()
        process.poll.return_value = None
        health = {"running": True, "ready": False, "bootstrap_ready": False}
        with patch.object(mesh, "control",
                          side_effect=[mesh.status_unavailable(), health]), \
                patch.object(mesh, "active_slot", return_value=self.root / "versions" / ("a" * 64)), \
                patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "spawn_daemon", return_value=process), \
                patch.object(mesh.time, "monotonic", side_effect=[0, 40, 121]), \
                patch.object(mesh.time, "sleep"), \
                patch.object(mesh, "stop") as stop:
            with self.assertRaisesRegex(mesh.MeshError, "did not become ready"):
                mesh.ensure(self.root)
        stop.assert_called_once_with(self.root)

    def test_breakaway_rejection_is_explicit_not_retried(self):
        mesh.write_json(self.root / "bin" / "interpreter.json", {"python": sys.executable})
        with patch.object(mesh, "control", return_value=mesh.status_unavailable()), \
                patch.object(mesh, "active_slot", return_value=self.root / "versions" / ("a" * 64)), \
                patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "spawn_daemon", side_effect=OSError(5, "denied")) as spawn:
            with self.assertRaisesRegex(mesh.MeshError, "breakaway"):
                mesh.ensure(self.root)
        self.assertEqual(spawn.call_count, 1)

    def test_durable_launch_preflight_uses_canonical_flags_and_waits_for_exit(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(mesh, "spawn_daemon", return_value=process) as spawn:
            mesh.verify_durable_launch(self.root)
        self.assertEqual(spawn.call_args.args[0], [sys.executable, "-c", "pass"])
        self.assertTrue(spawn.call_args.kwargs["breakaway"])
        self.assertTrue(spawn.call_args.kwargs["console_children"])
        self.assertNotIn("contained_fallback", spawn.call_args.kwargs)
        process.wait.assert_called_once_with(timeout=10)
        process.kill.assert_not_called()

    def test_durable_launch_preflight_timeout_reaps_its_own_probe(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("probe", 10), 1]
        process.poll.return_value = None
        with patch.object(mesh, "spawn_daemon", return_value=process), \
                self.assertRaisesRegex(mesh.MeshError, "preflight timed out"):
            mesh.verify_durable_launch(self.root)
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_args_list[-1].kwargs, {"timeout": 5})

    def test_durable_launch_preflight_nonzero_refuses(self):
        process = Mock()
        process.wait.return_value = 1
        process.poll.return_value = 1
        with patch.object(mesh, "spawn_daemon", return_value=process), \
                self.assertRaisesRegex(mesh.MeshError, "preflight failed"):
            mesh.verify_durable_launch(self.root)
        process.kill.assert_not_called()

    def test_breakaway_denial_preserves_healthy_predecessor_before_cutover(self):
        old, new = "a" * 64, "b" * 64
        mesh.atomic(self.root / "active", old)
        previous = (self.root / "config" / "windows.yaml").read_bytes()
        desired = self.root / "desired.yaml"
        mesh.write_json(desired, dict(self.config, gateway_http=True))
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value={"running": True, "ready": True}), \
                patch.object(mesh, "build_slot", return_value=self.root / "versions" / new), \
                patch.object(mesh, "write_launcher"), patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "spawn_daemon", side_effect=OSError(5, "denied")), \
                patch.object(mesh, "stop") as stop, \
                patch.object(mesh, "ensure") as ensure, \
                patch.object(mesh, "register_autostart") as registration:
            with self.assertRaisesRegex(mesh.MeshError, "preflight refused; predecessor not stopped"):
                mesh.deploy(self.root, desired, accept_config=True)
        stop.assert_not_called()
        ensure.assert_not_called()
        registration.assert_not_called()
        self.assertEqual((self.root / "active").read_text(), old)
        self.assertEqual((self.root / "config" / "windows.yaml").read_bytes(), previous)
        self.assertFalse((self.root / "state" / "deploy-manifest.json").exists())

    def test_candidate_failure_rolls_back_and_verifies_old_health(self):
        old, new = "a" * 64, "b" * 64
        mesh.atomic(self.root / "active", old)
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), \
                patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value={"running": True, "ready": True}), \
                patch.object(mesh, "build_slot", return_value=self.root / "versions" / new), \
                patch.object(mesh, "write_launcher"), \
                patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "verify_durable_launch"), \
                patch.object(mesh, "stop") as stop, \
                patch.object(mesh, "ensure", side_effect=[mesh.MeshError("failed"), {"ready": True}]) as ensure, \
                patch.object(mesh, "wait_mesh", return_value={"ready": True}) as verify:
            with self.assertRaisesRegex(mesh.MeshError, "predecessor restored"):
                mesh.deploy(self.root, self.root / "config" / "windows.yaml")
        self.assertEqual((self.root / "active").read_text().strip(), old)
        self.assertEqual(ensure.call_count, 2)
        self.assertEqual(stop.call_count, 2)
        verify.assert_called_once()
        self.assertFalse((self.root / "state" / "deploy-manifest.json").exists())

    def test_healthy_reinstall_does_not_stop_or_rotate_keys(self):
        version = "a" * 64
        mesh.atomic(self.root / "active", version)
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), \
                patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value={"running": True, "ready": True}), \
                patch.object(mesh, "build_slot", return_value=self.root / "versions" / version), \
                patch.object(mesh, "write_launcher"), \
                patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "verify_durable_launch") as preflight, \
                patch.object(mesh, "register_autostart"), \
                patch.object(mesh, "stop") as stop:
            mesh.deploy(self.root, self.root / "config" / "windows.yaml")
        stop.assert_not_called()
        preflight.assert_not_called()
        receipt = mesh.read_json(self.root / "state" / "deploy-manifest.json")
        self.assertFalse(receipt["authenticated_ssh_verified"])
        self.assertEqual(receipt["sourcehash"], version)

    def test_unenrolled_local_only_install_writes_no_success_receipt(self):
        version = "a" * 64
        health = {"running": True, "ready": False, "listener_ready": True}
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), \
                patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value=mesh.status_unavailable()), \
                patch.object(mesh, "build_slot", return_value=self.root / "versions" / version), \
                patch.object(mesh, "write_launcher"), \
                patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "verify_durable_launch"), \
                patch.object(mesh, "ensure", return_value=health), \
                patch.object(mesh, "register_autostart") as registration:
            result = mesh.deploy(self.root, self.root / "config" / "windows.yaml")
        self.assertFalse(result["ready"])
        self.assertFalse(result["deployment_complete"])
        registration.assert_not_called()
        self.assertFalse((self.root / "state" / "deploy-manifest.json").exists())

    def test_explicit_config_cutover_failure_restores_previous_config(self):
        old, new = "a" * 64, "b" * 64
        mesh.atomic(self.root / "active", old)
        desired = self.root / "desired.yaml"
        mesh.write_json(desired, dict(self.config, autostart=False))
        with patch.object(mesh, "require_user", return_value=WHO), \
                patch.object(mesh, "private_directory"), \
                patch.object(mesh, "verify_runtime"), \
                patch.object(mesh, "control", return_value={"running": True, "ready": True}), \
                patch.object(mesh, "build_slot", return_value=self.root / "versions" / new), \
                patch.object(mesh, "write_launcher"), \
                patch.object(mesh, "install_ssh_module"), \
                patch.object(mesh, "verify_durable_launch"), \
                patch.object(mesh, "stop"), \
                patch.object(mesh, "ensure", side_effect=[mesh.MeshError("failed"), {"ready": True}]), \
                patch.object(mesh, "wait_mesh", return_value={"ready": True}):
            with self.assertRaisesRegex(mesh.MeshError, "predecessor restored"):
                mesh.deploy(self.root, desired, accept_config=True)
        self.assertEqual(mesh.read_json(self.root / "config" / "windows.yaml"), self.config)
        self.assertEqual((self.root / "active").read_text().strip(), old)

    def test_clean_uninstall_preserves_enrollment_keys_and_config(self):
        mesh.atomic(self.root / "active", "a" * 64)
        (self.root / "config" / "client_ed25519").write_bytes(b"private")
        mesh.write_json(self.root / "config" / "enrollment.json", {"sealed_auth": "ciphertext"})
        with patch.object(mesh, "stop"), patch.object(mesh, "register_autostart") as registration:
            mesh.uninstall(self.root)
        registration.assert_called_once_with(self.root, False)
        self.assertTrue((self.root / "config" / "windows.yaml").exists())
        self.assertTrue((self.root / "config" / "client_ed25519").exists())
        self.assertFalse((self.root / "active").exists())
        self.assertFalse((self.root / "versions").exists())

    def test_vendor_receipt_matches_exact_canonical_copy(self):
        receipt = mesh.read_json(SERVICE / "libs" / "provenance.json")
        self.assertEqual(receipt["commit"], "f79c873ee4c03022216d5d1a0ff3d8a79f9e4fc1")
        for relative, record in receipt["files"].items():
            self.assertEqual(mesh.hashlib.sha256((SERVICE / "libs" / relative).read_bytes()).hexdigest(),
                             record["sha256"])

    def test_diagnostics_retain_stage_type_errno_without_raw_messages(self):
        mesh.stage("validate-sshd-config")
        error = OSError(13, "private-token must never be printed")
        result = mesh.sanitized_error(error, "install")
        self.assertEqual(result["stage"], "validate-sshd-config")
        self.assertEqual(result["type"], "PermissionError")
        self.assertEqual(result["errno"], 13)
        self.assertNotIn("private-token", json.dumps(result))
        result = mesh.sanitized_error(KeyError("private-token"), "install")
        self.assertEqual(result["type"], "KeyError")
        self.assertNotIn("private-token", json.dumps(result))

    def test_registry_exports_only_explicit_authorized_enrolled_targets(self):
        peers = [{"machine": name, "user": "operator", "public_key": KEY, "host_key": KEY}
                 for name in ("node-e", "node-c")]
        mesh.authorize(self.root, {"peers": peers})
        (self.root / "config" / "client_ed25519").write_bytes(b"fixture")
        enrollment = mesh.validate_enrollment(self.enrollment, self.config)
        with patch.object(mesh, "load_enrollment", return_value=enrollment), \
                patch.object(mesh, "verify_private"), \
                patch.object(mesh, "require_user", return_value=WHO):
            result = mesh.ssh_registry(self.root)
        self.assertEqual(set(result), {"transport", "topology", "machines"})
        self.assertEqual(result["transport"], "gateway-relay")
        self.assertEqual(result["topology"], "per-machine")
        self.assertEqual([m["name"] for m in result["machines"]],
                         ["operator-node-c-gateway", "operator-node-e-gateway"])
        self.assertEqual([m["port"] for m in result["machines"]], [24003, 24005])
        for target in result["machines"]:
            self.assertEqual(set(target), {"name", "hostname", "user", "port",
                                          "identity_file", "options"})
            self.assertEqual(target["hostname"], "127.0.0.1")
            self.assertEqual(target["identity_file"], str(self.root / "config" / "client_ed25519"))
            self.assertEqual(target["options"], {
                "HostKeyAlias": target["name"] if target["name"].endswith("-gateway")
                else target["name"] + "-gateway",
                "UserKnownHostsFile": str(self.root / "config" / "known_hosts"),
                "StrictHostKeyChecking": "yes", "IdentitiesOnly": "yes", "BatchMode": "yes",
                "IdentityAgent": "none", "ProxyCommand": "none", "ProxyJump": "none",
                "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
                "ServerAliveInterval": 15, "ServerAliveCountMax": 3})
        self.assertNotIn("auth", json.dumps(result))
        self.assertNotIn("public_key", json.dumps(result))
        self.assertFalse((self.root / "config" / "60-gateway-ssh-mesh.conf").exists())
        self.assertEqual(mesh.read_json(self.root / "config" / "agent-ssh-registry.yaml"), result)
        receipt = mesh.read_json(self.root / "config" / "agent-ssh-registry.provenance.json")
        self.assertEqual(receipt["live"], "not-probed")
        self.assertEqual(receipt["authorization_source"],
                         str(self.root / "config" / "authorized-peers.json"))
        mesh.write_json(self.root / "config" / "canonical-routes.json",
                        {"schema": 1, "machines": ["operator-node-e"]})
        with patch.object(mesh, "load_enrollment", return_value=enrollment), \
                patch.object(mesh, "verify_private"), \
                patch.object(mesh, "require_user", return_value=WHO):
            switched = mesh.ssh_registry(self.root)
        self.assertEqual([row["name"] for row in switched["machines"]],
                         ["operator-node-c-gateway", "operator-node-e-gateway", "operator-node-e"])
        self.assertEqual(switched["machines"][-1]["options"]["HostKeyAlias"],
                         "operator-node-e-gateway")

    def test_registry_refuses_missing_host_authority_and_exports_no_unenrolled_guesses(self):
        mesh.authorize(self.root, {"peers": [
            {"machine": "node-e", "user": "operator", "public_key": KEY, "host_key": KEY}]})
        with patch.object(mesh, "load_enrollment", return_value=None):
            self.assertEqual(mesh.ssh_registry(self.root)["machines"], [])
        (self.root / "config" / "known_hosts").write_text("")
        with patch.object(mesh, "load_enrollment", return_value=None):
            with self.assertRaisesRegex(mesh.MeshError, "disagrees"):
                mesh.ssh_registry(self.root)

    def test_provider_descriptor_distinguishes_configuration_from_live_health(self):
        write = self.root / "config" / "agent-ssh-registry.yaml"
        mesh.write_json(write, {"transport": "gateway-relay", "topology": "per-machine",
                               "machines": [{"name": "operator-node-e-gateway"}]})
        result = mesh.describe_provider(self.root)
        self.assertTrue(result["configured"])
        self.assertTrue(result["registry_available"])
        self.assertFalse(result["module_available"])
        self.assertEqual(result["live"], "not-probed")
        self.assertNotIn("ready", result)
        self.assertEqual(result["profile_owner"], "agent-ssh")

    def test_declared_module_is_installed_as_owned_durable_data(self):
        slot = self.root / "versions" / "module-fixture"
        (slot / "agent-ssh").mkdir(parents=True)
        source = slot / "agent-ssh" / "module.yaml"
        source.write_bytes(b'{"module":"gateway-relay"}\n')
        self.assertIn("agent-ssh/module.yaml", mesh.source_files())
        mesh.install_ssh_module(self.root, slot)
        self.assertEqual((self.root / "config" / "agent-ssh-module.yaml").read_bytes(),
                         source.read_bytes())

    def test_real_declared_sources_produce_a_stable_hash_with_safe_stage_codes(self):
        version = mesh.source_hash(self.config)
        self.assertRegex(version, r"^[a-f0-9]{64}$")
        self.assertEqual(mesh.source_hash(self.config), version)
        self.assertEqual(mesh.ERROR_STAGE, "source-agent-ssh-module-yaml")

    def test_bootstrap_bastion_does_not_require_relay_enrollment(self):
        tunnel = Mock(process=None, retry_at=0)
        with patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "bounded", return_value=b"") as bounded, \
                patch.object(mesh.shutil, "which", return_value="existing-az.exe"):
            mesh.ensure_bastion(tunnel, self.root, self.config, 1, 0)
        self.assertEqual(bounded.call_count, 1)
        self.assertEqual(bounded.call_args.kwargs["timeout"], 60)
        args = tunnel.start.call_args.args[0]
        self.assertEqual(args[args.index("--resource-port") + 1], "22")
        self.assertEqual(args[args.index("--port") + 1], "18444")
        relay = Mock(process=None, retry_at=0)
        mesh.reconcile_relay(relay, tunnel, self.root, self.root, None, 1, 0)
        relay.start.assert_not_called()

    def test_slow_azure_account_check_remains_bounded_and_launches_only_after_success(self):
        tunnel = Mock(process=None, retry_at=0)

        def account_check(argv, root, env, *, timeout):
            self.assertEqual(timeout, 60)
            self.assertGreater(timeout, 28)
            self.assertEqual(env["AZURE_AUTO_UPGRADE_ENABLE"], "no")
            return b""

        with patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "bounded", side_effect=account_check), \
                patch.object(mesh.shutil, "which", return_value="existing-az.exe"):
            mesh.ensure_bastion(tunnel, self.root, self.config, 1, 0)
        tunnel.start.assert_called_once()
        tunnel.reset_mock()
        with patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "bounded", side_effect=mesh.MeshError("Bounded validation timed out")), \
                patch.object(mesh.shutil, "which", return_value="existing-az.exe"):
            with self.assertRaisesRegex(mesh.MeshError, "timed out"):
                mesh.ensure_bastion(tunnel, self.root, self.config, 1, 0)
        tunnel.start.assert_not_called()

    def test_relay_reconcile_requires_owned_bastion_and_preserves_job_boundary(self):
        enrollment = mesh.validate_enrollment(self.enrollment, self.config)
        tunnel = Mock()
        tunnel.owns_port.return_value = False
        relay = Mock(process=None, retry_at=0)
        mesh.reconcile_relay(relay, tunnel, self.root, self.root, enrollment, 1, 0)
        relay.start.assert_not_called()
        tunnel.owns_port.return_value = True
        with patch.object(mesh, "port_open", return_value=False), \
                patch.object(mesh, "find_binary", return_value=Path("ssh.exe")):
            mesh.reconcile_relay(relay, tunnel, self.root, self.root, enrollment, 1, 0)
        self.assertEqual(relay.start.call_args.args[0],
                         mesh.relay_command(Path("ssh.exe"), self.root, enrollment))
        self.assertNotIn("AUTH", relay.start.call_args.args[2])
        relay.process = Mock()
        tunnel.owns_port.return_value = False
        mesh.reconcile_relay(relay, tunnel, self.root, self.root, enrollment, 1, 0)
        relay.cleanup.assert_called_once()

    def test_candidate_downloads_only_pinned_openssh_and_never_executes_blocked_payload(self):
        def native_fixture(kind, arch, destination):
            self.assertEqual(kind, "ssh")
            for name in ("ssh.exe", "sshd.exe", "ssh-keygen.exe"):
                (destination / name).write_bytes(b"test fixture only")
        with patch.object(mesh, "download", side_effect=native_fixture) as download, \
                patch.object(mesh, "ensure_keys"), patch.object(mesh, "validate_slot"):
            slot = mesh.build_slot(self.root, self.config)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(set(mesh.URLS), {"ssh"})
        self.assertTrue(all(set(pins) == {"ssh"} for pins in mesh.PINS.values()))
        files = mesh.read_json(slot / "files.json")
        self.assertFalse(any("chisel" in name.lower() for name in files))
        with patch.object(mesh, "find_binary", side_effect=lambda slot, name: Path(name)), \
                patch.object(mesh, "bounded", side_effect=[b"OpenSSH_for_Windows_10.0", b""]) as run:
            mesh.validate_slot(self.root, slot)
        self.assertEqual([call.args[0][0].name for call in run.call_args_list],
                         ["ssh.exe", "sshd.exe"])
        blocked = OSError(5, "never expose third-party content")
        blocked.winerror = 225
        with patch.object(mesh, "find_binary", return_value=Path("ssh.exe")), \
                patch.object(mesh, "bounded", side_effect=blocked) as run:
            with self.assertRaises(OSError) as caught:
                mesh.validate_slot(self.root, slot)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(mesh.sanitized_error(caught.exception, "install")["winerror"], 225)
        self.assertEqual(mesh.ERROR_STAGE, "validate-openssh-version")

    def test_candidate_publication_refusal_preserves_active_selection_and_keys(self):
        old = "a" * 64
        mesh.atomic(self.root / "active", old + "\n")
        key = self.root / "config" / "client_ed25519"
        key.write_bytes(b"preserved-fixture-only-key")
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        original_replace = mesh.os.replace

        def refuse_candidate(source, target):
            if Path(source).parent == self.root / "versions" and ".candidate-" in Path(source).name:
                raise PermissionError("fixture candidate publication refused")
            return original_replace(source, target)

        def archive_fixture(kind, arch, destination):
            for name in ("ssh.exe", "sshd.exe", "ssh-keygen.exe"):
                (destination / name).write_bytes(b"test fixture only")

        with patch.object(mesh, "download", side_effect=archive_fixture), \
                patch.object(mesh, "ensure_keys"), patch.object(mesh, "validate_slot"), \
                patch.object(mesh.os, "replace", side_effect=refuse_candidate):
            with self.assertRaisesRegex(PermissionError, "publication refused"):
                mesh.build_slot(self.root, self.config)
        self.assertEqual(list((self.root / "versions").iterdir()), [])
        self.assertEqual({str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()},
                         before)

    def test_explicit_generated_key_repair_does_not_skip_runtime_audit(self):
        with patch.object(mesh, "verify_private"), \
                patch.object(mesh, "normalize_generated_key") as normalize, \
                patch.object(mesh, "verify_runtime") as verify:
            for name in mesh.GENERATED_KEY_FILES:
                (self.root / "config" / name).write_bytes(b"unchanged")
            mesh.repair_generated_key_acls(self.root, WHO["sid"])
        self.assertEqual(normalize.call_count, 4)
        self.assertEqual({call.args[0].name for call in normalize.call_args_list},
                         mesh.GENERATED_KEY_FILES)
        verify.assert_called_once_with(self.root, WHO["sid"])

    @unittest.skipUnless(os.name == "nt", "Native Windows security APIs")
    def test_native_private_acl_current_user_scope(self):
        who = mesh.identity()
        child = self.root / "private"
        mesh.private_directory(child, who["sid"])
        mesh.verify_private(child, who["sid"])

    @unittest.skipUnless(os.name == "nt", "Native Windows process identity")
    def test_native_process_birth_and_bounded_console_job(self):
        self.assertIsInstance(mesh.process_birth(os.getpid()), int)
        self.assertIsNone(mesh.process_birth(0x7fffffff))
        self.assertEqual(mesh.bounded([sys.executable, "-c", "print('bounded')"], self.root).strip(),
                         b"bounded")
        with mesh.socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.assertEqual(mesh.listener_pid(listener.getsockname()[1]), os.getpid())

    @unittest.skipUnless(os.name == "nt", "Native Windows retained process handles")
    def test_exited_process_with_retained_handle_is_not_live(self):
        with mesh.subprocess.Popen([sys.executable, "-c", "pass"],
                                   stdout=mesh.subprocess.PIPE, stderr=mesh.subprocess.PIPE,
                                   **mesh.no_window_kwargs()) as process:
            process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0)
            self.assertIsNone(mesh.process_birth(process.pid))

    @unittest.skipUnless(os.name == "nt", "Real PowerShell stdin interface")
    def test_native_powershell_authorization_stdin_contract(self):
        who = mesh.identity()
        if who["admin"] or who["session"] == 0:
            self.skipTest("Installer correctly refuses this token/session")
        pilot = self.root / "pilot"
        mesh.private_directory(pilot, who["sid"])
        mesh.private_directory(pilot / "config", who["sid"])
        command = "$input | & '%s' -Action config-authorize -Root '%s' -Python '%s'" % (
            SERVICE / "install.ps1", pilot, sys.executable)
        powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        process, job = mesh.spawn_sync_in_kill_on_close_job(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
            require_job=True, allow_breakaway=False, cwd=str(self.root),
            stdin=mesh.subprocess.PIPE, stdout=mesh.subprocess.PIPE,
            stderr=mesh.subprocess.PIPE, **mesh.no_window_kwargs())
        try:
            public_only = {"peers": [{"machine": "node-e", "user": "operator",
                                     "public_key": KEY, "host_key": KEY}]}
            stdout, stderr = process.communicate(
                (json.dumps(public_only) + "\n").encode("ascii"), timeout=30)
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8"))
            self.assertTrue(json.loads(stdout)["authorized"])
            self.assertTrue((pilot / "config" / "authorized-peers.json").exists())
            self.assertFalse((pilot / "config" / "60-gateway-ssh-mesh.conf").exists())
        finally:
            if job is not None:
                job.terminate_and_wait(10)

    @unittest.skipUnless(os.name == "nt", "Real nested PowerShell exit propagation")
    def test_native_stable_launcher_preserves_notready_and_refusal_exit_codes(self):
        who = mesh.identity()
        if who["admin"] or who["session"] == 0:
            self.skipTest("Installer correctly refuses this token/session")
        pilot = self.root / "launcher-fixture"
        mesh.private_directory(pilot, who["sid"])
        for directory in ("config", "state", "logs", "bin", "versions"):
            mesh.private_directory(pilot / directory, who["sid"])
        slot = pilot / "versions" / ("a" * 64)
        slot.mkdir()
        for relative in mesh.source_files():
            destination = slot.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative == "agent-ssh/module.yaml" and not (SERVICE / relative).exists():

                destination.write_text('{"module":"gateway-relay"}\n')
            else:
                shutil.copyfile(SERVICE / relative, destination)
        mesh.atomic(pilot / "active", slot.name + "\n")
        mesh.write_launcher(pilot)
        powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        for expected in (2, 1):
            with self.subTest(exit_code=expected):
                if expected == 1:
                    mesh.write_json(pilot / "state" / "daemon.json", {})
                process, job = mesh.spawn_sync_in_kill_on_close_job(
                    [str(powershell), "-NoProfile", "-NonInteractive", "-File",
                     str(pilot / "bin" / "start.ps1"), "-Action", "status"],
                    require_job=True, allow_breakaway=False, cwd=str(self.root),
                    stdin=mesh.subprocess.DEVNULL, stdout=mesh.subprocess.PIPE,
                    stderr=mesh.subprocess.PIPE, **mesh.no_window_kwargs())
                try:
                    stdout, stderr = process.communicate(timeout=30)
                    self.assertEqual(process.returncode, expected,
                                     (stdout + stderr).decode("utf-8"))
                    result = json.loads(stdout if expected == 2 else stderr)
                    self.assertFalse(result["ready"])
                finally:
                    if job is not None:
                        job.terminate_and_wait(10)

    @unittest.skipUnless(os.name == "nt", "Bounded native ssh-keygen ACL regression")
    def test_native_generated_keys_are_tightened_and_preserved(self):
        who = mesh.require_user()
        binary = Path(os.environ.get("GATEWAY_MESH_TEST_SSH_KEYGEN", str(
            Path(os.environ["SystemRoot"]) / "System32" / "OpenSSH" / "ssh-keygen.exe")))
        self.assertTrue(binary.is_file(), "Supply an existing native test-only ssh-keygen")
        powershell = Path(shutil.which("pwsh.exe") or str(
            Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"))
        signature = mesh.bounded(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command",
             "Import-Module Microsoft.PowerShell.Security -ErrorAction Stop; "
             "(Get-AuthenticodeSignature -FilePath '%s').Status" %
             str(binary).replace("'", "''")], self.root).strip()
        self.assertEqual(signature, b"Valid", "Native test binary signature must verify")
        binary_digest = mesh.hashlib.sha256(binary.read_bytes()).hexdigest()
        pilot = self.root / "key-fixture"
        mesh.private_directory(pilot, who["sid"])
        for name in ("config", "state"):
            mesh.private_directory(pilot / name, who["sid"])
        with patch.object(mesh, "find_binary", return_value=binary):
            mesh.ensure_keys(pilot, Path("test-only-no-runtime-import"))
        saved = {}
        for name in mesh.GENERATED_KEY_FILES:
            key = pilot / "config" / name
            mesh.verify_private(key, who["sid"], protected=True)
            saved[name] = key.read_bytes()
        with patch.object(mesh, "bounded", side_effect=AssertionError("Key rotation forbidden")):
            mesh.ensure_keys(pilot, Path("test-only-no-runtime-import"))
        for name, value in saved.items():
            self.assertEqual((pilot / "config" / name).read_bytes(), value)
        mesh.repair_generated_key_acls(pilot, who["sid"])
        for name, value in saved.items():
            self.assertEqual((pilot / "config" / name).read_bytes(), value)
        self.assertEqual(mesh.hashlib.sha256(binary.read_bytes()).hexdigest(), binary_digest)

    @unittest.skipUnless(os.name == "nt", "Native verified-owned ACL refusal")
    def test_generated_key_repair_refuses_hardlinks_and_unknown_files(self):
        who = mesh.identity()
        pilot = self.root / "refusal-fixture"
        mesh.private_directory(pilot, who["sid"])
        mesh.private_directory(pilot / "config", who["sid"])
        source = pilot / "config" / "client_ed25519"
        source.write_bytes(b"fixture")
        os.link(source, pilot / "config" / "host_ed25519")
        with self.assertRaisesRegex(mesh.MeshError, "non-linked"):
            mesh.normalize_generated_key(source, who["sid"])
        self.assertEqual(source.read_bytes(), b"fixture")
        with self.assertRaisesRegex(mesh.MeshError, "four generated"):
            mesh.normalize_generated_key(pilot / "config" / "other", who["sid"])
        (pilot / "config" / "host_ed25519").unlink()
        with patch.object(mesh, "verify_private"), \
                patch.object(mesh, "sid_string", return_value="S-1-5-18"):
            with self.assertRaisesRegex(mesh.MeshError, "foreign ownership"):
                mesh.normalize_generated_key(source, who["sid"])
        with patch.object(mesh, "verify_private"), \
                patch.object(mesh, "sid_string", side_effect=[who["sid"], "S-1-5-11"]):
            with self.assertRaisesRegex(mesh.MeshError, "foreign"):
                mesh.normalize_generated_key(source, who["sid"])
        self.assertEqual(source.read_bytes(), b"fixture")


if __name__ == "__main__":
    unittest.main()
