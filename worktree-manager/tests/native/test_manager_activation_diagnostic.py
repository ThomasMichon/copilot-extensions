"""A failed read remains failed while its caught exception becomes inspectable."""

import json
import os
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.timeout(90)
sys.path.insert(0, str(Path(__file__).parent))
from manager_suite_diagnostics import activation_probe


def test_activation_diagnostic_retains_original_reader_failure(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "setup.json").write_text(
        json.dumps({"base": sys.executable}), encoding="utf-8",
    )
    reader = (
        tmp_path / "h" / ".copilot" / "installed-plugins" / "copilot-extensions"
        / "agent-vault" / "scripts" / "runtime-activation.py"
    )
    reader.parent.mkdir(parents=True)
    original = (
        "import json\n"
        "try:\n raise RuntimeError('controlled configuration refusal')\n"
        "except RuntimeError:\n print(json.dumps({'state':'failed'}))\n"
        "raise SystemExit(3)\n"
    )
    reader.write_text(original, encoding="utf-8")
    result = activation_probe(tmp_path, dict(os.environ))
    assert result["activation_probe_returncode"] == 3
    assert json.loads(result["stdout"]) == {"state": "failed"}
    events = json.loads(result["stderr"])["activation_exceptions"]
    assert any(event["type"] == "RuntimeError" for event in events)
    assert reader.read_text(encoding="utf-8") == original
