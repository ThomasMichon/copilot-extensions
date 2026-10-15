import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import helper_process_probe as probe


class HelperProbeTests(unittest.TestCase):
    def test_optimized_python_still_executes_measured_operations(self):
        code = (
            "import helper_process_probe as p\n"
            "calls = []\n"
            "def operation(consumer):\n"
            "    calls.append(consumer)\n"
            "    return {'fixture': consumer}\n"
            "p.measure(operation, 2)\n"
            "if sorted(calls) != ['alpha', 'alpha', 'beta', 'beta']:\n"
            "    raise RuntimeError('optimized probe skipped operations')\n"
        )
        result = subprocess.run(
            [sys.executable, "-O", "-c", code],
            cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=15,
            **probe.no_window_kwargs(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_process_contract(self):
        result = probe.probe(2)
        for mode in (
            "warm_direct_read", "fresh_process_per_read",
            "two_resident_helpers", "one_shared_helper",
        ):
            self.assertEqual(result[mode]["requests"], 4)
            self.assertGreaterEqual(result[mode]["median_ms"], 0)
        self.assertTrue(result["closed_helper_refused_and_direct_path_preserved"])

    def test_consumer_mismatch_is_an_error(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "alpha").mkdir()
            (root / "alpha" / "active.json").write_text(
                json.dumps({"fixture": "beta"}), encoding="utf-8"
            )
            with self.assertRaises(RuntimeError):
                probe.read(root, "alpha")
            with self.assertRaises(ValueError):
                probe.read(root, "../beta")

    def test_failed_readiness_reaps_helper(self):
        spawned = []
        real_popen = subprocess.Popen

        def record(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with patch.object(probe.subprocess, "Popen", side_effect=record):
                with self.assertRaises(probe.client.DaemonUnavailable):
                    with probe.helper(Path(directory)):
                        self.fail("missing fixtures must prevent readiness")
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0].poll(), 0)
