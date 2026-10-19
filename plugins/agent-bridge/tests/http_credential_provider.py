"""Synthetic attributed provider; no real credential backend is contacted."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def configure(monkeypatch, tmp_path):
    from dropin_registry import EntryDecision, ScanAuthority
    from plugin_activation import ActivationReport, ActivePlugin

    from agent_bridge import agent_registry_relay, provider_sources

    root = tmp_path / "credential-plugin"
    root.mkdir()
    source = "synthetic@example"
    (root / "plugin.json").write_text(json.dumps({"name": "synthetic"}))
    registry = tmp_path / "providers"
    registry.mkdir()
    calls = tmp_path / "credential-calls.jsonl"
    command = [sys.executable, str(Path(__file__).resolve()), str(calls)]
    (registry / "credentials.json").write_text(json.dumps({
        "schema_version": 1, "namespace": "synthetic", "plugin": source,
        "plugin_root": str(root), "command": command,
        "http_credentials_version": 1,
    }))
    plugin = ActivePlugin(source, "synthetic", "example", root.resolve(), ("global",))
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(registry))
    monkeypatch.setattr(provider_sources, "resolve_active_plugins", lambda: ActivationReport(
        ScanAuthority.COMPLETE, {source: EntryDecision.active(plugin)},
    ))
    monkeypatch.setattr(agent_registry_relay, "_register_provider_relay", lambda *args: None)
    return calls, registry


def main():
    calls, operation, *args = sys.argv[1:]
    if operation == "relay-profile":
        print(json.dumps({"http_sources": [{"id": "grant", "scoped": True}]}))
        return
    assert operation == "http-credential" and args == ["grant", "get"]
    fields = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if "=" in line)
    with Path(calls).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(fields) + "\n")
    assert fields["scope"] == "read" and fields["resource"] == "synthetic-resource"
    print("username=synthetic")
    print("pass" + "word=synthetic-credential")


if __name__ == "__main__":
    main()
