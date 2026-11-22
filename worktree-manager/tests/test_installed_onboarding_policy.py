"""Exercise the installed passive readers, not checkout imports."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class InstalledPolicyTest(unittest.TestCase):
    def test_explicit_pair_without_service_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            anchor, companion = root / "control", root / "knowledge"
            anchor.mkdir()
            companion.mkdir()

            def observe(app: str, expected: str | None, *, bound: bool = True, index_machine: str = "fixture") -> None:
                probe = (
                    "import sys,runpy\n"
                    "def guard(event,args):\n"
                    " if event in ('subprocess.Popen','os.system','socket.connect','socket.bind'):"
                    " raise AssertionError('unexpected service effect: '+event)\n"
                    "sys.addaudithook(guard)\n"
                    "runpy.run_module('worktree_manager.onboarding_policy',run_name='__main__')"
                )
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", probe, app, temp, "control",
                     str(anchor), str(companion) if bound else "", "worktrees-alias"], capture_output=True, text=True,
                    env={**os.environ, "AGENT_INDEX_MACHINE": index_machine}, timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.assertNotIn("unexpected service effect", result.stderr)
                if expected is None:
                    self.assertNotEqual(result.returncode, 0)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), expected)

            observe("agent-index", "unconfigured")
            observe("agent-vault", "configuration-empty")
            index = companion / ".agent-index" / "config.yaml"
            index.parent.mkdir()
            index.write_text("indexer:\n  machine: fixture\n", encoding="utf-8")
            vault = companion / ".agent-vault.json"
            vault.write_text('{"enabled":true}', encoding="utf-8")
            observe("agent-index", "unconfigured")
            policy = anchor / ".agent-worktrees" / "config.yaml"
            policy.parent.mkdir()
            policy.write_text("requires_external_state_root: true\n", encoding="utf-8")
            observe("agent-index", "host")
            observe("agent-index", "client", index_machine="different-index-host")
            observe("agent-vault", "configured")
            base_vault = anchor / ".agent-vault.json"
            base_vault.write_text('{"enabled":true,"port":"invalid"}', encoding="utf-8")
            observe("agent-vault", None)
            base_vault.unlink()
            base_index = anchor / ".agent-index" / "config.yaml"
            base_index.parent.mkdir()
            base_index.write_text("indexers: not-a-list", encoding="utf-8")
            observe("agent-index", None)
            base_index.write_text("indexer:\n  machine: fixture\n", encoding="utf-8")
            observe("agent-index", "host", bound=False)
            base_index.unlink()
            observe("agent-index", None, bound=False)
            vault.write_text('{"enabled":"false"}', encoding="utf-8")
            index.write_text("indexers: not-a-list", encoding="utf-8")
            observe("agent-vault", None)
            observe("agent-index", None)
            self.assertFalse((root / "apps").exists())


if __name__ == "__main__":
    unittest.main()
