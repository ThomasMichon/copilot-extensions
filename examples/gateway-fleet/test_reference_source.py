"""Static publication invariants; never starts prototype processes."""

import ast
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent


class ReferenceSourceTests(unittest.TestCase):
    def test_sources_compile_without_deployment_files(self):
        for path in ROOT.rglob("*.py"):
            with self.subTest(path=path.relative_to(ROOT)):
                compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_identifier_literals_are_synthetic(self):
        pattern = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".json", ".yaml"}:
                continue
            for value in pattern.findall(path.read_text(encoding="utf-8")):
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertRegex(value, r"^0000000[1-6]-0000-4000-8000-000000000000$")

    def test_examples_do_not_select_autostart_or_an_internal_provider(self):
        config = json.loads((ROOT / "ssh-mesh" / "windows.yaml").read_text())
        self.assertIs(config["autostart"], False)
        self.assertEqual(config["backend"], "openssh-reverse")
        self.assertEqual(config["user"], "operator")
        self.assertFalse(config["gateway_http"])

    def test_public_modules_have_no_absolute_machine_source_checkout(self):
        for path in ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    with self.subTest(path=path.relative_to(ROOT)):
                        self.assertNotRegex(node.value, r"(?i)[a-z]:[\\/].*src[\\/]")


if __name__ == "__main__":
    unittest.main()
