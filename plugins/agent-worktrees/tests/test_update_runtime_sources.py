"""Prepared-source process contract, independent of package and payload versions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from agent_worktrees import update_cli, update_options, update_runtime_source

SCHEMA = "copilot-extensions.prepared-runtime-sources"


def packet(tmp_path: Path, names=("agent-worktrees",)) -> dict:
    sources = {}
    for name in names:
        payload = tmp_path / name
        payload.mkdir(exist_ok=True)
        identity = {
            "schema": "copilot-extensions.runtime-source", "version": 1,
            "plugin": name, "package_version": "1.0.0", "source_commit": "a" * 40,
            "source_tree": "b" * 40, "assembly_hash": "c" * 64,
            "runtime_version": f"1.0.0+source.{'a' * 40}.{'c' * 16}",
        }
        sources[name] = {
            "payload": str(payload),
            "identity": {"runtime_source": identity, "runtime_version": identity["runtime_version"]},
        }
    return {"schema": SCHEMA, "version": 1, "sources": sources}


def parser():
    result = argparse.ArgumentParser()
    update_cli.add_parsers(result.add_subparsers(dest="command"))
    return result


def test_packet_preserves_each_payload_and_full_selection(tmp_path):
    data = packet(tmp_path, ("agent-worktrees", "agent-example"))
    selected = update_runtime_source.parse_sources(json.dumps(data))
    for name, source in selected.items():
        assert source.payload == tmp_path / name
        assert json.loads(source.identity) == data["sources"][name]["identity"]
    assert update_runtime_source.parse_sources(None) is None


@pytest.mark.parametrize("change", [
    "schema", "version", "boolean-version", "unknown", "empty", "list",
    "unsafe-name", "record", "extra-record", "relative", "missing", "identity",
    "wrong-plugin", "different-commit", "different-tree", "short-commit",
])
def test_invalid_packet_has_no_package_only_fallback(tmp_path, change):
    data = packet(tmp_path, ("agent-worktrees", "agent-example"))
    record = data["sources"]["agent-example"]
    identity = record["identity"]["runtime_source"]
    if change == "schema":
        data["schema"] = "other"
    elif change in {"version", "boolean-version"}:
        data["version"] = True if change == "boolean-version" else 2
    elif change == "unknown":
        data["optional-looking"] = "not an extensible request"
    elif change == "empty":
        data["sources"] = {}
    elif change == "list":
        data["sources"] = []
    elif change == "unsafe-name":
        data["sources"]["../peer"] = data["sources"].pop("agent-example")
    elif change == "record":
        data["sources"]["agent-example"] = []
    elif change == "extra-record":
        record["owner"] = "caller must not replace owner resolution"
    elif change == "relative":
        record["payload"] = "relative"
    elif change == "missing":
        record["payload"] = str(tmp_path / "absent")
    elif change == "identity":
        record["identity"] = {}
    elif change == "wrong-plugin":
        identity["plugin"] = "agent-worktrees"
    elif change == "different-commit":
        identity["source_commit"] = "d" * 40
    elif change == "different-tree":
        identity["source_tree"] = "d" * 40
    else:
        identity["source_commit"] = "abcdef"
    with pytest.raises((OSError, ValueError)):
        update_runtime_source.parse_sources(json.dumps(data))


def test_duplicate_json_fields_are_not_last_writer_wins(tmp_path):
    value = json.dumps(packet(tmp_path))
    with pytest.raises(ValueError, match="duplicate"):
        update_runtime_source.parse_sources(value.replace('"version": 1', '"version": 1, "version": 1', 1))


def test_source_option_survives_existing_queue_flag_roundtrip(tmp_path):
    raw = json.dumps(packet(tmp_path))
    args = parser().parse_args(["update", "--no-manager", "--runtime-sources", raw, "--force"])
    update_options.validate_auth_flags(args)
    assert update_cli._update_flags(args) == ["--force", "--runtime-sources", raw]
    assert vars(parser().parse_args([
        "update", "--no-manager", *update_cli._update_flags(args),
    ])) == vars(args)


@pytest.mark.parametrize("flags", [
    [], ["--inventory"], ["--target", "agent-example"], ["--auth-report"],
    ["--request-status", "previous"], ["--recovery-status"], ["--recover-after-restart"],
    ["--recover-for-repair"],
])
def test_source_packet_cannot_be_ignored_by_another_operation(tmp_path, flags):
    raw = json.dumps(packet(tmp_path))
    argv = ["update", "--runtime-sources", raw, *flags]
    if flags:
        argv.append("--no-manager")
    with pytest.raises(SystemExit):
        update_options.validate_auth_flags(parser().parse_args(argv))


def test_packet_coverage_is_exact_before_any_installer(tmp_path):
    from agent_worktrees.update_runtime_plan import RuntimeUpdateStep

    selected = update_runtime_source.parse_sources(json.dumps(packet(tmp_path)))
    steps = [
        RuntimeUpdateStep("agent-worktrees", "engine_runtime", tmp_path, eligible=True),
        RuntimeUpdateStep("agent-example", "runtime_reconcile", tmp_path, eligible=True),
        RuntimeUpdateStep("disabled", "runtime_reconcile", tmp_path, required=False),
    ]
    with pytest.raises(ValueError, match="missing: agent-example"):
        update_runtime_source.check_source_coverage(selected, steps)
    steps[1] = RuntimeUpdateStep("agent-example", "runtime_reconcile", tmp_path)
    update_runtime_source.check_source_coverage(selected, steps)
    steps[0] = RuntimeUpdateStep("agent-worktrees", "engine_runtime", tmp_path)
    with pytest.raises(ValueError, match="unexpected: agent-worktrees"):
        update_runtime_source.check_source_coverage(selected, steps)


@pytest.mark.parametrize(("provider", "option", "code"), [
    ("current", "--runtime-sources", 0), ("current", "--runtime-source", 2),
    ("prior", "--runtime-sources", 2), ("current", "--force", 0), ("prior", "--force", 0),
])
def test_actual_process_parser_preserves_baseline_and_refuses_unsupported_sources(
    tmp_path, provider, option, code,
):
    raw = json.dumps(packet(tmp_path))
    program = textwrap.dedent("""
        import argparse, sys
        if sys.argv[1] == 'current':
            from agent_worktrees import update_cli, update_options
            parser = argparse.ArgumentParser()
            update_cli.add_parsers(parser.add_subparsers(dest='command'))
            args = parser.parse_args(sys.argv[2:])
            update_options.validate_auth_flags(args)
        else:
            parser = argparse.ArgumentParser(allow_abbrev=False)
            command = parser.add_subparsers().add_parser('update', allow_abbrev=False)
            command.add_argument('--no-manager', action='store_true')
            command.add_argument('--force', action='store_true')
            args = parser.parse_args(sys.argv[2:])
        print('PARSED_WITHOUT_INSTALLATION')
    """)
    result = subprocess.run(
        [sys.executable, "-c", program, provider, "update", "--no-manager", option,
         *([raw] if option != "--force" else [])],
        capture_output=True, text=True, timeout=30,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert ("PARSED_WITHOUT_INSTALLATION" in result.stdout) == (code == 0)
    if code:
        assert "unrecognized arguments" in result.stderr
