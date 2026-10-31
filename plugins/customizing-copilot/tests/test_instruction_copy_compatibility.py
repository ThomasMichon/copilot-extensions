"""Compatibility publication and startup-equivalent source discovery."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_instruction_delivery import delivery, fixture, projections
from test_instruction_delivery import SCRIPTS


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


@pytest.mark.parametrize("mapping", [None, "actual-catalog", ""])
def test_runtime_catalog_alias_requires_an_explicit_valid_mapping(tmp_path: Path, mapping: str | None) -> None:
    repo, source, _spec = fixture(tmp_path)
    home = tmp_path / "home"
    market = repo / "marketplace"
    shutil.copytree(source.payload_root, market / "policy")
    (market / "marketplace.json").write_text(json.dumps({
        "name": "actual-catalog", "plugins": [{"name": "policy", "source": "policy"}],
    }))
    configured = {"source": "directory", "path": str(market)}
    if mapping is not None:
        configured["manifestName"] = mapping
    _runtime_settings(repo, home, configured)
    sources = projections.discover_enabled_sources(repo, home=home, require_trust=True, runtime=True)
    assert bool(sources[0].error) is (mapping != "actual-catalog")
    result = projections.render_local_cache(repo, lambda: sources)
    assert bool(result.blocking) is (mapping != "actual-catalog")


@pytest.mark.parametrize("invalid", ["missing", "malformed", "wrong-name", "unpacked"])
def test_invalid_plugin_manifest_is_incomplete_discovery(tmp_path: Path, invalid: str) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    current = repo / delivery.managed_destination(spec.destination, spec.plugin)
    before = current.read_bytes()
    manifest = source.payload_root / "plugin.json"
    if invalid in {"missing", "unpacked"}:
        manifest.unlink()
        if invalid == "unpacked":
            (source.payload_root / "instruction-projections.json").unlink()
    else:
        manifest.write_bytes(b"{" if invalid == "malformed" else b'{"name":"other"}')
    result = projections.render_local_cache(repo, lambda: [source])
    assert result.blocking and not result.written and not result.removed
    assert current.read_bytes() == before
    with pytest.raises(ValueError, match="no managed copy"):
        projections.resolve_instruction_source(repo, spec.destination, [source])


@pytest.mark.parametrize("name", ["rules.instructions.md", "rules.local.instructions.md"])
def test_runtime_top_level_destinations_match_startup(tmp_path: Path, name: str) -> None:
    repo, source, spec = fixture(tmp_path)
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    destination = f".github/instructions/{name}"
    data["projections"][0]["destination"] = destination
    declaration.write_text(json.dumps(data))
    expected = (
        ".github/instructions/agent-worktrees-generated/market/"
        + name.removesuffix(".instructions.md") + ".local.instructions.md"
    )
    target = repo / expected
    target.parent.mkdir(parents=True)
    target.write_bytes(b"current upstream copy")
    selection = projections.resolve_instruction_source(repo, destination, [source])
    assert selection["selectedPath"] == expected
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking, result.findings
    assert target.read_bytes() == (source.payload_root / spec.template).read_bytes()


@pytest.mark.parametrize("linked", [False, True])
def test_pending_pair_blocks_compatibility_discovery(tmp_path: Path, linked: bool) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    current = repo / delivery.managed_destination(spec.destination, spec.plugin)
    before = current.read_bytes()
    git_dir = tmp_path / "linked-admin" if linked else repo / ".git"
    pending = git_dir / "agent-worktrees-local-guidance/pair-pending"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"unfinished composition")
    if linked:
        (repo / ".git").write_text(f"gitdir: {git_dir}\n")

    def forbidden():
        pytest.fail("pending pairing must be checked before source discovery")

    result = projections.render_local_cache(repo, forbidden)
    assert result.blocking and not result.changed
    assert current.read_bytes() == before
    assert any("paired" in finding.message for finding in result.findings)


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr


def test_pending_pair_blocks_the_independent_compatibility_cli(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    current = repo / delivery.managed_destination(spec.destination, spec.plugin)
    before = current.read_bytes()
    _git(repo, "init", "-q")
    pending = repo / ".git/agent-worktrees-local-guidance/pair-pending"
    pending.parent.mkdir()
    pending.write_bytes(b"unfinished composition")
    home = tmp_path / "home"
    shutil.copytree(source.payload_root, home / ".copilot/installed-plugins/market/policy")
    (repo / ".gitignore").write_text("*.local.instructions.md\n")
    _runtime_settings(repo, home, {"source": "github", "repo": "example/plugins"})
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "manage-instruction-projections.py"),
         "render-local-cache", str(repo), "--json"],
        cwd=repo, env=dict(os.environ, HOME=str(home), USERPROFILE=str(home)),
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = json.loads(completed.stdout)
    assert completed.returncode != 0 and result["blocking"]
    assert any("paired" in finding["message"] for finding in result["findings"])
    assert current.read_bytes() == before
    pending.unlink()
    resumed = subprocess.run(
        completed.args, cwd=repo, env=dict(os.environ, HOME=str(home), USERPROFILE=str(home)),
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["written"] == [current.relative_to(repo).as_posix()]


@pytest.mark.parametrize("mutation", ["delete", "rename"])
@pytest.mark.parametrize("selected", [False, True])
def test_staged_removal_protects_the_remaining_working_copy(
    tmp_path: Path, mutation: str, selected: bool,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    current = repo / delivery.managed_destination(spec.destination, spec.plugin)
    relative = current.relative_to(repo).as_posix()
    before = current.read_bytes()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("*.local.instructions.md\n")
    _git(repo, "add", "--force", relative)
    _git(repo, "-c", "user.name=Test User", "-c", "user.email=test@example.com",
         "commit", "-q", "-m", "Record the fixture copy")
    if mutation == "delete":
        _git(repo, "rm", "--cached", "--quiet", relative)
    else:
        _git(repo, "mv", relative, "retained.md")
        current.write_bytes(before)
    (source.payload_root / spec.template).write_bytes(b"must not replace staged source")
    result = projections.render_local_cache(repo, lambda: [source] if selected else [])
    assert not result.written and not result.removed
    assert any("tracked" in finding.check for finding in result.findings)
    assert current.read_bytes() == before


@pytest.mark.parametrize("identity", ["@market", "policy@", "policy@market@other"])
def test_malformed_activation_identity_cannot_authorize_cleanup(tmp_path: Path, identity: str) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.render_local_cache(repo, lambda: [source]).blocking
    current = repo / delivery.managed_destination(spec.destination, spec.plugin)
    before = current.read_bytes()
    home = tmp_path / "home"
    settings = _runtime_settings(repo, home, {"source": "github", "repo": "example/plugins"})
    data = json.loads(settings.read_bytes())
    data["enabledPlugins"] = {identity: True}
    settings.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity"):
        projections.discover_enabled_sources(repo, home=home, require_trust=True, runtime=True)
    result = projections.render_local_cache(repo, lambda: projections.discover_enabled_sources(
        repo, home=home, require_trust=True, runtime=True,
    ))
    assert result.blocking and not result.written and not result.removed
    assert current.read_bytes() == before
    assert any("identity" in finding.message for finding in result.findings)


@pytest.mark.parametrize("identity", ["policy_tools@team_market", "Policy@Market"])
def test_runtime_portable_plugin_identities_match_startup(tmp_path: Path, identity: str) -> None:
    repo, source, spec = fixture(tmp_path)
    name, market = identity.split("@")
    (source.payload_root / "plugin.json").write_text(json.dumps({"name": name}))
    source.origin = f"{market}/{name}"
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking, result.findings
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["identity"]["plugin"] == identity
    assert selected["selectedPath"] == delivery.managed_destination(spec.destination, identity)
    assert (repo / selected["selectedPath"]).read_bytes() == (source.payload_root / spec.template).read_bytes()


@pytest.mark.parametrize("filename", ["instruction-projections.json", "plugin.json"])
def test_runtime_json_bom_matches_startup(tmp_path: Path, filename: str) -> None:
    repo, source, spec = fixture(tmp_path)
    path = source.payload_root / filename
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking, result.findings
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == result.written[0]
    assert (repo / result.written[0]).read_bytes() == (source.payload_root / spec.template).read_bytes()


@pytest.mark.parametrize("cross_provider", [False, True])
def test_runtime_excludes_all_ancestor_descendant_output_collisions(
    tmp_path: Path, cross_provider: bool,
) -> None:
    from types import SimpleNamespace

    repo, source, spec = fixture(tmp_path)
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    first = dict(data["projections"][0],
                 destination=".github/instructions/policy/topic.instructions.md")
    child = dict(first, id="child",
                 destination=".github/instructions/policy/topic.local.instructions.md/body.instructions.md")
    safe = dict(first, id="safe", destination=".github/instructions/policy/safe.instructions.md")
    data["projections"] = [first, safe] if cross_provider else [first, child, safe]
    declaration.write_text(json.dumps(data))
    sources = [source]
    if cross_provider:
        other_root = tmp_path / "other-plugin"
        shutil.copytree(source.payload_root, other_root)
        (other_root / "plugin.json").write_text('{"name":"other"}')
        (other_root / declaration.name).write_text(json.dumps(dict(data, projections=[child])))
        sources.append(SimpleNamespace(origin="market/other", payload_root=other_root))
    previous = repo / delivery.managed_destination(first["destination"], spec.plugin)
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"retain the earlier copy, but do not select it as current")
    result = projections.render_local_cache(repo, lambda: sources)
    expected = delivery.managed_destination(safe["destination"], spec.plugin)
    assert result.blocking
    assert result.written == [expected] and not result.removed
    assert previous.read_bytes() == b"retain the earlier copy, but do not select it as current"
    with pytest.raises(ValueError, match="ambiguous|no managed copy"):
        projections.resolve_instruction_source(repo, first["destination"], sources)


def test_compatibility_result_preserves_declared_source_count(tmp_path: Path) -> None:
    repo, source, _spec = fixture(tmp_path)
    result = projections.render_local_cache(repo, lambda: [source])
    assert not result.blocking
    assert result.declared == result.to_dict()["declared"] == 1
