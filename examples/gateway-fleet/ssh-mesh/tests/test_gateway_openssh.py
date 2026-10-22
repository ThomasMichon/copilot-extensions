import base64
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("gateway_openssh", HERE.parent / "gateway_openssh.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def key(number):
    raw = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([number]) * 32
    return "ssh-ed25519 " + base64.b64encode(raw).decode()


class OpenSSHAdapterTests(unittest.TestCase):
    def setUp(self):
        self.uid = os.getuid() if os.name != "nt" else 1000
        if self.uid == 0:
            self.skipTest("Non-root adapter permission tests require a non-root test account")
        self.workspace = HERE / (".test-openssh-" + uuid.uuid4().hex)
        self.workspace.mkdir(mode=0o700)
        self.home = self.workspace / "home"
        self.home.mkdir(mode=0o700)
        self.root = self.workspace / "runtime"
        self.source = self.workspace / "gateway_openssh.py"
        self.source.write_bytes((HERE.parent / "gateway_openssh.py").read_bytes())
        self.host = self.workspace / "ssh_host_ed25519_key.pub"
        self.host.write_text(key(1) + " platform-public-host-key\n")
        os.chmod(self.host, 0o644)
        self.config = {
            "schema": 1, "gateway_user": "operator", "host_public_key": str(self.host),
            "server_port": 22, "peers": dict(zip(module.MACHINES, range(23001, 23006))),
        }
        self.config_path = self.workspace / "gateway-openssh.yaml"
        self.config_path.write_bytes(module.encode(self.config))
        self.adapter = module.Adapter(self.root, self.home, "operator", self.uid,
                                      probe=lambda port: "SSH-2.0-OpenSSH_9.6" if port == 22 else None)
        self.original = (key(2) + " bootstrap-wave-key\n").encode()
        self.adapter.keys.parent.mkdir(mode=0o700)
        self.adapter.keys.write_bytes(self.original)
        os.chmod(self.adapter.keys, 0o600)
        self.peer = {"machine": "node-e", "user": "operator",
                     "public_key": key(3), "host_key": key(4)}

    def tearDown(self):
        shutil.rmtree(self.workspace)

    def install(self):
        return self.adapter.install(self.source, self.config_path)

    def enroll(self):
        return self.adapter.import_peers({"peers": [self.peer]})

    def test_initial_install_exports_full_public_descriptor(self):
        status = self.install()
        self.assertTrue(status["installed"])
        self.assertFalse(status["enrollment_enabled"])
        self.assertTrue(status["platform_ssh"]["provider_owned"])
        self.assertTrue(status["platform_ssh"]["observed"])
        self.assertEqual(status["authenticated"], "unknown")
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)
        descriptor = self.adapter.export_client("node-e")
        self.assertEqual(descriptor, {
            "schema": 1, "backend": "openssh-reverse", "machine": "operator-node-e",
            "gateway_user": "operator", "host_key": key(1), "server_port": 22,
            "relay_port": 23005,
            "peers": [{"machine": name, "relay_port": port} for name, port in self.config["peers"].items()],
        })

    def test_restricted_routes_and_no_session_agent_or_pty(self):
        self.install()
        self.enroll()
        data = self.adapter.keys.read_text()
        own = next(line for line in data.splitlines() if module.SERVICE + ":operator-node-e" in line)
        self.assertIn('restrict,port-forwarding,command="/bin/false"', own)
        self.assertEqual(own.count("permitlisten="), 1)
        self.assertIn('permitlisten="127.0.0.1:23005"', own)
        for port in range(23001, 23006):
            self.assertIn(f'permitopen="127.0.0.1:{port}"', own)
        for forbidden in ('permitopen="127.0.0.1:22"', 'permitopen="127.0.0.1:8787"',
                          'permitlisten="0.0.0.0:', 'permitlisten="127.0.0.1:23001"',
                          "agent-forwarding", "pty", 'permitopen="*"'):
            self.assertNotIn(forbidden, own)
        self.assertTrue(data.encode().startswith(self.original))

    def test_repeat_install_and_import_are_idempotent(self):
        self.install()
        self.enroll()
        before = self.adapter.keys.read_bytes()
        inode = self.adapter.keys.stat().st_ino
        self.install()
        self.enroll()
        self.assertEqual(self.adapter.keys.read_bytes(), before)
        self.assertEqual(self.adapter.keys.stat().st_ino, inode)
        self.assertEqual(before.count(module.BEGIN), 1)
        self.assertEqual(before.count(module.END), 1)

    def test_http_opt_in_requires_review_and_changes_only_fixed_destination(self):
        self.install()
        self.enroll()
        before = self.adapter.keys.read_bytes()
        directory = self.adapter.export_directory()
        descriptor = self.adapter.export_client("node-e")
        self.config_path.write_bytes(module.encode(dict(self.config, gateway_http=True)))
        with self.assertRaisesRegex(module.AdapterError, "accept-config"):
            self.adapter.install(self.source, self.config_path)
        self.assertEqual(self.adapter.keys.read_bytes(), before)
        status = self.adapter.install(self.source, self.config_path, accept_config=True)
        self.assertTrue(status["gateway_http_configured"])
        self.assertTrue(status["gateway_http_authorized"])
        owned = next(line for line in self.adapter.keys.read_bytes().splitlines()
                     if module.SERVICE.encode() + b":operator-node-e" in line)
        self.assertIn(b'permitopen="127.0.0.1:8787"', owned)
        self.assertEqual(self.adapter.keys.read_bytes().replace(
            b',permitopen="127.0.0.1:8787"', b""), before)
        self.assertEqual(self.adapter.export_directory(), directory)
        self.assertEqual(self.adapter.export_client("node-e"), descriptor)
        self.config_path.write_bytes(module.encode(dict(self.config, gateway_http=False)))
        status = self.adapter.install(self.source, self.config_path, accept_config=True)
        self.assertFalse(status["gateway_http_authorized"])
        self.assertEqual(self.adapter.keys.read_bytes(), before)

    def test_http_config_acceptance_cannot_change_other_gateway_authority(self):
        self.install()
        self.enroll()
        before = self.adapter.keys.read_bytes()
        alternative = self.workspace / "other" / "ssh_host_ed25519_key.pub"
        alternative.parent.mkdir()
        alternative.write_bytes(self.host.read_bytes())
        os.chmod(alternative, 0o644)
        self.config_path.write_bytes(module.encode(dict(
            self.config, gateway_http=True, host_public_key=str(alternative))))
        with self.assertRaisesRegex(module.AdapterError, "Only the fixed"):
            self.adapter.install(self.source, self.config_path, accept_config=True)
        self.assertEqual(self.adapter.keys.read_bytes(), before)
        self.assertEqual(self.adapter.export_directory()["peers"][0]["public_key"], self.peer["public_key"])

    def test_http_opt_in_is_not_an_arbitrary_route_or_non_boolean_flag(self):
        for value in (None, 1, 0, "true", {"host": "elsewhere", "port": 22}):
            with self.subTest(value=value), self.assertRaises(module.AdapterError):
                module.config_policy(dict(self.config, gateway_http=value), "operator")
        self.config_path.write_bytes(module.encode(dict(self.config, gateway_http=True)))
        status = self.install()
        self.assertTrue(status["gateway_http_configured"])
        self.assertFalse(status["gateway_http_authorized"])
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)

    def test_public_readable_authorized_keys_permission_is_allowed_and_preserved(self):
        self.install()
        os.chmod(self.adapter.keys, 0o644)
        original_mode = stat.S_IMODE(self.adapter.keys.stat().st_mode)
        self.enroll()
        self.adapter.control(False)
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.adapter.keys.stat().st_mode), original_mode)
        original_stat = Path.stat

        def public_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            values = list(result)
            values[4] = self.uid
            if path == self.adapter.keys:
                values[0] = stat.S_IFREG | 0o644
            return os.stat_result(values)

        with patch.object(module.os, "name", "posix"), patch.object(Path, "stat", public_stat):
            self.assertEqual(module.checked_bytes(self.adapter.keys, self.uid, authorization=True), self.original)
            with self.assertRaisesRegex(module.AdapterError, "permissions"):
                module.checked_bytes(self.adapter.keys, self.uid)

    def test_stop_uninstall_preserve_unrelated_keys_and_public_config(self):
        self.install()
        self.enroll()
        status = self.adapter.control(False)
        self.assertFalse(status["enrollment_enabled"])
        self.assertEqual(status["revocation_scope"], "new-authentications-only")
        self.assertEqual(status["existing_sessions"], "unknown")
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)
        self.adapter.control(True)
        added = (key(7) + " later-bootstrap-key\n").encode()
        self.adapter.keys.write_bytes(self.adapter.keys.read_bytes() + added)
        self.adapter.uninstall()
        self.assertEqual(self.adapter.keys.read_bytes(), self.original + added)
        self.assertTrue((self.root / "config" / "peers.json").exists())
        self.assertFalse(self.adapter.status()["installed"])
        self.install()
        self.assertTrue(self.adapter.status()["enrollment_enabled"])
        self.adapter.uninstall(remove_config=True)
        self.assertEqual(self.adapter.keys.read_bytes(), self.original + added)
        self.assertEqual(list((self.root / "config").iterdir()), [])
        self.assertFalse(self.adapter.status()["installed"])

    def test_original_without_terminal_newline_is_restored_exactly(self):
        self.adapter.keys.write_bytes(self.original.rstrip(b"\n"))
        self.install()
        self.enroll()
        self.adapter.control(False)
        self.assertEqual(self.adapter.keys.read_bytes(), self.original.rstrip(b"\n"))

    def test_missing_authorized_keys_never_deletes_unrelated_file(self):
        self.adapter.keys.unlink()
        self.install()
        self.assertFalse(self.adapter.keys.exists())
        self.enroll()
        self.assertTrue(self.adapter.keys.exists())
        self.adapter.uninstall()
        self.assertTrue(self.adapter.keys.exists())
        self.assertEqual(self.adapter.keys.read_bytes(), b"")

    def test_merge_public_directory_and_no_implicit_key_rotation(self):
        self.install()
        self.enroll()
        peer2 = dict(self.peer, machine="operator-node-a", public_key=key(8), host_key=key(9))
        self.adapter.import_peers({"peers": [peer2]})
        directory = self.adapter.export_directory()
        self.assertEqual(len(directory["peers"]), 2)
        self.assertEqual({row["machine"] for row in directory["peers"]},
                         {"operator-node-e", "operator-node-a"})
        before = self.adapter.keys.read_bytes()
        with self.assertRaisesRegex(module.AdapterError, "rotation"):
            self.adapter.import_peers({"peers": [dict(self.peer, public_key=key(10))]})
        self.assertEqual(before, self.adapter.keys.read_bytes())

    def test_duplicate_machine_or_shared_forwarding_key_refuses(self):
        self.install()
        for payload in (
            {"peers": [self.peer, dict(self.peer, machine="operator-node-e")]},
            {"peers": [self.peer, dict(self.peer, machine="node-a", host_key=key(5))]},
        ):
            with self.assertRaises(module.AdapterError):
                self.adapter.import_peers(payload)
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)

    def test_immutable_script_and_unknown_slot_state_refuse_before_key_mutation(self):
        self.install()
        self.enroll()
        before = self.adapter.keys.read_bytes()
        slot = self.root / "versions" / self.adapter.state()["slot"]
        script = slot / "gateway_openssh.py"
        actual = script.read_bytes()
        script.write_bytes(actual + b"\n# unexpected change\n")
        with self.assertRaisesRegex(module.AdapterError, "immutable script drift"):
            self.adapter.uninstall()
        self.assertEqual(self.adapter.keys.read_bytes(), before)
        script.write_bytes(actual)
        unknown = slot / "unowned-file"
        unknown.write_bytes(b"preserve user state")
        os.chmod(unknown, 0o600)
        with self.assertRaisesRegex(module.AdapterError, "Unexpected immutable"):
            self.adapter.uninstall()
        self.assertEqual(self.adapter.keys.read_bytes(), before)

    def test_malformed_keys_names_and_unauthorized_topology_are_refused(self):
        self.install()
        for value in ("ssh-rsa AAAA", "ssh-ed25519 AAAA", key(3) + "\ncommand=oops",
                      "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh-ed25519 !!!!"):
            with self.subTest(value=value), self.assertRaises(module.AdapterError):
                self.adapter.import_peers({"peers": [dict(self.peer, public_key=value)]})
        for name in ("unknown", "cloud6", "operator-node-e\nbad"):
            with self.assertRaises(module.AdapterError):
                self.adapter.export_client(name)
        bad = dict(self.config, peers=dict(self.config["peers"], **{"operator-node-e": 22}))
        with self.assertRaises(module.AdapterError):
            module.config_policy(bad, "operator")
        with self.assertRaises(module.AdapterError):
            module.config_policy(dict(self.config, gateway_user="root"), "operator")
        with self.assertRaises(module.AdapterError):
            module.config_policy(dict(self.config, host_public_key=str(self.host).removesuffix(".pub")), "operator")

    def test_foreign_duplicate_and_changed_block_refuse(self):
        self.install()
        self.adapter.keys.write_bytes(self.original + module.BEGIN + b"foreign\n" + module.END + b"foreign\n")
        with self.assertRaisesRegex(module.AdapterError, "Foreign"):
            self.enroll()
        self.adapter.keys.write_bytes(self.original)
        self.enroll()
        original = self.adapter.keys.read_bytes()
        self.adapter.keys.write_bytes(original.replace(b'command="/bin/false"', b'command="/bin/true"'))
        for action in (self.adapter.status, lambda: self.adapter.control(False), self.adapter.uninstall):
            with self.assertRaisesRegex(module.AdapterError, "block content/source drift"):
                action()
        self.adapter.keys.write_bytes(original + original[original.index(module.BEGIN):])
        with self.assertRaisesRegex(module.AdapterError, "duplicate"):
            self.adapter.status()

    def test_owned_config_and_host_identity_drift_refuse(self):
        self.install()
        (self.root / "config" / "peers.json").write_bytes(b'{"peers":[]} ')
        with self.assertRaisesRegex(module.AdapterError, "source drift"):
            self.adapter.status()
        (self.root / "config" / "peers.json").write_bytes(module.encode({"peers": []}))
        self.host.write_text(key(9) + "\n")
        with self.assertRaisesRegex(module.AdapterError, "host public identity"):
            self.adapter.export_client("node-e")

    def test_provider_absence_or_missing_public_host_key_refuses_without_authorizing(self):
        with patch.object(self.adapter, "probe", return_value=None):
            with self.assertRaisesRegex(module.AdapterError, "platform SSH banner"):
                self.install()
        self.host.unlink()
        with self.assertRaisesRegex(module.AdapterError, "PUBLIC host key unavailable"):
            self.install()
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)

    def test_atomic_authorization_failure_rolls_back_enrollment_and_preserves_keys(self):
        self.install()
        before_state = (self.root / "state" / "active.json").read_bytes()
        before_peers = (self.root / "config" / "peers.json").read_bytes()
        replace = module.os.replace

        def fail(source, target):
            if Path(target) == self.adapter.keys:
                raise OSError("simulated replace refusal")
            return replace(source, target)

        with patch.object(module.os, "replace", side_effect=fail):
            with self.assertRaisesRegex(module.AdapterError, "previous authorization/state restored"):
                self.enroll()
        self.assertEqual(self.adapter.keys.read_bytes(), self.original)
        self.assertEqual((self.root / "state" / "active.json").read_bytes(), before_state)
        self.assertEqual((self.root / "config" / "peers.json").read_bytes(), before_peers)
        self.assertFalse(any(self.workspace.rglob("*.pending")))

    def test_update_metadata_failure_rolls_back_selection_and_authorization(self):
        self.install()
        self.enroll()
        original = {path: path.read_bytes() for path in (
            self.root / "state" / "active.json", self.root / "current-version",
            self.root / "deploy-manifest.json", self.adapter.keys)}
        self.source.write_bytes(self.source.read_bytes() + b"\n# updated installer\n")
        replace = module.os.replace
        failed = False

        def fail(source, target):
            nonlocal failed
            if Path(target).name == "deploy-manifest.json" and not failed:
                failed = True
                raise OSError("simulated receipt write failure")
            return replace(source, target)

        with patch.object(module.os, "replace", side_effect=fail):
            with self.assertRaisesRegex(module.AdapterError, "restored"):
                self.install()
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertTrue(self.adapter.status()["enrollment_enabled"])

    def test_hardlink_and_symlink_authorized_keys_are_refused(self):
        linked = self.workspace / "linked-keyfile"
        os.link(self.adapter.keys, linked)
        try:
            with self.assertRaisesRegex(module.AdapterError, "Hard-linked"):
                self.install()
        finally:
            linked.unlink()
        self.adapter.keys.unlink()
        try:
            self.adapter.keys.symlink_to(linked)
        except OSError:
            self.skipTest("Creating symlink requires Windows developer mode/privilege")
        with self.assertRaisesRegex(module.AdapterError, "Symlink"):
            self.install()

    def test_private_file_permission_and_owner_drift_refuse(self):
        self.install()
        real_stat = Path.stat

        def private_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            values = list(result)
            values[4] = self.uid
            values[0] = stat.S_IFDIR | 0o700 if path in (
                self.home, self.adapter.keys.parent) else stat.S_IFREG | 0o600
            return os.stat_result(values)

        def changed(path, *args, **kwargs):
            result = private_stat(path, *args, **kwargs)
            if path == self.adapter.keys:
                values = list(result)
                values[4] = self.uid + 1
                return os.stat_result(values)
            return result

        with patch.object(module.os, "name", "posix"), patch.object(Path, "stat", changed):
            with self.assertRaisesRegex(module.AdapterError, "ownership"):
                self.adapter.key_snapshot()

        def unsafe(path, *args, **kwargs):
            result = private_stat(path, *args, **kwargs)
            if path == self.adapter.keys:
                values = list(result)
                values[0] = stat.S_IFREG | 0o664
                return os.stat_result(values)
            return result

        with patch.object(module.os, "name", "posix"), patch.object(Path, "stat", unsafe):
            with self.assertRaisesRegex(module.AdapterError, "permissions"):
                self.adapter.key_snapshot()

    def test_root_foreign_state_hardlink_and_root_identity_are_refused(self):
        with self.assertRaisesRegex(module.AdapterError, "non-root"):
            module.Adapter(self.root, self.home, "root", 0)
        self.root.mkdir(mode=0o700)
        (self.root / "unknown").write_bytes(b"not owned")
        with self.assertRaisesRegex(module.AdapterError, "foreign"):
            self.install()
        shutil.rmtree(self.root)
        self.install()
        source = self.root / "config" / "gateway.json"
        linked = self.workspace / "linked-config"
        os.link(source, linked)
        with self.assertRaisesRegex(module.AdapterError, "Hard-linked"):
            self.adapter.status()
        linked.unlink()

    def test_installed_script_survives_source_removal_and_has_exact_cli(self):
        self.install()
        slot = self.adapter.state()["slot"]
        installed = self.root / "versions" / slot / "gateway_openssh.py"
        self.source.unlink()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([sys.executable, str(installed), "--help"], capture_output=True,
                                text=True, check=True, **options)
        self.assertIn("import-peers", result.stdout)
        self.assertIn("export-directory", result.stdout)
        self.assertTrue(self.adapter.status()["installed"])

    def test_concurrent_unrelated_change_is_not_overwritten(self):
        self.install()
        previous = self.adapter.keys.read_bytes()
        original = self.adapter.transaction
        extra = (key(11) + " concurrent-bootstrap\n").encode()

        def transaction(updates, **kwargs):
            self.adapter.keys.write_bytes(previous + extra)
            return original(updates, **kwargs)

        with patch.object(self.adapter, "transaction", side_effect=transaction):
            with self.assertRaisesRegex(module.AdapterError, "Concurrent"):
                self.enroll()
        self.assertEqual(self.adapter.keys.read_bytes(), previous + extra)


if __name__ == "__main__":
    unittest.main()
