"""Target repository source must not replace a deployed command owner."""

import json

import pytest

from agent_worktrees import installer as inst


@pytest.fixture
def payloads(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_PAYLOAD_ROOT", raising=False)
    repo = tmp_path / "source-checkout"
    source = repo / "plugins" / "agent-worktrees"
    (source / "src" / "agent_worktrees").mkdir(parents=True)
    deployed = tmp_path / "installed-payload"
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
    for _path, content in inst._project_binstub_specs("example", repo_dir=repo):
        assert str(source) not in content
        assert str(target) in content or target.as_posix() in content


def test_explicit_source_install_retains_its_own_owner(payloads, monkeypatch):
    repo, source, deployed, runtime = payloads
    _record_source(runtime, deployed)
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(source))

    assert inst._payload_root(repo) == source.resolve()
    assert inst._binstub_owner(repo)["payload_root"] == source.resolve().as_posix()


def test_first_source_install_can_resolve_without_deployment_manifest(payloads):
    repo, source, _deployed, _runtime = payloads
    assert inst._payload_root(repo) == source.resolve()
