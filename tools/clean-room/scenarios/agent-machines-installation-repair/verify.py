"""Agent-free Linux first-touch check against the installed candidate binstub."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def main(command: str) -> None:
    with tempfile.TemporaryDirectory(prefix="installation-repair-consumer-") as temporary:
        project = Path(temporary)
        market = project / ".ai"
        payload = market / "agent-example"
        write(market / ".claude-plugin" / "marketplace.json", {
            "name": "fixture", "plugins": [{"name": "agent-example", "source": "./agent-example"}],
        })
        write(project / ".github" / "copilot" / "settings.json", {
            "extraKnownMarketplaces": {
                "fixture": {"source": {"source": "directory", "path": "./.ai"}},
            },
            "enabledPlugins": {"agent-example@fixture": True},
        })
        write(payload / "plugin.json", {
            "name": "agent-example", "version": "1.0.0",
            "installerReadiness": "installer-readiness.json",
        })
        write(payload / "payload-invocation.json", {
            "schema": "copilot-extensions.payload-invocation", "version": 2,
            "command": "agent-example", "module": "agent_example",
            "legacyRuntimeRoot": ".agent-example", "installationContext": "required",
            "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
            "payloadRootEnv": "AGENT_EXAMPLE_PAYLOAD_ROOT", "purpose": "Fixture runtime",
        })
        action = payload / "scripts" / "action.sh"
        action.parent.mkdir()
        action.write_text(
            '#!/usr/bin/env bash\nset -eu\n'
            'state="$HOME/.agent-example/${2//\\//-}.ready"\n'
            'if [ "$1" = install ]; then\n'
            '  [ -z "${AGENT_EXAMPLE_NO_SELFPROVISION:-}" ] || exit 8\n'
            '  mkdir -p "$HOME/.agent-example"; printf ready > "$state"; exit 0\nfi\n'
            '[ "${AGENT_EXAMPLE_NO_SELFPROVISION:-}" = 1 ] || exit 9\n'
            'value=not-ready; [ ! -f "$state" ] || value=ready\n'
            'printf \'{"schema":"copilot-extensions.module-readiness","version":1,'
            '"module":"%s","state":"%s"}\\n\' "$2" "$value"\n',
            encoding="utf-8",
        )
        modules = []
        for name in ("base", "child"):
            module_id = "agent-example/" + name
            modules.append({
                "id": module_id, "platforms": ["linux"], "classification": "required",
                "installer": {"linux": {
                    "kind": "payload-script", "path": "scripts/action.sh",
                    "arguments": ["install", module_id],
                }},
                "readiness": {
                    "schema": "copilot-extensions.module-readiness", "version": 1,
                    "configurationEmpty": "satisfied", "invocations": {"linux": {
                        "kind": "payload-script", "path": "scripts/action.sh",
                        "arguments": ["probe", module_id],
                    }},
                },
                "dependsOn": ["agent-example/base"] if name == "child" else [], "restart": "none",
            })
        write(payload / "installer-readiness.json", {
            "schema": "copilot-extensions.installer-readiness", "version": 1,
            "owner": {"plugin": "agent-example"}, "state": "supported", "modules": modules,
        })
        argv = [command, "installation-repair", "--project-root", str(project)]

        def call(arguments: list[str], expected: int = 0) -> dict:
            result = subprocess.run(
                [*argv, *arguments], capture_output=True, text=True, timeout=30,
                env=os.environ.copy(), start_new_session=True,
            )
            assert result.returncode == expected, result.stderr
            return json.loads(result.stdout) if result.stdout else {}

        available = call([])["available"]
        child = next(item for item in available if item.endswith("::agent-example/child"))
        selection = ["--module", child]
        plan = call(selection)
        assert plan["platform"] == "linux"
        assert not (Path.home() / ".agent-example").exists()
        config = project / "configuration.json"
        config.write_text('{"preserve":true}', encoding="utf-8")
        apply = [*selection, "--apply", "--expected-plan-digest", plan["planDigest"]]
        repaired = call(apply)
        assert repaired["status"] == "ready"
        assert all(row["observedAfter"]["state"] == "ready" for row in repaired["outcomes"])
        repeated = call(apply)
        assert repeated["status"] == "ready"
        assert not any(row["attempted"] for row in repeated["outcomes"])
        assert config.read_text(encoding="utf-8") == '{"preserve":true}'
        action.write_text(action.read_text(encoding="utf-8") + "\n# changed payload\n", encoding="utf-8")
        call(apply, expected=1)
        modules[0]["restart"] = "session"
        write(payload / "installer-readiness.json", {
            "schema": "copilot-extensions.installer-readiness", "version": 1,
            "owner": {"plugin": "agent-example"}, "state": "supported", "modules": modules,
        })
        for name in ("base", "child"):
            (Path.home() / ".agent-example" / f"agent-example-{name}.ready").unlink()
        plan = call(selection)
        apply = [*selection, "--apply", "--expected-plan-digest", plan["planDigest"]]
        first = call(apply, expected=2)
        assert first["outcomes"][0]["attempted"]
        assert first["outcomes"][0]["restartRequired"]
        assert not first["outcomes"][1]["attempted"]
        repeated = call(apply, expected=2)
        assert repeated["restarts"] == first["restarts"]
        assert not any(row["attempted"] for row in repeated["outcomes"])
        call(["--confirm-restart", "0" * 64], expected=1)
        confirmed = call(["--confirm-restart", first["restarts"]["pendingDigest"]])
        assert confirmed["status"] == "restart-confirmed"
        assert confirmed["confirmation"] == "operator-attestation"
        assert not confirmed["restarts"]["pending"]
        repaired = call(apply)
        assert repaired["status"] == "ready"
        assert not repaired["outcomes"][0]["attempted"]
        assert repaired["outcomes"][1]["attempted"]
        assert config.read_text(encoding="utf-8") == '{"preserve":true}'
        print("installed ordinary repair: fresh readiness, retry, preservation, stale refusal, "
              "durable restart blocking and exact operator-confirmed continuation")


if __name__ == "__main__":
    main(sys.argv[1])
