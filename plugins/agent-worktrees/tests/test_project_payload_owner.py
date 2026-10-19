"""Target repository source must not replace a deployed command owner."""

import json
import os
import subprocess
import sys

import pytest

from agent_worktrees import installer as inst
from agent_worktrees import project_command_templates as templates
from agent_procutil import no_window_kwargs


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
