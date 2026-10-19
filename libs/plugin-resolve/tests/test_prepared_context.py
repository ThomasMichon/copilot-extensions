"""The registration lock supplies effective native identity, not input guesses."""

import sys
import json
import os
import subprocess
from pathlib import Path

import pytest

from plugin_resolve.preparation import prepare
from plugin_resolve.reflection import build_plan, build_prepare_script, parse_prepared, parse_prepared_context
from plugin_resolve.registration import atomic_json
from test_plugin_preparation import init_checkout, local_plan
from test_source_preserving_preparation import git_origin as git_origin_fixture

git_origin = git_origin_fixture


def test_context_tracks_native_override_disable_and_no_skill_manifest(tmp_path):
    source, native, target = (tmp_path / name for name in ("source", "native", "target"))
    local_plan(source)
    plan = build_plan([{"source": "sample@local"}], [source], local_mode="reference")
    local_plan(native)
    init_checkout(target)
    settings_path = target / ".github" / "copilot" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    definition = {"source": {"source": "directory", "path": str(native / ".ai")}}
    atomic_json(settings_path, {
        "extraKnownMarketplaces": {"local": definition}, "enabledPlugins": {"sample@local": True},
    })
    atomic_json(native / ".ai" / "sample" / "plugin.json", {"name": "sample", "version": "7.0.0"})
    context = {"stale": True}
    result = prepare({"repo": str(target), **plan, "local_mode": "reference"}, prepared_context=context)
    assert result == {"sample@local": str(native / ".ai" / "sample")}
    assert context == {
        "cwd": str(target), "logical_payloads": result,
        "plugins": [{"projected_source": "sample@local", "logical_source": "sample@local",
                     "version": "7.0.0", "live_marketplace_root": str(native / ".ai")}],
    }
    atomic_json(settings_path, {
        "extraKnownMarketplaces": {"local": definition}, "enabledPlugins": {"sample@local": False},
    })
    assert prepare({"repo": str(target), **plan, "local_mode": "reference"}, prepared_context=context) == {}
    assert context["plugins"] == [] and context["logical_payloads"] == {}
    assert not list(native.rglob("skills"))


def test_context_uses_projected_identity_for_mixed_local_alias(tmp_path):
    source, other, target = (tmp_path / name for name in ("source", "other", "target"))
    local_plan(source)
    local_plan(other, name="second")
    init_checkout(target)
    plan = build_plan([{"source": "sample@local"}], [source], local_mode="reference")
    other_plan = build_plan([{"source": "second@local"}], [other], local_mode="reference")
    plan["plugins"].extend(other_plan["plugins"])
    context = {}
    result = prepare({"repo": str(target), **plan, "local_mode": "reference"}, prepared_context=context)
    items = {item["logical_source"]: item for item in context["plugins"]}
    assert set(items) == set(result) == {"sample@local", "second@local"}
    assert items["sample@local"]["projected_source"] == "sample@local"
    assert items["second@local"]["projected_source"].startswith("second@local-venue-")
    assert items["second@local"]["live_marketplace_root"] == str(other / ".ai")


def test_git_context_omits_private_acquisition_root(tmp_path, git_origin):
    origin, _first, _second = git_origin
    target = tmp_path / "target"
    init_checkout(target)
    definition = {"source": {"source": "git", "url": origin.as_uri(), "ref": "first", "path": "catalog-a"}}
    plan = build_plan([{"source": "helper@git-fixture"}], [], marketplaces={"git-fixture": definition})
    context = {}
    result = prepare(
        {"repo": str(target), **plan},
        copilot_command=(sys.executable, str(Path(__file__).parent / "fixtures" / "structured_git_installer.py")),
        prepared_context=context,
    )
    assert context["plugins"] == [{
        "projected_source": "helper@git-fixture", "logical_source": "helper@git-fixture",
        "version": "1.0.0",
    }]
    assert context["logical_payloads"] == result


def test_failed_registration_does_not_publish_success_metadata(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    local_plan(source)
    plan = build_plan([{"source": "sample@local"}], [source], local_mode="reference")
    init_checkout(target)
    (source / ".ai" / "sample" / "plugin.json").unlink()
    context = {}
    with pytest.raises(ValueError):
        prepare({"repo": str(target), **plan}, prepared_context=context)
    assert context == {}


def test_context_marker_is_opt_in_and_preserves_directory_parser(tmp_path):
    context = {"version": 1, "cwd": "/callee", "plugins": [{
        "projected_source": "helper@alias", "logical_source": "helper@original",
    }], "logical_payloads": {"helper@original": "/controller/.ai/helper"}}
    output = "__VENUE_PLUGINS__" + json.dumps(context["logical_payloads"]) + "\n"
    output += "__VENUE_PLUGIN_CONTEXT__" + json.dumps(context) + "\n"
    assert parse_prepared(output) == ["/controller/.ai/helper"]
    assert parse_prepared_context(output) == context
    assert "__VENUE_PLUGIN_CONTEXT__" not in build_prepare_script({}, "/callee")
    assert "__VENUE_PLUGIN_CONTEXT__" in build_prepare_script({}, "/callee", include_prepared_context=True)
    with pytest.raises(ValueError, match="no compiled context"):
        parse_prepared_context("__VENUE_PLUGINS__{}")


def test_legacy_installed_only_availability_is_not_native_cli_origin_proof(tmp_path):
    target, home = tmp_path / "target", tmp_path / "home"
    init_checkout(target)
    payload = home / "installed-plugins" / "legacy" / "helper"
    payload.mkdir(parents=True)
    atomic_json(payload / "plugin.json", {"name": "helper", "version": "1.0.0"})
    context = {}
    plan = build_plan([{"source": "helper@legacy"}], [])
    result = prepare(
        {"repo": str(target), **plan, "local_mode": "reference"}, environment={"COPILOT_HOME": str(home)},
        prepared_context=context,
    )
    assert result == {"helper@legacy": str(payload)}
    assert context["acp_only_sources"] == ["helper@legacy"]


def test_context_transport_executes_with_only_stdlib_and_actual_receiver_checkout(tmp_path):
    target, home = tmp_path / "target", tmp_path / "home"
    init_checkout(target)
    local_plan(target)
    plan = build_plan([{"source": "sample@local"}], [target], local_mode="reference")
    plan["repo"] = str(tmp_path / "not-the-allocated-checkout")
    script = build_prepare_script(plan, str(target), include_prepared_context=True)
    code = script.split("<<'VENUE_PLUGIN_PREPARE'\n", 1)[1].rsplit("VENUE_PLUGIN_PREPARE", 1)[0]
    environment = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "COPILOT_HOME": str(home / ".copilot")}
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-"], input=code, cwd=target, env=environment,
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    context = parse_prepared_context(result.stdout)
    assert context["cwd"] == str(target)
    assert context["logical_payloads"] == {"sample@local": str(target / ".ai" / "sample")}
    assert parse_prepared(result.stdout) == [str(target / ".ai" / "sample")]


def test_context_uses_final_complete_frame_not_earlier_preparation():
    old = {"version": 1, "cwd": "/old", "plugins": [{"projected_source": "old@local"}],
           "logical_payloads": {"old@local": "/old/plugin"}}
    current = {"version": 1, "cwd": "/actual", "plugins": [
        {"projected_source": "new@local"}, {"projected_source": "other@local"},
    ], "logical_payloads": {"new@local": "/actual/new", "other@local": "/actual/other"}}
    def frame(value):
        return ("__VENUE_PLUGINS__" + json.dumps(value["logical_payloads"]) + "\n"
                "__VENUE_PLUGIN_CONTEXT__" + json.dumps(value) + "\n")
    output = frame(old) + frame(current)
    assert parse_prepared(output) == ["/actual/new", "/actual/other"]
    assert parse_prepared_context(output, expected_repo="/actual") == current
    with pytest.raises(ValueError, match="final directory frame"):
        parse_prepared_context(frame(old) + "__VENUE_PLUGINS__" + json.dumps(current["logical_payloads"]))
    for malformed in ({**current, "version": True}, {**current, "logical_payloads": old["logical_payloads"]}):
        bad = frame(old) + "__VENUE_PLUGINS__" + json.dumps(current["logical_payloads"]) + "\n"
        bad += "__VENUE_PLUGIN_CONTEXT__" + json.dumps(malformed)
        with pytest.raises(ValueError):
            parse_prepared_context(bad, expected_repo="/actual")
    with pytest.raises(ValueError, match="execution checkout"):
        parse_prepared_context(output, expected_repo="/different")
