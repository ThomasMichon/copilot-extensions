"""Assert the disposable machine's installed CLI, never a host runtime."""

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

if sys.flags.optimize:
    raise SystemExit("verification requires assertions (run without -O)")
if len(sys.argv) != 3:
    raise SystemExit("usage: verify-install.py <installed-binstub> <snapshot>")

import agent_procutil
import budget_guidance


def package_version(manifest_version):
    number = r"(?:0|[1-9][0-9]*)"
    parts = re.fullmatch(rf"({number}\.{number}\.{number})(?:-dev({number}))?", manifest_version)
    if parts is None:
        raise AssertionError("scenario requires a canonical repository release or -devN version")
    return parts[1] + (".dev" + parts[2] if parts[2] is not None else "")


for raw, normalized in (
    ("1.2.3", "1.2.3"), ("1.2.3-dev0", "1.2.3.dev0"), ("1.2.3-dev4", "1.2.3.dev4"),
):
    assert package_version(raw) == normalized
for malformed in ("1.2.3-dev04", "01.2.3", "1.2.3-dev"):
    try:
        package_version(malformed)
    except AssertionError:
        pass
    else:
        raise AssertionError("noncanonical manifest version was accepted")

bin_path, snapshot = map(Path, sys.argv[1:])
prefix = Path(sys.prefix)
assert Path(budget_guidance.__file__).is_relative_to(prefix)
assert Path(agent_procutil.__file__).is_relative_to(prefix)
expected = json.loads((snapshot / "plugin.json").read_text(encoding="utf-8"))["version"]
assert budget_guidance.__version__ == expected
assert version("budget-guidance") == package_version(expected)
original = snapshot / "src/budget_guidance/quota.py"
installed = Path(budget_guidance.__file__).parent / "quota.py"
assert hashlib.sha256(original.read_bytes()).digest() == hashlib.sha256(installed.read_bytes()).digest()


def status(*args):
    result = subprocess.run(
        [str(bin_path), "status", *args, "--json"],
        encoding="utf-8", errors="strict", capture_output=True, timeout=20, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode:
        raise AssertionError(
            f"installed CLI failed: {result.args!r}, exit={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        posture = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"installed CLI returned invalid JSON: {result.args!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc
    assert posture["schema"] == "copilot-extensions.budget-posture"
    return posture


with tempfile.TemporaryDirectory(prefix="budget-static-") as temporary:
    missing = status("--config", str(Path(temporary) / "absent-config.json"))
    assert missing["availability"] == "unavailable"
    assert missing["calculated"] is None
    config = Path(temporary) / "budget.json"
    config.write_text(json.dumps({
        "schema": "copilot-extensions.budget-guidance-config", "version": 1,
        "adapters": [{
            "id": "manual-example", "type": "static", "authority": 10,
            "reading": {
                "schema": "copilot-extensions.budget-reading", "version": 1,
                "source": "manual-example", "availability": "available",
                "captured_at": "2030-01-10T12:00:00Z", "freshness_seconds": 86400,
                "allowance": 1000, "consumption": 320, "reset_at": "2030-02-01T00:00:00Z",
                "trailing_rates": [{"window_days": 7, "rate_per_day": 34}],
            },
        }],
    }), encoding="utf-8")
    posture = status("--config", str(config), "--at", "2030-01-10T12:00:00Z")
    assert posture["availability"] == "available"
    # Posture v1 deliberately serializes calculated Decimal values as strings.
    assert posture["calculated"]["remaining"] == "680"
    assert posture["fields"]["allowance"]["selected"]["source"] == "manual-example"
    assert posture["routing_advice"]["availability"] == "available"

print("installed source/version identity and offline posture verified")
