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


def native_packet(tmp_path: Path, names=("agent-worktrees",)) -> dict:
    data = packet(tmp_path, names)
    for name, record in data["sources"].items():
        snapshot = Path(record["snapshot"])
        project = json.loads((snapshot / "install-source.json").read_text())["projects"][name]
        data["sources"][name] = {
            "payload": str(snapshot / project["payload"]),
            "identity": {
                "runtime_source": project["source"],
                "runtime_version": project["source"]["runtime_version"],
            },
        }
    del data["source"]
    data["version"] = 1
    return data


@pytest.mark.parametrize("version", [1, 2])
def test_native_packet_preserves_each_payload_and_full_selection(tmp_path, version):
    data = native_packet(tmp_path, ("agent-worktrees", "agent-example"))
    data["version"] = version
    sources = update_runtime_source.parse_sources(json.dumps(data))
    for name, source in sources.items():
        assert source.payload == Path(data["sources"][name]["payload"])
        assert json.loads(source.identity) == data["sources"][name]["identity"]
        assert source.application is None


@pytest.mark.parametrize("kind", ["source", "main"])
def test_packet_preserves_each_payload_and_full_selection(tmp_path, kind):
    data = packet(tmp_path, ("agent-worktrees", "agent-example"), kind=kind)
    selected = update_runtime_source.parse_sources(json.dumps(data))
    for name, source in selected.items():
        assert source.payload == tmp_path / name / "payload"
        assert source.application.snapshot == tmp_path / name
        assert json.loads(source.identity)["runtime_source"]["plugin"] == name
    assert update_runtime_source.parse_sources(None) is None


def test_v2_packet_preserves_native_profile_and_legacy_in_one_selection(tmp_path):
    data = native_packet(tmp_path, ("agent-worktrees", "future-app", "legacy-app"))
    data["version"] = 2
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    record = data["sources"]["future-app"]
    record.update(install_source=str(snapshot), native={
        "identity": record["identity"],
        "runtime_version": record["identity"]["runtime_version"] + ".profile." + "d" * 16,
        "payload_hash": "e" * 64,
    })
    sources = update_runtime_source.parse_sources(json.dumps(data))
    assert sources["future-app"].version == record["native"]["runtime_version"]
    assert sources["future-app"].install_source == snapshot
    assert sources["legacy-app"].install_source is None
    data["version"] = 1
    with pytest.raises(ValueError):
        update_runtime_source.parse_sources(json.dumps(data))


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_released_native_inputs_are_recognized_without_a_new_sidecar(tmp_path, platform):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "runtime_install.py").write_text(
        "parser.add_argument('--install-source')\nfrom install_source import project_inputs\n",
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        "param([string]$InstallSource)\n# native forwarding: --install-source\n", encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        "case $1 in --install-source) ;; esac\n", encoding="utf-8",
    )
    actual = update_runtime_source.source_install_entry(tmp_path, platform)
    assert actual == {
        "script": "scripts/install.ps1" if platform == "windows" else "scripts/install.sh",
        "argument": "-InstallSource" if platform == "windows" else "--install-source",
    }


def test_internal_input_flag_alone_does_not_replace_a_legacy_public_flow(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "runtime_install.py").write_text(
        "parser.add_argument('--install-source')\nfrom install_source import project_inputs\n",
        encoding="utf-8",
    )
    assert update_runtime_source.source_install_entry(tmp_path, "windows") is None


def test_declared_native_inputs_with_missing_handoff_do_not_become_legacy(tmp_path):
    (tmp_path / "source-install.json").write_text(json.dumps({
        "schema": "copilot-extensions.native-source-install", "version": 1,
        "windows": {"script": "scripts/install.ps1", "argument": "-InstallSource"},
        "posix": {"script": "scripts/install.sh", "argument": "--install-source"},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="unavailable"):
        update_runtime_source.source_install_entry(tmp_path, "windows")


@pytest.mark.parametrize("fault", ["relative", "mismatched-identity", "base-version", "missing-native"])
def test_invalid_native_profile_never_becomes_legacy(tmp_path, fault):
    data = native_packet(tmp_path)
    data["version"] = 2
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    record = data["sources"]["agent-worktrees"]
    record.update(install_source=str(snapshot), native={
        "identity": record["identity"],
        "runtime_version": record["identity"]["runtime_version"] + ".profile." + "d" * 16,
        "payload_hash": "e" * 64,
    })
    if fault == "relative":
        record["install_source"] = "relative"
    elif fault == "mismatched-identity":
        record["native"]["identity"] = {}
    elif fault == "base-version":
        record["native"]["runtime_version"] = record["identity"]["runtime_version"]
    else:
        del record["native"]
    with pytest.raises(ValueError):
        update_runtime_source.parse_sources(json.dumps(data))


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


@pytest.mark.parametrize("change", [
    "schema", "version", "boolean-version", "unknown", "empty", "list",
    "unsafe-name", "record", "extra-record", "relative", "missing", "identity",
    "wrong-plugin", "different-commit", "different-tree", "short-commit",
])
def test_invalid_native_packet_has_no_package_only_fallback(tmp_path, change):
    data = native_packet(tmp_path, ("agent-worktrees", "agent-example"))
    record = data["sources"]["agent-example"]
    identity = record["identity"]["runtime_source"]
    if change == "schema":
        data["schema"] = "other"
    elif change in {"version", "boolean-version"}:
        data["version"] = True if change == "boolean-version" else 3
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


@pytest.mark.parametrize("application", [False, True])
def test_packet_formats_cannot_be_mixed_or_inferred_after_a_missing_field(tmp_path, application):
    data = packet(tmp_path) if application else native_packet(tmp_path)
    if application:
        del data["source"]
    else:
        data["version"] = 2
        data["source"] = {"commit": "a" * 40, "tree": "b" * 40, "main_seed": "f" * 40}
    with pytest.raises(ValueError, match="entry"):
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
