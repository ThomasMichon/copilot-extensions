"""Source provenance reads must not depend on installation-cell state."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.installation_context_smoke


@pytest.fixture
def context_module():
    spec = importlib.util.spec_from_file_location(
        "payload_source_installation_context", LIB / "installation_context.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _installed(tmp_path: Path, descriptor: dict) -> tuple[Path, Path]:
    copilot = tmp_path / "home" / ".copilot"
    payload = copilot / "installed-plugins" / "example" / "agent-worktrees"
    payload.mkdir(parents=True)
    _write_json(
        copilot / "settings.json",
        {"extraKnownMarketplaces": {"example": {"source": descriptor}}},
    )
    return copilot, payload


def _manifest(root: Path, payload: Path) -> None:
    _write_json(
        root / ".github" / "plugin" / "marketplace.json",
        {
            "name": "example",
            "plugins": [{
                "name": "agent-worktrees",
                "source": payload.relative_to(root).as_posix(),
            }],
        },
    )


def _snapshot(root: Path) -> dict:
    result = {}
    for path in [root, *root.rglob("*")]:
        metadata = path.stat()
        result[path.relative_to(root).as_posix()] = (
            path.is_dir(), metadata.st_mtime_ns,
            None if path.is_dir() else path.read_bytes(),
        )
    return result


@pytest.mark.parametrize("descriptor,canonical", [
    ({"source": "github", "repo": "Example/Marketplace.git"}, "github:example/marketplace"),
    ({"source": "git", "url": "https://example.test/plugins.git"}, "git:https://example.test/plugins"),
])
def test_installed_source_without_cell_state(
    tmp_path, monkeypatch, context_module, descriptor, canonical
) -> None:
    copilot, payload = _installed(tmp_path, descriptor)
    durable = tmp_path / "home" / ".copilot-extensions"
    _write_json(durable / "installation-mode.json", {"invalid": True})
    _write_json(durable / "marketplaces" / "foreign" / "namespace.json", {"invalid": True})
    before = _snapshot(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("a source-only read consulted installation state")

    monkeypatch.setattr(context_module, "_find_existing_source", forbidden)
    original_read = context_module.read_json

    def scoped_read(path):
        assert not Path(path).is_relative_to(durable)
        return original_read(path)

    monkeypatch.setattr(context_module, "read_json", scoped_read)
    source = context_module.resolve_payload_source(
        payload_root=payload, plugin_id="agent-worktrees",
        copilot_home=copilot,
        environment={"COPILOT_EXTENSIONS_CONTEXT": str(durable / "foreign.json")},
    )
    assert source["source"]["canonical"] == canonical
    assert source["source"]["kind"] == descriptor["source"]
    assert source["sourceFingerprint"].startswith("sha256:")
    assert source["pluginId"] == "agent-worktrees"
    assert source["payloadRoot"] == str(payload.resolve())
    assert source["locator"]["marketplaceKey"] == "example"
    assert set(source) == {
        "source", "sourceFingerprint", "marketplaceId", "pluginId", "payloadRoot", "locator",
    }
    assert _snapshot(tmp_path) == before


def test_directory_source_is_catalog_attributable(tmp_path, context_module) -> None:
    payload = tmp_path / "plugins" / "agent-worktrees"
    payload.mkdir(parents=True)
    _manifest(tmp_path, payload)
    before = _snapshot(tmp_path)
    source = context_module.resolve_payload_source(
        payload_root=payload, plugin_id="agent-worktrees",
        copilot_home=tmp_path / "absent-copilot", environment={},
    )
    assert source["source"]["canonical"] == f"directory:{tmp_path.resolve()}"
    assert source["locator"] == {
        "kind": "directory", "marketplaceRoot": str(tmp_path.resolve()),
    }
    assert _snapshot(tmp_path) == before


def test_explicit_management_source_is_not_an_installation_receipt(
    tmp_path, context_module
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    source = context_module.resolve_payload_source(
        payload_root=payload, plugin_id="agent-worktrees",
        source_descriptor={"source": "github", "repo": "example/marketplace", "ref": "dev"},
        marketplace_key="example", copilot_home=tmp_path / "copilot", environment={},
    )
    assert source["source"]["ref"] == "dev"
    assert source["locator"] is None
    assert not (tmp_path / "copilot").exists()
    assert "installReceipt" not in source
    assert "runtimeRoot" not in source


@pytest.mark.parametrize("failure", [
    "missing-declaration", "conflicting-declaration", "wrong-plugin", "inherited-payload",
    "relative-payload", "unsupported-source", "malformed-settings",
])
def test_source_provenance_failures_do_not_fall_back(
    tmp_path, context_module, failure
) -> None:
    copilot, payload = _installed(
        tmp_path, {"source": "github", "repo": "example/marketplace"}
    )
    # An enclosing directory catalog must not rescue invalid installed provenance.
    _manifest(tmp_path, payload)
    arguments = {
        "payload_root": payload, "plugin_id": "agent-worktrees",
        "copilot_home": copilot, "environment": {},
    }
    if failure == "missing-declaration":
        _write_json(copilot / "settings.json", {})
    elif failure == "conflicting-declaration":
        _write_json(copilot / "settings.local.json", {
            "extraKnownMarketplaces": {
                "example": {"source": {"source": "github", "repo": "other/marketplace"}},
            },
        })
    elif failure == "wrong-plugin":
        arguments["plugin_id"] = "agent-index"
    elif failure == "inherited-payload":
        arguments["environment"] = {"COPILOT_PLUGIN_ROOT": str(tmp_path)}
    elif failure == "relative-payload":
        arguments["payload_root"] = Path("relative")
    elif failure == "unsupported-source":
        _write_json(copilot / "settings.json", {
            "extraKnownMarketplaces": {"example": {"source": {"source": "unknown"}}},
        })
    else:
        (copilot / "settings.json").write_text("{", encoding="utf-8")
    before = _snapshot(tmp_path)
    with pytest.raises(context_module.InstallationContextError):
        context_module.resolve_payload_source(**arguments)
    assert _snapshot(tmp_path) == before


def test_context_keeps_cell_ownership_validation(
    tmp_path, monkeypatch, context_module
) -> None:
    copilot, payload = _installed(
        tmp_path, {"source": "github", "repo": "example/marketplace"}
    )
    arguments = {
        "payload_root": payload, "plugin_id": "agent-worktrees",
        "copilot_home": copilot, "environment": {},
    }
    source = context_module.resolve_payload_source(**arguments)
    calls = []

    def existing(durable, fingerprint, marketplace_id, locator):
        calls.append((durable, fingerprint, marketplace_id, locator))
        return []

    monkeypatch.setattr(context_module, "_find_existing_source", existing)
    durable = tmp_path / "durable"
    context = context_module.resolve_context(**arguments, durable_home=durable)
    assert {key: context[key] for key in source} == source
    assert calls == [(
        durable, source["sourceFingerprint"], source["marketplaceId"], source["locator"],
    )]
    assert context["operative"] is False
    assert Path(context["installReceipt"]).parts[-3:] == (
        "plugins", "agent-worktrees", "install.json",
    )

    def foreign(*args):
        return [{"sameId": False, "locatorMatch": False, "marketplaceId": "foreign"}]

    monkeypatch.setattr(context_module, "_find_existing_source", foreign)
    with pytest.raises(context_module.InstallationContextError, match="explicit rebind"):
        context_module.resolve_context(**arguments, durable_home=durable)


def test_unknown_payload_never_invents_source(tmp_path, context_module) -> None:
    payload = tmp_path / "agent-worktrees"
    payload.mkdir()
    _write_json(payload / "plugin.json", {
        "name": "agent-worktrees", "repository": "https://example.test/guess.git",
    })
    with pytest.raises(context_module.InstallationContextError, match="Cannot establish"):
        context_module.resolve_payload_source(
            payload_root=payload, plugin_id="agent-worktrees",
            copilot_home=tmp_path / "copilot", environment={},
        )
