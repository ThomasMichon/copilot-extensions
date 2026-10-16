"""Exercise the actual payload entry with an isolated, credential-free SDK."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from budget_guidance.cli import main


PLUGIN = Path(__file__).resolve().parents[1]


def test_host_quota_entry_and_boundaries(tmp_path, capsys):
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the extension contract tests"
    root = tmp_path / "plugin"
    shutil.copytree(PLUGIN / "extensions", root / "extensions")
    (root / "tests").mkdir()
    shutil.copyfile(PLUGIN / "tests" / "host-quota.test.mjs", root / "tests" / "host-quota.test.mjs")
    sdk = root / "node_modules" / "@github" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {"./extension": "./extension.mjs"},
    }), encoding="utf-8")
    (sdk / "extension.mjs").write_text("""
export const state = { joins: 0, calls: [], options: null };
export async function joinSession(options) {
    state.joins++;
    state.options = options;
    const forbidden = () => { throw new Error("forbidden mutation"); };
    return {
        sessionId: "session-example",
        rpc: {
            quota: {
                async get() {
                    state.calls.push("quota");
                    return { isTbbUser: true, token: "sensitive-sentinel", snapshots: {
                        premium_interactions: { isUnlimitedEntitlement: false,
                            entitlementRequests: 1000, usedRequests: 250,
                            tokenBasedBilling: true, resetDateEpochMs: 1896134400000 }
                    }};
                },
                refresh: forbidden, takeWarnings: forbidden,
            },
            gitHubAuth: {
                async getStatus() {
                    state.calls.push("status");
                    return { isAuthenticated: true, login: "Example_User",
                        host: "https://github.com", token: "sensitive-sentinel" };
                },
                setCredentials: forbidden,
            },
        },
        abort: forbidden, destroy: forbidden, disconnect: forbidden,
        get connection() { return forbidden(); },
        get auth() { return forbidden(); },
    };
}
export class CopilotClient { constructor() { throw new Error("new client forbidden"); } }
""", encoding="utf-8")
    # This test process runs only under the repository's bounded test runner.
    result = subprocess.run(
        [node, "--test", str(root / "tests" / "host-quota.test.mjs")],
        cwd=root, text=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    entry = next(line for line in result.stdout.splitlines() if "ENTRY_CAPTURE=" in line)
    capture = json.loads(entry.split("ENTRY_CAPTURE=", 1)[1])
    capture_path = tmp_path / "capture.json"
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    assert main([
        "quota", "--capture", str(capture_path), "--session-id", "session-example", "--json",
    ]) == 0
    reading = json.loads(capsys.readouterr().out)
    assert reading["attribution"] == "caller-declared-capture"
    assert reading["availability"] == "cached"
    assert reading["freshness"] == "unknown"
    assert reading["allowance"] == "1000"
    assert reading["calculated"] is None
    assert reading["advice"]["concurrency"] is None
