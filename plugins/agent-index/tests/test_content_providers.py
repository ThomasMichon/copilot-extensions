"""Tests for agent-index's external content-domain provider mechanism
(``providers.d`` discovery + the ``CliSourceConnector`` process-boundary
adapter)."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from agent_index.sources.providers import (
    CliSourceConnector,
    ProviderError,
    ProviderManifest,
    discover_and_register_providers,
    parse_manifest,
    scan_provider_registry,
)

# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def test_parse_manifest_minimal():
    manifest = parse_manifest({"source_name": "gitea", "command": ["/abs/provider"]})
    assert manifest.source_name == "gitea"
    assert manifest.command == ("/abs/provider",)
    assert manifest.description == ""


def test_parse_manifest_with_description():
    manifest = parse_manifest(
        {"source_name": "gitea", "command": ["/abs/provider"], "description": "Facility Gitea"}
    )
    assert manifest.description == "Facility Gitea"


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"command": ["/abs/provider"]},
        {"source_name": "", "command": ["/abs/provider"]},
        {"source_name": 5, "command": ["/abs/provider"]},
        {"source_name": "gitea"},
        {"source_name": "gitea", "command": []},
        {"source_name": "gitea", "command": "not-a-list"},
        {"source_name": "gitea", "command": [1, 2]},
        {"source_name": "gitea", "command": ["/abs/provider"], "description": 5},
    ],
)
def test_parse_manifest_rejects_malformed(data):
    with pytest.raises(ValueError):
        parse_manifest(data)


# ---------------------------------------------------------------------------
# providers.d discovery
# ---------------------------------------------------------------------------


def _write_script(path: Path, body: str) -> Path:
    """Write an executable Python script (cross-platform: shebang + chmod)."""
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _write_manifest(directory: Path, name: str, *, source_name: str, command: list[str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / f"{name}.json"
    manifest_path.write_text(
        json.dumps({"source_name": source_name, "command": command}), encoding="utf-8"
    )
    return manifest_path


def test_scan_absent_directory_returns_no_manifests(tmp_path):
    report = scan_provider_registry(tmp_path / "does-not-exist")
    assert report.manifests == {}
    assert report.findings == ()


def test_scan_skips_malformed_manifest_with_finding(tmp_path):
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    report = scan_provider_registry(tmp_path)
    assert report.manifests == {}
    assert len(report.findings) == 1
    assert report.findings[0].reason == "invalid-entry"


def test_scan_skips_manifest_with_missing_command(tmp_path):
    _write_manifest(tmp_path, "gone", source_name="gitea", command=[str(tmp_path / "nope")])
    report = scan_provider_registry(tmp_path)
    assert report.manifests == {}
    assert report.findings[0].reason == "missing-target"


def test_scan_finds_valid_manifest(tmp_path):
    provider = _write_script(tmp_path / "provider.py", "pass\n")
    _write_manifest(
        tmp_path, "gitea", source_name="gitea", command=[sys.executable, str(provider)]
    )
    report = scan_provider_registry(tmp_path)
    assert set(report.manifests) == {"gitea"}
    assert report.findings == ()


def test_scan_dedupes_duplicate_source_name(tmp_path):
    provider = _write_script(tmp_path / "provider.py", "pass\n")
    _write_manifest(tmp_path, "a", source_name="gitea", command=[sys.executable, str(provider)])
    _write_manifest(tmp_path, "b", source_name="gitea", command=[sys.executable, str(provider)])
    report = scan_provider_registry(tmp_path)
    assert set(report.manifests) == {"gitea"}
    assert any(f.reason == "duplicate" for f in report.findings)


def test_discover_and_register_providers_registers_connector(tmp_path, monkeypatch):
    from agent_index import sources as sources_mod

    provider = _write_script(tmp_path / "provider.py", "pass\n")
    _write_manifest(
        tmp_path, "gitea", source_name="gitea", command=[sys.executable, str(provider)]
    )
    before = sources_mod.registered_source_prefixes()
    try:
        discover_and_register_providers(tmp_path)
        after = sources_mod.registered_source_prefixes()
        assert "gitea" in after
        assert "gitea" not in before
        connector = sources_mod.get_connector("gitea")
        assert isinstance(connector, CliSourceConnector)
    finally:
        # The registry is process-global (mirrors the built-in connectors'
        # own module-level registration) -- clean up so this test can't leak
        # a "gitea" prefix into any other test in the same process.
        sources_mod._CONNECTORS.pop("gitea", None)


# ---------------------------------------------------------------------------
# CliSourceConnector -- the process-boundary adapter
# ---------------------------------------------------------------------------


_ENTRY_PROVIDER_BODY = """
import json
import sys

verb = sys.argv[1]
if verb == "content-discover":
    print(json.dumps({"entries": [
        {"path": "a.md", "content": "hello", "language": "markdown", "source": "gitea:a"},
    ]}))
elif verb == "content-discover-changed":
    print(json.dumps({"entries": []}))
elif verb == "content-list-paths":
    print(json.dumps({"paths": {"gitea": ["a.md"]}}))
elif verb == "content-current-commit":
    print(json.dumps({"commit": "abc123"}))
else:
    sys.exit(1)
"""


def _entry_connector(tmp_path: Path) -> CliSourceConnector:
    provider = _write_script(tmp_path / "provider.py", _ENTRY_PROVIDER_BODY)
    manifest = ProviderManifest(source_name="gitea", command=(sys.executable, str(provider)))
    return CliSourceConnector("gitea", manifest)


def test_discover_returns_parsed_entries(tmp_path):
    connector = _entry_connector(tmp_path)
    entries = connector.discover()
    assert len(entries) == 1
    assert entries[0].path == "a.md"
    assert entries[0].content == "hello"
    assert entries[0].metadata == {}


def test_discover_changed_returns_empty(tmp_path):
    connector = _entry_connector(tmp_path)
    assert connector.discover_changed("abc123") == []


def test_list_paths_returns_sets(tmp_path):
    connector = _entry_connector(tmp_path)
    paths = connector.list_paths()
    assert paths == {"gitea": {"a.md"}}


def test_current_commit_returns_string(tmp_path):
    connector = _entry_connector(tmp_path)
    assert connector.current_commit() == "abc123"


def test_source_name_property(tmp_path):
    connector = _entry_connector(tmp_path)
    assert connector.source_name == "gitea"


def _bad_provider_connector(tmp_path: Path, body: str) -> CliSourceConnector:
    provider = _write_script(tmp_path / "provider.py", body)
    manifest = ProviderManifest(source_name="gitea", command=(sys.executable, str(provider)))
    return CliSourceConnector("gitea", manifest)


def test_nonzero_exit_fails_closed(tmp_path):
    connector = _bad_provider_connector(
        tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n"
    )
    with pytest.raises(ProviderError, match="exited 1"):
        connector.discover()


def test_malformed_json_fails_closed(tmp_path):
    connector = _bad_provider_connector(tmp_path, "print('not json')\n")
    with pytest.raises(ProviderError, match="malformed JSON"):
        connector.discover()


def test_missing_entries_key_fails_closed(tmp_path):
    connector = _bad_provider_connector(tmp_path, "import json\nprint(json.dumps({}))\n")
    with pytest.raises(ProviderError, match="entries"):
        connector.discover()


def test_entry_missing_required_field_fails_closed(tmp_path):
    body = (
        "import json\n"
        'print(json.dumps({"entries": [{"path": "a.md"}]}))\n'
    )
    connector = _bad_provider_connector(tmp_path, body)
    with pytest.raises(ProviderError, match="missing required field"):
        connector.discover()


def test_entry_non_string_field_fails_closed(tmp_path):
    body = (
        "import json\n"
        'print(json.dumps({"entries": [{"path": 1, "content": "x", '
        '"language": "md", "source": "gitea:a"}]}))\n'
    )
    connector = _bad_provider_connector(tmp_path, body)
    with pytest.raises(ProviderError, match="non-string"):
        connector.discover()


def test_list_paths_malformed_fails_closed(tmp_path):
    body = 'import json\nprint(json.dumps({"paths": {"gitea": [1, 2]}}))\n'
    connector = _bad_provider_connector(tmp_path, body)
    with pytest.raises(ProviderError, match="malformed"):
        connector.list_paths()


def test_current_commit_malformed_fails_closed(tmp_path):
    body = 'import json\nprint(json.dumps({"commit": 5}))\n'
    connector = _bad_provider_connector(tmp_path, body)
    with pytest.raises(ProviderError, match="must be a string or null"):
        connector.current_commit()


def test_current_commit_none_is_valid(tmp_path):
    body = 'import json\nprint(json.dumps({"commit": None}))\n'
    connector = _bad_provider_connector(tmp_path, body)
    assert connector.current_commit() is None


def test_spawn_failure_fails_closed(tmp_path):
    manifest = ProviderManifest(source_name="gitea", command=(str(tmp_path / "does-not-exist"),))
    connector = CliSourceConnector("gitea", manifest)
    with pytest.raises(ProviderError, match="failed to run"):
        connector.discover()


def test_cancel_check_raises_before_subprocess_spawn(tmp_path):
    connector = _entry_connector(tmp_path)

    def _cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        connector.discover(cancel_check=_cancelled)
