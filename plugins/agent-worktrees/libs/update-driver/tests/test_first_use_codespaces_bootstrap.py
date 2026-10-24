"""Actual CodeSpaces bootstrap admission, native caller loss and durable results."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import agent_procutil
import pytest
from packaging.version import Version
from update_driver import foundation_status, status
from update_driver.foundation_inputs import PACKAGES, SUPPORT

from test_first_use import primitive
from test_first_use_codespaces import (
    isolate_disabled_owner, materialized_codespaces as materialized_codespaces,
)
from test_first_use_genesis import observe
from test_first_use_native import native_command
from test_first_use_pull_requests_command import files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt, owned_hook, receipt_completed_count


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux CodeSpaces bootstrap",
)
ACKNOWLEDGEMENT = "[agent-codespaces] driver launch acknowledgement: "


def hook_command(payload, project, native_directory, name="bootstrap-check"):
    hook = payload / "scripts" / (name + (".ps1" if os.name == "nt" else ".sh"))
    if os.name != "nt":
        return ["/bin/bash", str(hook)]
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    return [
        str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
        "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        f"Set-Location -LiteralPath {quote(project)}; "
        f"[IO.Directory]::SetCurrentDirectory({quote(native_directory)}); "
        f"& {quote(hook)}; exit $LASTEXITCODE",
    ]


def test_materialized_codespaces_has_complete_independent_foundation(materialized_codespaces):
    payload, _installation, _project = materialized_codespaces
    source = payload / "libs/update-driver"
    for package in PACKAGES:
        assert (payload / "libs" / package / "pyproject.toml").is_file()
    for support in SUPPORT:
        assert (source / "scripts" / support).is_file()
    assert (source / "src/update_driver/first_use.py").is_file()
    assert (source / "scripts/first-use.ps1").is_file()
    assert (source / "scripts/first-use.sh").is_file()
    assert '../../libs/' not in (payload / "pyproject.toml").read_text()


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("reason", [
    "managed", "malformed", "no-self", "pinned", "nested", "cell",
    "missing-driver", "noninstalled", "declaration",
])
def test_actual_codespaces_bootstrap_refuses_before_writes(
    materialized_codespaces, monkeypatch, area, phase, reason,
):
    payload, installation, project = materialized_codespaces
    if reason == "noninstalled":
        copied = area / "copy/agent-codespaces"
        shutil.copytree(payload, copied)
        payload = copied
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(payload)},
        }), encoding="utf-8")
        (installation / "reconcile.lock").write_text("424242", encoding="utf-8")
    if reason in {"managed", "malformed"}:
        api = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-codespaces",
            legacy_root=installation, project_root=project,
        )
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        policy = {
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_CODESPACES_NO_SELFPROVISION",
            "pinned": "AGENT_CODESPACES_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_CODESPACES_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason == "missing-driver":
        (payload / "libs/update-driver/scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )).unlink()
    elif reason == "declaration":
        declaration = payload / "payload-invocation.json"
        manifest = json.loads(declaration.read_text())
        manifest["provisionMode"] = "direct"
        declaration.write_text(json.dumps(manifest), encoding="utf-8")
    before, source = files(installation), files(payload)
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == (0 if reason in {"no-self", "pinned"} else 3), (
        result.stdout + result.stderr
    )
    assert result.stdout == "{}" and result.stderr
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()
    assert files(installation) == before and files(payload) == source


@pytest.mark.parametrize("reason", ["namespaced", "killswitch"])
def test_actual_codespaces_bootstrap_retains_nonpublication_noops(
    materialized_codespaces, monkeypatch, reason,
):
    payload, installation, project = materialized_codespaces
    if reason == "namespaced":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{}")
    else:
        guard = payload / "scripts" / (
            "bootstrap-killswitch-guard.ps1" if os.name == "nt" else "bootstrap-killswitch-guard.sh"
        )
        guard.write_text("exit 0\n", encoding="utf-8")
    before = files(installation)
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout == "{}" and not result.stderr
    assert files(installation) == before
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()


def test_bootstrap_refusal_does_not_disable_independent_hooks(materialized_codespaces):
    payload, installation, project = materialized_codespaces
    env = {
        **os.environ, "AGENT_CODESPACES_NO_SELFPROVISION": "1",
        "COPILOT_PLUGIN_ROOT": str(payload),
        "AGENT_BRIDGE_PROVIDERS_DIR": str(installation.parent / "providers.d"),
    }
    before = files(installation)
    for name, stdin in (
        ("bootstrap-check", None),
        ("register-bridge-provider", None),
        ("write-session-guidance", '{"sessionId":"bootstrap-independent","cwd":'
         + json.dumps(str(project)) + "}"),
    ):
        result = subprocess.run(
            hook_command(payload, project, installation.parent, name), env=env, cwd=project,
            input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=45,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    provider = json.loads(
        (installation.parent / "providers.d/agent-codespaces.json").read_text(encoding="utf-8-sig")
    )
    assert Path(provider["plugin_root"]) == payload
    assert provider["command"] == [str(payload / "bin" / (
        "agent-codespaces.cmd" if os.name == "nt" else "agent-codespaces"
    ))]
    guidance = installation.parent / (
        ".copilot/session-state/bootstrap-independent/instructions"
        "/agent-codespaces/session-guidance.instructions.md"
    )
    assert guidance.read_text().startswith("# Agent Codespaces session guidance\n")
    assert files(installation) == before
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()


@native_genesis
def test_actual_codespaces_bootstrap_native_lifetime_concurrency_and_installed_consumer(
    materialized_codespaces, area, native_window_observation,
):
    payload, installation, project = materialized_codespaces
    profile = installation.parent
    root = profile / ".copilot-extensions/update-driver"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_CODESPACES_HOME"] = str(installation)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(profile / "bootstrap-killswitch.json")
    isolate_disabled_owner(installation, project, env)
    deadline = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"][0]["timeoutSec"]
    assert deadline == 15
    command = hook_command(payload, project, profile)

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(area)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in area.rglob("*")
            if path.is_file() and (path.suffix == ".log" or path.name in {"stdout", "stderr"})
        )

    def owned(label):
        return owned_hook(
            command, env=env, project=project, directory=area / label,
            timeout=deadline, diagnostic=diagnostic, acknowledgement_prefix=ACKNOWLEDGEMENT,
        )

    def assert_admission(item, action):
        admission = json.loads(item.spec.source)
        assert admission["plugin"] == "agent-codespaces"
        assert admission.get("installer_action", "provision") == action
        assert Path(admission["payload"]) == payload
        assert Path(admission["runtime_root"]) == installation
        assert Path(admission["project"]) == project

    with owned("cold-first") as first:
        observe(lambda: foundation_status(root=root) is not None, 90, diagnostic)
        operation = foundation_status(root=root).operation_id
        assert foundation_status(root=root).state == "running" and not first.exited(), diagnostic()
        first.close_caller()
        assert not first.exited(), diagnostic()
        with owned("cold-second") as second:
            assert not first.exited() and not second.exited(), diagnostic()
            second.close_caller()

            def both_completed():
                receipts = [ordinary_receipt(caller.receipt_log) for caller in (first, second)]
                if not all(receipts):
                    return False
                return all(status(receipt, root=root).state == "completed" for receipt in receipts)

            observe(both_completed, 850, diagnostic)
            for caller in (first, second):
                observe(caller.exited, 60, diagnostic)
    requests = status(root=root)
    assert 1 <= len(requests) <= 2
    assert all(item.state == "completed" and item.exit_code == 0 for item in requests), diagnostic()
    assert foundation_status(root=root).operation_id == operation
    ordered = sorted(requests, key=lambda item: item.started_at)
    assert all(left.finished_at <= right.started_at for left, right in zip(ordered, ordered[1:]))
    for item in requests:
        assert_admission(item, "stamp")
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    count = len(requests)

    # Prepare through the already-qualified recipe, not the unadopted command.
    prepare = native_command(root, payload, installation, project, source=payload / "libs/update-driver")
    prepare += ["-Wait"] if os.name == "nt" else ["--wait"]
    initial = subprocess.run(
        prepare, env=env, cwd=project, capture_output=True, text=True,
        timeout=850, **agent_procutil.no_window_kwargs(),
    )
    assert initial.returncode == 0, initial.stdout + initial.stderr + diagnostic()
    count += 1
    if os.name != "nt":
        (Path(env["PATH"]) / "python3").symlink_to(sys._base_executable)

    def set_version(version):
        project_file = payload / "pyproject.toml"
        project_file.write_text(re.sub(
            r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', project_file.read_text(),
        ), encoding="utf-8")
        identity = json.loads((payload / "plugin.json").read_text())
        identity["version"] = version
        (payload / "plugin.json").write_text(json.dumps(identity), encoding="utf-8")

    set_version("99.0.0")
    with owned("drift") as caller:
        assert not caller.exited(), diagnostic()
        caller.close_caller()
        observe(lambda: receipt_completed_count(root, count + 1, caller.receipt_log), 850, diagnostic)
        drift = status(ordinary_receipt(caller.receipt_log), root=root)
        assert drift.exit_code == 0, diagnostic()
        assert_admission(drift, "provision")
        observe(caller.exited, 60, diagnostic)
    count += 1
    selected = (installation / "current-version").read_text().strip()
    assert selected == "99.0.0"
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    owner = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_codespaces", "owner", "--status"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert owner.returncode == 0, owner.stdout + owner.stderr
    assert json.loads(owner.stdout)["enabled"] is False
    assert not (profile / ".config/systemd").exists()
    if os.name == "nt":
        assert (profile / "task-probes").read_text() == "agent-codespaces-owner" * 2
    healthy = subprocess.run(
        command, env=env, cwd=project, capture_output=True, text=True,
        timeout=deadline, **agent_procutil.no_window_kwargs(),
    )
    assert healthy.returncode == 0 and healthy.stdout == "{}" and not healthy.stderr
    assert len(status(root=root)) == count and completion.read_bytes() == complete

    set_version("99.0.1")
    (payload / "src/agent_codespaces/__init__.py").write_text(
        'raise RuntimeError("fixture failed bootstrap candidate")\n', encoding="utf-8",
    )
    with owned("failed-candidate") as caller:
        caller.close_caller()
        observe(lambda: receipt_completed_count(root, count + 1, caller.receipt_log), 850, diagnostic)
        failed = status(ordinary_receipt(caller.receipt_log), root=root)
        assert failed.exit_code != 0, diagnostic()
        assert_admission(failed, "provision")
        observe(caller.exited, 60, diagnostic)
    count += 1
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    assert not (installation / "versions/99.0.1/.install-complete.json").exists()
    assert not (installation / "reconcile.lock").exists()
    payload.rename(payload.with_name("removed-codespaces"))
    primary = profile / ".local/bin" / (
        "agent-codespaces.ps1" if os.name == "nt" else "agent-codespaces"
    )
    dispatch = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(primary), "version"]
        if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(primary), "version"]
    )
    invoked = subprocess.run(
        dispatch, env={**env, "AGENT_CODESPACES_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert invoked.returncode == 0, invoked.stdout + invoked.stderr + diagnostic()
    origin = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_codespaces,json; from importlib.metadata import version; "
         "from agent_codespaces._build_info import BUILD_INFO; "
         "print(json.dumps({'origin':agent_codespaces.__file__,'version':version('agent-codespaces'),"
         "'build':BUILD_INFO}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert origin.returncode == 0, origin.stdout + origin.stderr
    identity = json.loads(origin.stdout)
    assert Path(identity["origin"]).is_relative_to(slot)
    assert str(Version(identity["build"]["version"])) == identity["version"]
    assert invoked.stdout.strip() == (
        "agent-codespaces " + identity["build"]["version"]
        + " (" + identity["build"]["commit"][:8] + ")"
    )
    assert len(status(root=root)) == count and completion.read_bytes() == complete
