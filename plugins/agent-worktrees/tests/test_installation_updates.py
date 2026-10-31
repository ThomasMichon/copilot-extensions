"""One-plugin update admission, exact effects and read-only observed inventory."""

import argparse
import contextlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import installation_updates as owner
from agent_worktrees import reconcile, update_cli, update_options, update_runtime, update_runtime_plan

PLAN_REGISTERED = update_runtime_plan.plan_registered
DEPLOYED_VERSION = reconcile.runtime_deployed_version


@pytest.fixture
def installed(tmp_path, monkeypatch):
    payloads = {}
    for name, scope in (("agent-example", "universal"), ("guide", "none"), ("agent-other", "universal")):
        path = tmp_path / name
        path.mkdir()
        (path / "plugin.json").write_text(json.dumps({"name": name, "version": "2", "runtimeScope": scope}))
        payloads[name] = path
    target = update_runtime._RegisteredPluginTarget(tmp_path, update_runtime._PluginActivation.ACTIVE)
    monkeypatch.setattr(update_runtime, "_registered_plugin_targets", lambda: {name: target for name in payloads})
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", payloads.get)
    monkeypatch.setattr(reconcile, "discover_core_payload_dirs", lambda: list(payloads.items()))
    monkeypatch.setattr(reconcile, "resolve_runtime_installation", lambda name, path: SimpleNamespace(
        runtime_root=tmp_path / "selected" / name, actual_mode="legacy", reason="fixture",
    ))
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1")
    monkeypatch.setattr(reconcile, "runtime_running_version", lambda *a, **k: "0")
    monkeypatch.setattr(owner.config, "detect_platform", lambda: "linux")
    monkeypatch.setattr(update_runtime, "_refresh_marketplace", lambda *a, **k: True)
    monkeypatch.setattr(update_runtime_plan, "plan_registered", lambda name, *a, **k:
        update_runtime_plan.RuntimeUpdateStep(name, "runtime_reconcile", payloads[name], eligible=True))
    return payloads


def test_inventory_is_read_only_and_does_not_confuse_versions(installed, monkeypatch):
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: pytest.fail("browse acquired a payload"))
    before = {p: p.read_bytes() for root in installed.values() for p in root.rglob("*") if p.is_file()}
    facts = owner.inventory()
    assert facts["runtime_sources"] == {
        "schema": "copilot-extensions.prepared-runtime-sources", "version": 1,
        "target_contract": {"schema": "copilot-extensions.runtime-source-target", "version": 1},
    }
    rows = {row["id"]: row for row in facts["entries"]}
    assert rows["agent-example"]["payload"] == "2"
    assert rows["agent-example"]["selected"] == "1"
    assert rows["agent-example"]["running"] == "0"
    assert rows["guide"]["selected"] == "not applicable"
    assert rows["guide"]["updatable"]
    assert not rows["agent-worktrees"]["installed"]
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("name", ["guide", "agent-example"])
def test_target_acquires_and_executes_only_selected_owner(installed, monkeypatch, name):
    calls = []
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda target, market, **k: calls.append(("payload", target)) or "OK")
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda step: calls.append(("runtime", step.name)) or "OK")
    monkeypatch.setattr(update_runtime, "_fast_forward_project_anchors", lambda: pytest.fail("target updated anchors"))
    monkeypatch.setattr(update_cli, "_prune_stale_pivots_after_update", lambda: pytest.fail("target pruned unrelated pivots"))
    before = (installed["agent-other"] / "plugin.json").read_bytes()
    assert update_cli._update_in_plugin_steps(argparse.Namespace(target=name)) == 0
    assert calls == [("payload", name), ("runtime", name)]
    assert (installed["agent-other"] / "plugin.json").read_bytes() == before


def test_failed_acquisition_stops_runtime_and_retry_reuses_target(installed, monkeypatch):
    calls = []
    results = iter(["authentication denied", "OK"])
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda target, market, **k: next(results))
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda step: calls.append(step.name) or "OK")
    assert owner.update_target("agent-example") == 1
    assert calls == []
    assert owner.update_target("agent-example") == 0
    assert calls == ["agent-example"]


def test_policy_block_is_visible_and_refuses_before_acquisition(installed, monkeypatch):
    monkeypatch.setattr(update_runtime_plan, "plan_registered", lambda name, *a, **k:
        update_runtime_plan.RuntimeUpdateStep(name, "runtime_reconcile", installed[name], status="SKIPPED (machine policy)"))
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: pytest.fail("blocked policy acquired"))
    rows = {row["id"]: row for row in owner.inventory()["entries"]}
    assert not rows["agent-example"]["updatable"]
    assert "machine policy" in rows["agent-example"]["limitation"]
    assert owner.update_target("agent-example") == 1


@pytest.mark.parametrize("name", ["unknown", "../other", "agent-example/other"])
def test_unknown_or_unsafe_target_never_becomes_suite_update(installed, monkeypatch, name):
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: pytest.fail("invalid target acquired"))
    assert owner.update_target(name) == 1


def test_old_engine_installer_cannot_recover_manager_as_target_effect(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "plugin.json").write_text('{"runtimeScope":"universal"}')
    for ext in ("sh", "ps1"):
        (tmp_path / "scripts" / f"install.{ext}").write_text("old installer")
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    monkeypatch.setattr(update_runtime_plan, "plan_engine", lambda *a, **k:
        update_runtime_plan.RuntimeUpdateStep("agent-worktrees", "engine_runtime", tmp_path))
    with pytest.raises(ValueError, match="lacks targeted"):
        owner.target_plan("agent-worktrees", None)
    for ext in ("sh", "ps1"):
        (tmp_path / "scripts" / f"install.{ext}").write_text("AGENT_WORKTREES_TARGETED_UPDATE")
    assert owner.target_plan("agent-worktrees", None).environment["AGENT_WORKTREES_TARGETED_UPDATE"] == "1"


@pytest.mark.parametrize("prepared_supported", [False, True])
def test_isolated_engine_checks_execution_artifact_not_authority(tmp_path, prepared_supported):
    authority, prepared = tmp_path / "authority", tmp_path / "prepared"
    for payload, supported in ((authority, not prepared_supported), (prepared, prepared_supported)):
        (payload / "scripts").mkdir(parents=True)
        for extension in ("sh", "ps1"):
            (payload / "scripts" / f"install.{extension}").write_text(
                "AGENT_WORKTREES_TARGETED_UPDATE" if supported else "old installer",
            )
    step = update_runtime_plan.RuntimeUpdateStep(
        "agent-worktrees", "engine_runtime", authority,
        source=SimpleNamespace(payload=prepared),
    )
    if prepared_supported:
        assert owner.isolated_engine_step(step).environment["AGENT_WORKTREES_TARGETED_UPDATE"] == "1"
    else:
        with pytest.raises(ValueError, match="lacks targeted"):
            owner.isolated_engine_step(step)


@pytest.mark.skipif(os.name == "nt", reason="native POSIX installer function")
@pytest.mark.parametrize("targeted", [False, True])
def test_posix_engine_installer_recovery_matches_selected_scope(tmp_path, targeted):
    from agent_procutil import no_window_kwargs

    payload = Path(__file__).resolve().parents[1]
    text = (payload / "scripts" / "install.sh").read_text()
    function = "_deploy_python_runtime() {" + text.split(
        "_deploy_python_runtime() {", 1,
    )[1].split("\n}\n", 1)[0] + "\n}\n"
    (tmp_path / "runtime_install.py").write_text(
        "import json, os, pathlib, sys\n"
        "assert ('--recover-adopted-manager' in sys.argv) == "
        "(os.environ.get('AGENT_WORKTREES_TARGETED_UPDATE') != '1')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--result') + 1]).write_text(json.dumps({"
        "'runtime_version': 'fixture', 'runtime_python': sys.executable, "
        "'deployment_manifest_published': True, 'helpers_published': True}))\n",
    )
    script = function + r"""
set -eu
ACTION=update
OWNER_ARG=
CONTEXTUAL_INSTALL=false
PLUGIN_DIR="$FIXTURE_ROOT"
SCRIPT_DIR="$FIXTURE_ROOT"
INSTALL_DIR="$FIXTURE_ROOT/root"
VENV_DIR="$INSTALL_DIR/versions/fixture"
_bootstrap_python() { printf '%s\n' "$FIXTURE_PYTHON"; }
_ensure_uv() { :; }
_ensure_uv_index() { :; }
_assert_source_runtime() { :; }
ok() { :; }
err() { printf '%s\n' "$*" >&2; }
_deploy_python_runtime
[[ "$PYTHON_RUNTIME_MANIFEST_PUBLISHED" == 1 ]]
"""
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=20,
        env={
            **os.environ, "FIXTURE_ROOT": str(tmp_path), "FIXTURE_PYTHON": sys.executable,
            "AGENT_WORKTREES_TARGETED_UPDATE": "1" if targeted else "",
        },
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_installer_branches_preserve_unrelated_finishing_effects():
    root = Path(__file__).resolve().parents[1] / "scripts"
    for ext, start, finish in (
        ("sh", 'if [[ "${AGENT_WORKTREES_TARGETED_UPDATE', "\n        fi"),
        ("ps1", "if ($env:AGENT_WORKTREES_TARGETED_UPDATE", "\n        }"),
    ):
        text = (root / f"install.{ext}").read_text()
        assert "TARGETED_UPDATE" in text
        assert "--recover-adopted-manager" in text
        branch = text[text.index(start):]
        branch = branch[:branch.index(finish)]
        assert "exit 0" in branch
        assert "reconcile_binstubs" not in branch and "Reconcile-Binstubs" not in branch


def test_target_flags_survive_driver_forwarding_and_reject_force():
    args = argparse.Namespace(target="guide")
    assert update_options.forwarded_flags(args) == ["--target", "guide"]
    with pytest.raises(SystemExit):
        update_options.validate_auth_flags(argparse.Namespace(target="guide", force=True))
    with pytest.raises(SystemExit):
        update_options.validate_auth_flags(argparse.Namespace(target="guide", skip_modules=[]))


def test_disabled_payload_only_plugin_update_does_not_require_enabling(installed, monkeypatch):
    inactive = update_runtime._RegisteredPluginTarget(None, update_runtime._PluginActivation.INACTIVE)
    monkeypatch.setattr(update_runtime, "_registered_plugin_targets", lambda: {"guide": inactive})
    monkeypatch.setattr(update_runtime_plan, "plan_registered", lambda name, *a, **k:
        update_runtime_plan.RuntimeUpdateStep(name, "runtime_reconcile", installed[name], status="payload-only"))
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: "OK")
    rows = {row["id"]: row for row in owner.inventory()["entries"]}
    assert rows["guide"]["enabled"] == "inactive" and rows["guide"]["updatable"]
    assert owner.update_target("guide") == 0


def test_real_update_parser_routes_inventory_without_execution(monkeypatch):
    from agent_worktrees import update_driver_cli

    parser = argparse.ArgumentParser()
    update_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["update", "--inventory", "--json"])
    calls = []
    monkeypatch.setattr(owner, "print_inventory", lambda: calls.append("read") or 0)
    assert update_driver_cli.cmd_update_request(args) == 0
    assert calls == ["read"]
    with pytest.raises(SystemExit):
        update_options.validate_auth_flags(parser.parse_args(["update", "--inventory", "--target", "guide"]))
    with pytest.raises(SystemExit):
        parser.parse_args(["update", "--target-plugin-typo", "guide"])


@pytest.mark.parametrize("target", ["", " ", "../plugin"])
def test_empty_or_invalid_target_is_never_dropped_into_suite(installed, monkeypatch, target):
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: pytest.fail("invalid target acquired"))
    args = argparse.Namespace(target=target)
    with pytest.raises(SystemExit):
        update_options.validate_auth_flags(args)
    assert update_options.forwarded_flags(args) == ["--target", target]
    assert update_cli._update_in_plugin_steps(args) == 1


def test_inventory_warnings_stay_on_stderr_not_in_json(installed, monkeypatch, capsys):
    original = update_runtime._registered_plugin_targets

    def warned():
        print("Warning: activation metadata unavailable")
        return original()

    monkeypatch.setattr(update_runtime, "_registered_plugin_targets", warned)
    assert owner.print_inventory() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["contract"] == 1
    assert "activation metadata" in captured.err


def test_payload_only_scope_change_cannot_install_disabled_runtime(installed, monkeypatch):
    inactive = update_runtime._RegisteredPluginTarget(None, update_runtime._PluginActivation.INACTIVE)
    monkeypatch.setattr(update_runtime, "_registered_plugin_targets", lambda: {"guide": inactive})
    monkeypatch.setattr(update_runtime_plan, "plan_registered", lambda name, *a, **k:
        update_runtime_plan.RuntimeUpdateStep(name, "runtime_reconcile", installed[name], status="payload-only"))

    def acquire(*args, **kwargs):
        (installed["guide"] / "plugin.json").write_text('{"name":"guide","version":"3","runtimeScope":"universal"}')
        return "OK"

    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", acquire)
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda *a: pytest.fail("disabled runtime installed"))
    assert owner.update_target("guide") == 1


def test_target_executes_under_engine_custody_without_legacy_manager_handoff(monkeypatch):
    from agent_worktrees import __main__ as core

    monkeypatch.setattr(update_cli, "_usable_worktree_manager", lambda: pytest.fail("target probed legacy Manager"))
    calls = []
    monkeypatch.setattr(core, "_cmd_update_in_plugin", lambda args: calls.append(args.target) or 0)
    assert update_cli.cmd_update(argparse.Namespace(target="guide")) == 0
    assert calls == ["guide"]


def test_target_authentication_prepares_only_its_owning_context(monkeypatch):
    from agent_worktrees import update_auth_context as auth

    prepared = []
    value = SimpleNamespace(
        prepare=lambda context, **kwargs: prepared.append(context),
        anchor_failed=False, unresolved=False,
    )

    @contextlib.contextmanager
    def session():
        yield value

    monkeypatch.setattr(auth, "session", session)
    monkeypatch.setattr(auth, "_marketplace_context", lambda: "unrelated-default")

    def steps(args):
        value.prepare("selected-target")
        return 0

    assert auth.run_update(argparse.Namespace(target="guide"), steps) == 0
    assert prepared == ["selected-target"]


def test_engine_target_preserves_no_install_fallback_acquisition_policy(installed, monkeypatch, tmp_path):
    import subprocess
    from agent_worktrees import update_acquisition, update_auth_context

    payload = tmp_path / "agent-worktrees"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-worktrees","version":"2","runtimeScope":"universal"}')
    installed["agent-worktrees"] = payload
    monkeypatch.setattr(update_runtime, "_invocation_update_context", lambda: tmp_path)
    monkeypatch.setattr(owner, "target_plan", lambda name, target:
        update_runtime_plan.RuntimeUpdateStep(name, "engine_runtime", payload, eligible=True))
    monkeypatch.setattr(reconcile, "resolve_copilot", lambda: "synthetic-copilot")
    monkeypatch.setattr(update_auth_context, "blocked", lambda *a: False)
    monkeypatch.setattr(update_auth_context, "child_environment", lambda *a: {})
    monkeypatch.setattr(update_auth_context, "run_acquisition", lambda run, argv, **kwargs:
        run(argv, **{key: value for key, value in kwargs.items() if key != "context"}))
    monkeypatch.setattr(update_acquisition, "_WINDOWS", False)
    calls = []

    def fail(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "plugin agent-worktrees is not installed")

    monkeypatch.setattr(update_acquisition, "_run_update", fail)
    monkeypatch.setattr(update_acquisition, "_run_install", lambda *a, **k: pytest.fail("engine acquired via install fallback"))
    assert owner.update_target("agent-worktrees") == 1
    assert calls == [["synthetic-copilot", "plugin", "update", "agent-worktrees@copilot-extensions"]]


@pytest.mark.parametrize("name", ["efforts", "visions", "customizing-copilot"])
@pytest.mark.parametrize("enabled", [True, False])
def test_shipped_payload_only_manifest_without_scope_remains_updatable(
    installed, monkeypatch, tmp_path, name, enabled,
):
    payload = tmp_path / name
    payload.mkdir()
    shipped = Path(__file__).resolve().parents[2] / name / "plugin.json"
    data = json.loads(shipped.read_text())
    assert "runtimeScope" not in data
    (payload / "plugin.json").write_text(json.dumps(data))
    installed[name] = payload
    activation = update_runtime._PluginActivation
    target = update_runtime._RegisteredPluginTarget(
        tmp_path, activation.ACTIVE if enabled else activation.INACTIVE,
    )
    monkeypatch.setattr(update_runtime, "_registered_plugin_targets", lambda: {name: target})
    monkeypatch.setattr(update_runtime_plan, "plan_registered", PLAN_REGISTERED)
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: "OK")
    monkeypatch.setattr(update_runtime, "_run_human_installer", lambda *a, **k: pytest.fail("payload-only runtime installed"))
    rows = {row["id"]: row for row in owner.inventory()["entries"]}
    assert rows[name]["kind"] == "none" and rows[name]["updatable"]
    assert rows[name]["selected"] == rows[name]["running"] == "not applicable"
    assert owner.update_target(name) == 0


@pytest.mark.parametrize("scope", [None, 42, "invalid", {}])
def test_malformed_explicit_runtime_scope_is_not_payload_only(installed, monkeypatch, scope):
    (installed["guide"] / "plugin.json").write_text(json.dumps({
        "name": "guide", "version": "2", "runtimeScope": scope,
    }))
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: pytest.fail("invalid scope acquired"))
    rows = {row["id"]: row for row in owner.inventory()["entries"]}
    assert not rows["guide"]["updatable"]
    assert "Invalid runtimeScope" in rows["guide"]["limitation"]
    assert rows["guide"]["payload"] == "2"
    assert owner.update_target("guide") == 1


def test_acquired_runtime_policy_block_is_failure_not_success(installed, monkeypatch):
    plans = iter([
        update_runtime_plan.RuntimeUpdateStep("guide", "runtime_reconcile", installed["guide"], status="payload-only"),
        update_runtime_plan.RuntimeUpdateStep("guide", "runtime_reconcile", installed["guide"], status="SKIPPED (machine policy)"),
    ])
    monkeypatch.setattr(owner, "target_plan", lambda *a: next(plans))

    def acquire(*args, **kwargs):
        (installed["guide"] / "plugin.json").write_text('{"name":"guide","version":"3","runtimeScope":"machine-gated"}')
        return "OK"

    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", acquire)
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda *a: pytest.fail("policy-blocked runtime executed"))
    assert owner.update_target("guide") == 1


@pytest.mark.parametrize("document", ["[]", "null", '"not-an-object"', "{invalid"])
def test_malformed_manifest_cannot_hide_valid_peer_inventory(installed, monkeypatch, document):
    (installed["guide"] / "plugin.json").write_text(document)
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda *a, **k: pytest.fail("malformed payload acquired"))
    rows = {row["id"]: row for row in owner.inventory()["entries"]}
    assert rows["agent-example"]["payload"] == "2"
    assert rows["guide"]["installed"] and rows["guide"]["payload"] == "unknown"
    assert not rows["guide"]["updatable"] and rows["guide"]["limitation"]
    assert owner.update_target("guide") == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX actual-installer fixture; native Windows remains separate")
def test_real_selected_installer_publishes_version_without_unrelated_effects(installed, monkeypatch, tmp_path):
    name = "agent-example"
    selected = tmp_path / "selected-runtime"
    selected.mkdir()
    manifest = selected / "deploy-manifest.json"
    manifest.write_text('{"source":{"version":"1"}}')
    script = installed[name] / "scripts" / "install.sh"
    script.parent.mkdir()
    code = "import pathlib; pathlib.Path(" + repr(str(manifest)) + ").write_text('{\"source\":{\"version\":\"2\"}}')"
    script.write_text("set -e\n[ \"$1\" = update ]\n" + shlex.quote(sys.executable) + " -c " + shlex.quote(code) + "\n")
    monkeypatch.setattr(update_runtime_plan, "plan_registered", PLAN_REGISTERED)
    monkeypatch.setattr(reconcile, "runtime_deployed_version", DEPLOYED_VERSION)
    monkeypatch.setattr(reconcile, "runtime_installer_environment", lambda *a: (dict(os.environ), selected))
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *a: False)
    monkeypatch.setattr(update_runtime, "_installer_timeout", lambda *a: 30)
    acquisition = []
    monkeypatch.setattr(update_runtime, "_update_one_plugin_payload", lambda target, *a, **k: acquisition.append(target) or "OK")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert owner.update_target(name) == 0
    assert acquisition == [name]
    assert DEPLOYED_VERSION(name, root=selected) == "2"
    assert {p: p.read_bytes() for p in before if p != manifest} == {p: b for p, b in before.items() if p != manifest}
