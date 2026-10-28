"""Compatibility publication and startup-equivalent source discovery."""

import json
from pathlib import Path
import shutil

import pytest

from test_instruction_delivery import delivery, fixture, projections


@pytest.mark.parametrize("reverse", [False, True])
def test_refresh_failure_does_not_block_an_independent_existing_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool,
) -> None:
    repo, source, spec = fixture(tmp_path)
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    second = dict(data["projections"][0], id="second",
                  destination=".github/instructions/policy/second.instructions.md")
    data["projections"].append(second)
    if reverse:
        data["projections"].reverse()
    declaration.write_text(json.dumps(data))
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    broken = repo / delivery.managed_destination(spec.destination, spec.plugin)
    healthy = repo / delivery.managed_destination(second["destination"], spec.plugin)
    before = broken.read_bytes()
    template = source.payload_root / spec.template
    template.write_bytes(b"updated upstream instructions")
    write = projections.transaction.atomic_write

    def fail_one(path: Path, content: bytes) -> None:
        if path == broken:
            raise OSError("one destination cannot be replaced")
        write(path, content)

    monkeypatch.setattr(projections.transaction, "atomic_write", fail_one)
    result = projections.render_local_cache(repo, lambda: [source])
    assert result.blocking
    assert broken.read_bytes() == before
    assert healthy.read_bytes() == template.read_bytes()
    assert result.written == [healthy.relative_to(repo).as_posix()]
    assert not result.removed


def test_case_only_destination_change_keeps_the_new_copy(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    old = repo / delivery.managed_destination(spec.destination, spec.plugin)
    renamed_destination = spec.destination.replace("rules", "RULES")
    new = repo / delivery.managed_destination(renamed_destination, spec.plugin)
    case_insensitive = new.exists()
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    data["projections"][0]["destination"] = renamed_destination
    declaration.write_text(json.dumps(data))
    template = source.payload_root / spec.template
    template.write_bytes(b"current case-renamed instructions")
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking, result.findings
    assert new.read_bytes() == template.read_bytes()
    assert result.removed == ([] if case_insensitive else [old.relative_to(repo).as_posix()])


def test_case_only_change_cannot_overwrite_a_tracked_portable_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    old = repo / delivery.managed_destination(spec.destination, spec.plugin)
    before = old.read_bytes()
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    data["projections"][0]["destination"] = spec.destination.replace("rules", "RULES")
    declaration.write_text(json.dumps(data))
    (source.payload_root / spec.template).write_bytes(b"must not replace tracked bytes")
    tracked = old.relative_to(repo).as_posix()
    monkeypatch.setattr(projections, "_resolve_git_tracked_paths", lambda *a: ({tracked}, False))
    result = projections.render_local_cache(repo, lambda: [source])
    assert result.blocking and not result.written and not result.removed
    assert old.read_bytes() == before


def _runtime_settings(repo: Path, home: Path, source: dict, *, native_builtin: bool = False) -> Path:
    settings = repo / ".github/copilot/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {
            "builtin": {"enabled": True} if native_builtin else True, "policy@market": True,
        },
        "extraKnownMarketplaces": {"market": {"source": source}},
    }))
    (home / ".copilot").mkdir(parents=True, exist_ok=True)
    (home / ".copilot/config.json").write_text(json.dumps({
        "trustedFolders": [str(repo.resolve())],
    }))
    return settings


@pytest.mark.parametrize("require_trust", [False, True])
def test_native_builtin_settings_do_not_block_marketplace_instructions(
    tmp_path: Path, require_trust: bool,
) -> None:
    repo, source, _spec = fixture(tmp_path)
    home = tmp_path / "home"
    installed = home / ".copilot/installed-plugins/market/policy"
    shutil.copytree(source.payload_root, installed)
    _runtime_settings(repo, home, {"source": "github", "repo": "example/plugins"}, native_builtin=True)
    sources = projections.discover_enabled_sources(repo, home=home, require_trust=require_trust, runtime=True)
    assert [item.origin for item in sources] == ["market/policy"]
    assert not projections.render_local_cache(repo, lambda: sources).blocking


@pytest.mark.parametrize("kind", ["github", "git", "git-subdir", "npm", "url"])
def test_runtime_remote_source_kinds_use_the_installed_payload(tmp_path: Path, kind: str) -> None:
    repo, source, spec = fixture(tmp_path)
    home = tmp_path / "home"
    installed = home / ".copilot/installed-plugins/market/policy"
    shutil.copytree(source.payload_root, installed)
    _runtime_settings(repo, home, {"source": kind, "repo": "example/plugins"})
    sources = projections.discover_enabled_sources(repo, home=home, require_trust=True, runtime=True)
    assert len(sources) == 1 and not sources[0].error
    assert sources[0].payload_root == installed
    result = projections.render_local_cache(repo, lambda: sources)
    assert not result.blocking, result.findings
    selected = projections.resolve_instruction_source(repo, spec.destination, sources)
    assert selected["selectedPath"] == result.written[0]


@pytest.mark.parametrize("relative", [
    ".github/plugin/marketplace.json", "marketplace.json",
    ".plugin/marketplace.json", ".claude-plugin/marketplace.json",
])
def test_runtime_local_catalog_layouts_match_startup(tmp_path: Path, relative: str) -> None:
    repo, source, spec = fixture(tmp_path)
    home = tmp_path / "home"
    market = repo / "marketplace"
    payload = market / "policy"
    shutil.copytree(source.payload_root, payload)
    manifest = market / relative
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "name": "market", "plugins": [{"name": "policy", "source": "policy"}],
    }))
    _runtime_settings(repo, home, {"source": "local", "path": str(market)})
    sources = projections.discover_enabled_sources(repo, home=home, require_trust=True, runtime=True)
    assert len(sources) == 1 and not sources[0].error
    assert sources[0].payload_root == payload
    result = projections.render_local_cache(repo, lambda: sources)
    assert not result.blocking, result.findings
    selected = projections.resolve_instruction_source(repo, spec.destination, sources)
    assert selected["selectedPath"] == result.written[0]
