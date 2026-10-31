"""Target repository source must not replace a deployed command owner."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import installer as inst
from agent_worktrees import project_command_templates as templates
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["plain", "quote ' percent% dollar$"])
def path_label(request):
    return request.param


@pytest.fixture
def payloads(tmp_path, monkeypatch, path_label):
    monkeypatch.delenv("AGENT_WORKTREES_PAYLOAD_ROOT", raising=False)
    repo = tmp_path / "source-checkout"
    source = repo / "plugins" / "agent-worktrees"
    (source / "src" / "agent_worktrees").mkdir(parents=True)
    deployed = tmp_path / f"installed-payload-{path_label}"
    deployed.mkdir()
    for root in (source, deployed):
        (root / "plugin.json").write_text(json.dumps({
            "name": "agent-worktrees", "version": "1.0.0",
            "repository": "https://example.invalid/extensions",
        }), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(inst, "install_dir", lambda: runtime)
    monkeypatch.setattr(inst, "local_bin", lambda: tmp_path / "bin")
    return repo, source, deployed, runtime


def _record_source(runtime, payload):
    (runtime / "deploy-manifest.json").write_text(json.dumps({
        "source": {"kind": "marketplace", "path": str(payload)},
    }), encoding="utf-8")


@pytest.mark.parametrize("cell", [False, True])
def test_target_checkout_does_not_override_deployed_payload(payloads, monkeypatch, cell):
    repo, source, deployed, runtime = payloads
    _record_source(runtime, deployed)
    monkeypatch.setattr(inst.registry_paths, "installation_context", lambda: {
        "marketplaceId": "example", "installReceipt": "receipt",
    } if cell else None)

    assert inst._payload_root(repo) == deployed.resolve()
    assert inst._binstub_owner(repo)["payload_root"] == deployed.resolve().as_posix()
    target = deployed if cell else inst.local_bin()
    expected = templates.render(
        "example", deployed.resolve(), windows=sys.platform == "win32",
        user_bin=None if cell else target,
    )
    assert {
        path.name: content for path, content in inst._project_binstub_specs("example", repo_dir=repo)
    } == expected


def test_explicit_source_install_retains_its_own_owner(payloads, monkeypatch):
    repo, source, deployed, runtime = payloads
    _record_source(runtime, deployed)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(source))

    assert inst._payload_root(repo) == source.resolve()
    assert inst._binstub_owner(repo)["payload_root"] == source.resolve().as_posix()


def test_first_source_install_can_resolve_without_deployment_manifest(payloads):
    repo, source, _deployed, _runtime = payloads
    assert inst._payload_root(repo) == source.resolve()


def test_native_consumer_keeps_deployed_owner(payloads, tmp_path):
    repo, _source, deployed, _runtime = payloads
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    _record_source(runtime, deployed)
    environment = dict(os.environ, AGENT_HOME=str(tmp_path))
    environment.pop("AGENT_WORKTREES_PAYLOAD_ROOT", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; from pathlib import Path; from agent_worktrees import installer; "
         "assert installer._payload_root(Path(sys.argv[1])) == Path(sys.argv[2]).resolve(); "
         "print('native-owner-ok')", str(repo), str(deployed)],
        env=environment, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "native-owner-ok"


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.parametrize("inherited_payload", [False, True])
def test_payload_entry_binds_relocated_runtime_to_its_own_peers(
    tmp_path, request, shell, inherited_payload,
):
    executable = request.getfixturevalue(f"native_{shell}")
    home = tmp_path / "home"
    payload = tmp_path / "marketplace with spaces" / "agent-worktrees"
    peer = payload.parent / "agent-pull-requests"
    suffix = ".cmd" if os.name == "nt" else ""
    for root in (payload, peer):
        (root / "bin").mkdir(parents=True)
        (root / "plugin.json").write_text(json.dumps({"name": root.name}), encoding="utf-8")
        (root / "bin" / f"{root.name}{suffix}").touch()
    runtime_bin = home / ".agent-worktrees" / "bin"
    runtime_bin.mkdir(parents=True)
    if shell == "powershell":
        entry = payload / "bin" / "agent-worktrees.ps1"
        shutil.copyfile(PLUGIN / "bin" / entry.name, entry)
        name = "agent-worktrees-update-entry.ps1"
        shutil.copyfile(PLUGIN / "bin" / name, entry.parent / name)
        (runtime_bin / "resolve-runtime.ps1").write_text(
            "$AwPy = '" + sys.executable.replace("'", "''") + "'\n", encoding="utf-8",
        )
        command = [executable, "-NoProfile", "-File", str(entry)]
    else:
        entry = payload / "bin" / "agent-worktrees"
        shutil.copyfile(PLUGIN / "bin" / entry.name, entry)
        name = "agent-worktrees-update-entry.sh"
        shutil.copyfile(PLUGIN / "bin" / name, entry.parent / name)
        (runtime_bin / "resolve-runtime.sh").write_text(
            f"AW_PY={shlex.quote(sys.executable)}\n", encoding="utf-8",
        )
        command = [executable, str(entry)]
    installed = tmp_path / "relocated-runtime" / "Lib" / "site-packages"
    package = installed / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    for name in ("registry_paths.py", "pr_watch_owner.py"):
        shutil.copyfile(PLUGIN / "src" / "agent_worktrees" / name, package / name)
    (package / "__main__.py").write_text(
        "import json,sys\n"
        "from .pr_watch_owner import _payload_commands\n"
        "print(json.dumps({'peers':_payload_commands(None,None),'args':sys.argv[1:]}))\n"
        "raise SystemExit(23)\n", encoding="utf-8",
    )
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        environment.pop(key, None)
    if inherited_payload:
        environment["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(tmp_path / "foreign-payload")
    result = subprocess.run(
        [*command, "pr-watch", "ownerstatus", "--json"], cwd=installed, env=environment,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["peers"] == [
        str(root.resolve() / "bin" / f"{root.name}{suffix}") for root in (payload, peer)
    ]
    assert observed["args"] == ["pr-watch", "ownerstatus", "--json"]
