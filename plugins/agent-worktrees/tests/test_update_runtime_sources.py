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


def packet(tmp_path: Path, names=("agent-worktrees",), *, kind="source") -> dict:
    sources = {}
    for name in names:
        snapshot = tmp_path / name
        payload = snapshot / "payload"
        payload.mkdir(parents=True, exist_ok=True)
        identity = {
            "schema": "copilot-extensions.runtime-source", "version": 1,
            "plugin": name, "package_version": "1.0.0", "source_commit": "a" * 40,
            "source_tree": "b" * 40, "assembly_hash": "c" * 64,
            "runtime_version": f"1.0.0+source.{'a' * 40}.{'c' * 16}",
        }
        application = snapshot / "application"
        application.mkdir(exist_ok=True)
        (application / "application.json").write_text(json.dumps({
            "name": name, "module": name.replace("-", "_"),
            "construction": {
                "identity": {"runtime_source": identity, "runtime_version": identity["runtime_version"]},
                "runtime_version": identity["runtime_version"] + ".profile." + "e" * 16,
                "payload_hash": "d" * 64, "extras": [], "local_packages": [name],
            },
        }))
        (snapshot / "install-source.json").write_text(json.dumps({
            "schema": "copilot-extensions.install-source", "version": 1, "kind": kind,
            "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "f" * 40,
            **({"composition": {"main_seed": "f" * 40, "tree": "a" * 40}}
               if kind == "source" else {}),
            "projects": {name: {
                "payload": "payload", "source": identity, "profile": "profile.json",
                "wheel_inputs": "wheels", "artifact": {}, "application": "application",
            }},
        }))
        sources[name] = {"snapshot": str(snapshot), "profile": None}
    return {
        "schema": SCHEMA, "version": 2,
        "source": {"commit": "a" * 40, "tree": "b" * 40, "main_seed": "f" * 40},
        "sources": sources,
    }


def parser():
    result = argparse.ArgumentParser()
    update_cli.add_parsers(result.add_subparsers(dest="command"))
    return result


@pytest.mark.parametrize("kind", ["source", "main"])
def test_packet_preserves_each_payload_and_full_selection(tmp_path, kind):
    data = packet(tmp_path, ("agent-worktrees", "agent-example"), kind=kind)
    selected = update_runtime_source.parse_sources(json.dumps(data))
    for name, source in selected.items():
        assert source.payload == tmp_path / name / "payload"
        assert source.application.snapshot == tmp_path / name
        assert json.loads(source.identity)["runtime_source"]["plugin"] == name
    assert update_runtime_source.parse_sources(None) is None


@pytest.mark.parametrize("fault", ["main-seed", "composition"])
def test_finalized_main_is_not_an_unseeded_or_composed_dev_snapshot(tmp_path, fault):
    data = packet(tmp_path, kind="main")
    path = Path(data["sources"]["agent-worktrees"]["snapshot"]) / "install-source.json"
    record = json.loads(path.read_text())
    if fault == "main-seed":
        record.pop("main_head")
    else:
        record["composition"] = {"main_seed": "f" * 40, "tree": "a" * 40}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="complete source snapshot"):
        update_runtime_source.parse_sources(json.dumps(data))


@pytest.mark.parametrize("change", [
    "schema", "version", "boolean-version", "unknown", "empty", "list",
    "unsafe-name", "record", "extra-record", "relative", "missing", "identity",
    "wrong-plugin", "different-commit", "different-tree", "short-commit", "main-seed",
    "composition", "profile-inputs", "application-inputs", "source-selection",
])
def test_invalid_packet_has_no_package_only_fallback(tmp_path, change):
    data = packet(tmp_path, ("agent-worktrees", "agent-example"))
    record = data["sources"]["agent-example"]
    snapshot = Path(record["snapshot"])
    manifest_path = snapshot / "install-source.json"
    manifest = json.loads(manifest_path.read_text())
    identity = manifest["projects"]["agent-example"]["source"]
    if change == "schema":
        data["schema"] = "other"
    elif change in {"version", "boolean-version"}:
        data["version"] = True if change == "boolean-version" else 1
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
        record["snapshot"] = "relative"
    elif change == "missing":
        record["snapshot"] = str(tmp_path / "absent")
    elif change == "identity":
        manifest["projects"]["agent-example"]["source"] = {}
    elif change == "wrong-plugin":
        identity["plugin"] = "agent-worktrees"
    elif change == "different-commit":
        identity["source_commit"] = "d" * 40
    elif change == "different-tree":
        identity["source_tree"] = "d" * 40
    elif change == "main-seed":
        manifest["main_head"] = "d" * 40
        manifest["composition"]["main_seed"] = "d" * 40
    elif change == "composition":
        manifest["composition"]["tree"] = "d" * 40
    elif change in {"profile-inputs", "application-inputs"}:
        manifest["projects"]["agent-example"].pop(
            "profile" if change == "profile-inputs" else "application",
        )
    elif change == "source-selection":
        data["source"]["commit"] = "d" * 40
    else:
        identity["source_commit"] = "abcdef"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((OSError, ValueError)):
        update_runtime_source.parse_sources(json.dumps(data))


def test_duplicate_json_fields_are_not_last_writer_wins(tmp_path):
    value = json.dumps(packet(tmp_path))
    with pytest.raises(ValueError, match="duplicate"):
        update_runtime_source.parse_sources(value.replace('"version": 2', '"version": 2, "version": 2', 1))


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
