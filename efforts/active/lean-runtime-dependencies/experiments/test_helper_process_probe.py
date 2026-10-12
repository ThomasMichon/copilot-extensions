import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import helper_process_probe as probe


class HelperProbeTests(unittest.TestCase):
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
