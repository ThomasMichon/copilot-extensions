import importlib.util
import io
import os
from pathlib import Path
import sys
import time
import unittest


SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostic_mesh", SERVICE / "windows.py")
mesh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mesh
spec.loader.exec_module(mesh)


class RelayDiagnosticTests(unittest.TestCase):
    def test_exact_reverse_failure_is_not_misreported_as_proven_stale_ownership(self):
        capture = mesh.RelayDiagnostics(23001)
        capture.read(io.BytesIO(
            b"Error: remote port forwarding failed for listen port 23001\r\n"))
        self.assertEqual(capture.result(), {
            "kind": "reverse-forward-refused", "port": 23001,
            "owner": "unknown", "automatic_action": "bounded-retry",
        })

    def test_wrong_port_unknown_and_credentials_never_enter_status(self):
        capture = mesh.RelayDiagnostics(23001)
        capture.read(io.BytesIO(
            b"Error: remote port forwarding failed for listen port 23002\n"
            b"private path and credential-shaped arbitrary output\n"))
        self.assertIsNone(capture.result())
        self.assertNotIn("private", repr(capture.__dict__))

    def test_auth_and_host_key_failures_have_distinct_codes(self):
        for line, kind in (
            (b"operator@host: Permission denied (publickey).\n", "authentication-refused"),
            (b"Host key verification failed.\n", "host-key-refused"),
        ):
            with self.subTest(kind=kind):
                capture = mesh.RelayDiagnostics(23001)
                capture.read(io.BytesIO(line))
                self.assertEqual(capture.result()["kind"], kind)
                self.assertNotIn("operator", repr(capture.result()))

    def test_large_or_unterminated_output_is_bounded_and_drained(self):
        capture = mesh.RelayDiagnostics(23001)
        capture.read(io.BytesIO(b"x" * (1024 * 1024) + b"\n"
                               b"remote port forwarding failed for listen port 23001"))
        self.assertEqual(capture.result()["kind"], "reverse-forward-refused")
        self.assertLess(len(repr(capture.__dict__)), 1500)

    def test_read_failure_is_visible_not_success(self):
        class Broken:
            def read(self, _size):
                raise OSError("must not expose private diagnostic output")

        capture = mesh.RelayDiagnostics(23001)
        capture.read(Broken())
        self.assertEqual(capture.result()["kind"], "diagnostic-read-failed")
        self.assertNotIn("private", repr(capture.result()))

    def test_closed_diagnostic_pipe_never_raises_into_supervisor(self):
        capture = mesh.RelayDiagnostics(23001)
        stream = io.BytesIO()
        stream.close()
        capture.pump(stream)
        self.assertEqual(capture.result()["kind"], "diagnostic-read-failed")

    @unittest.skipUnless(os.name == "nt", "native Windows owned-child pipe contract")
    def test_real_owned_child_drains_large_stderr_and_classifies_without_threads(self):
        child = mesh.Child("gateway-ssh")
        command = [sys.executable, "-c",
                   "import sys;sys.stderr.write('x'*200000+'\\n');"
                   "sys.stderr.write('remote port forwarding failed for listen port 23001\\n')",
                   "-R", "127.0.0.1:23001:127.0.0.1:2223"]
        try:
            child.start(command, SERVICE, os.environ.copy())
            deadline = time.monotonic() + 15
            while child.process is not None and time.monotonic() < deadline:
                child.observe()
                time.sleep(0.01)
            self.assertIsNone(child.process, "Owned noisy child failed to settle")
            self.assertEqual(child.last_failure["kind"], "reverse-forward-refused")
            self.assertFalse(hasattr(child, "reader"))
        finally:
            child.cleanup()
