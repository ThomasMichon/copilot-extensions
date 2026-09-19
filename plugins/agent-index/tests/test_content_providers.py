"""Tests for agent-index's external content-domain provider mechanism
(``providers.d`` discovery + the ``CliSourceConnector`` process-boundary
adapter)."""

from __future__ import annotations

import json
import stat
import subprocess
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


def test_parse_manifest_strips_trailing_colon():
    """A trailing ':' would register a prefix that never matches
    get_connector()'s hierarchical 'source.startswith(prefix + \":\")' lookup --
    normalize it away, mirroring agent-bridge's own namespace manifests."""
    manifest = parse_manifest({"source_name": "gitea:", "command": ["/abs/provider"]})
    assert manifest.source_name == "gitea"


def test_parse_manifest_rejects_colon_only_source_name():
    with pytest.raises(ValueError):
        parse_manifest({"source_name": "::", "command": ["/abs/provider"]})


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


def test_scan_rejects_relative_command(tmp_path):
    _write_manifest(tmp_path, "gitea", source_name="gitea", command=["provider-cli"])
    report = scan_provider_registry(tmp_path)
    assert report.manifests == {}
    assert report.findings[0].reason == "target-unusable"


def test_resolve_command_treats_stat_oserror_as_target_unusable(tmp_path, monkeypatch):
    """A ``stat()`` failure other than "missing" (e.g. permission denied) must
    become a clean ``target-unusable``, not bubble up as an indeterminate scan
    result that could keep a stale manifest alive."""
    from agent_index.sources import providers as providers_mod

    provider = tmp_path / "provider"
    provider.write_text("", encoding="utf-8")

    def _denied(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "stat", _denied)
    with pytest.raises(providers_mod.TargetUnusableError, match="not accessible"):
        providers_mod._resolve_command((str(provider),))


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


def test_discover_and_register_providers_skips_builtin_collision(tmp_path):
    """A manifest reusing a built-in prefix (``git``) must never overwrite it."""
    from agent_index import sources as sources_mod

    provider = _write_script(tmp_path / "provider.py", "pass\n")
    _write_manifest(tmp_path, "git", source_name="git", command=[sys.executable, str(provider)])
    original_type = type(sources_mod.get_connector("git"))
    report = discover_and_register_providers(tmp_path)
    resolved = sources_mod.get_connector("git")
    assert isinstance(resolved, original_type)
    assert not isinstance(resolved, CliSourceConnector)
    assert any(f.reason == "prefix-collision" for f in report.findings)
    # The collision-skipped manifest must not appear as "active" in the
    # returned report -- manifests and the live registry must stay consistent.
    assert "git" not in report.manifests


def test_discover_and_register_providers_skips_hierarchical_collision(tmp_path):
    """A manifest that would shadow *part of* a built-in family (e.g. 'git:foo'
    under the built-in 'git' prefix) must be rejected too, not just an exact
    source_name match."""
    from agent_index import sources as sources_mod

    provider = _write_script(tmp_path / "provider.py", "pass\n")
    _write_manifest(
        tmp_path, "gitsub", source_name="git:foo", command=[sys.executable, str(provider)]
    )
    report = discover_and_register_providers(tmp_path)
    assert "git:foo" not in sources_mod.registered_source_prefixes()
    assert any(f.reason == "prefix-collision" and f.target == "git:foo" for f in report.findings)


def test_discover_and_register_providers_skips_intra_scan_hierarchical_collision(tmp_path):
    """Two providers in the SAME scan that hierarchically overlap each other
    must not both register -- the second is rejected against the first."""
    from agent_index import sources as sources_mod

    provider = _write_script(tmp_path / "provider.py", "pass\n")
    _write_manifest(
        tmp_path, "a-parent", source_name="myfamily", command=[sys.executable, str(provider)]
    )
    _write_manifest(
        tmp_path, "b-child", source_name="myfamily:sub", command=[sys.executable, str(provider)]
    )
    try:
        report = discover_and_register_providers(tmp_path)
        registered_after = sources_mod.registered_source_prefixes()
        assert "myfamily" in registered_after
        assert "myfamily:sub" not in registered_after
        assert any(
            f.reason == "prefix-collision" and f.target == "myfamily:sub"
            for f in report.findings
        )
    finally:
        sources_mod._CONNECTORS.pop("myfamily", None)
        sources_mod._CONNECTORS.pop("myfamily:sub", None)


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


_ARGV_ECHO_PROVIDER_BODY = """
import json
import sys

args = sys.argv[1:]
source = args[args.index("--source") + 1]
print(json.dumps({"entries": [
    {"path": "argv.txt", "content": " ".join(args), "language": "text", "source": source},
]}))
"""


def test_discover_passes_source_argument(tmp_path):
    provider = _write_script(tmp_path / "provider.py", _ARGV_ECHO_PROVIDER_BODY)
    manifest = ProviderManifest(
        source_name="gitea", command=(sys.executable, str(provider))
    )
    connector = CliSourceConnector("gitea:owner/repo", manifest)
    entries = connector.discover()
    argv = entries[0].content.split()
    assert argv[0] == "content-discover"
    assert "--source" in argv
    assert argv[argv.index("--source") + 1] == "gitea:owner/repo"


def test_run_uses_no_window_kwargs(tmp_path, monkeypatch):
    """The provider subprocess must be spawned with console-suppression kwargs
    (Windows) and explicit UTF-8 decoding, matching every other subprocess call
    site in agent-index."""
    from unittest import mock

    connector = _entry_connector(tmp_path)
    captured: dict = {}

    def _fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout='{"entries": []}', stderr=""
        )

    monkeypatch.setattr(
        "agent_index.sources.providers.no_window_kwargs", lambda: {"creationflags": 999}
    )
    with mock.patch("agent_index.sources.providers.subprocess.run", side_effect=_fake_run):
        connector.discover()
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"
    assert captured.get("creationflags") == 999


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


def test_entry_outside_requested_source_fails_closed(tmp_path):
    """A provider invoked for 'gitea' must not be able to inject content
    attributed to an unrelated source (e.g. 'github:owner/repo')."""
    body = (
        "import json\n"
        'print(json.dumps({"entries": [{"path": "a.md", "content": "x", '
        '"language": "md", "source": "github:owner/repo"}]}))\n'
    )
    connector = _bad_provider_connector(tmp_path, body)
    with pytest.raises(ProviderError, match="outside the requested"):
        connector.discover()


def test_entry_exact_source_match_is_allowed(tmp_path):
    body = (
        "import json\n"
        'print(json.dumps({"entries": [{"path": "a.md", "content": "x", '
        '"language": "md", "source": "gitea"}]}))\n'
    )
    connector = _bad_provider_connector(tmp_path, body)
    assert connector.discover()[0].source == "gitea"


def test_list_paths_outside_requested_source_fails_closed(tmp_path):
    body = 'import json\nprint(json.dumps({"paths": {"github:owner/repo": ["a.md"]}}))\n'
    connector = _bad_provider_connector(tmp_path, body)
    with pytest.raises(ProviderError, match="outside the requested"):
        connector.list_paths()


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


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "0", "-5", "not-a-number", ""])
def test_verb_timeout_rejects_invalid_overrides(monkeypatch, value):
    from agent_index.sources.providers import DEFAULT_VERB_TIMEOUT, _verb_timeout

    monkeypatch.setenv("AGENT_INDEX_PROVIDER_TIMEOUT", value)
    assert _verb_timeout() == DEFAULT_VERB_TIMEOUT


def test_verb_timeout_accepts_valid_override(monkeypatch):
    from agent_index.sources.providers import _verb_timeout

    monkeypatch.setenv("AGENT_INDEX_PROVIDER_TIMEOUT", "12.5")
    assert _verb_timeout() == 12.5


def test_verb_timeout_default_when_unset(monkeypatch):
    from agent_index.sources.providers import DEFAULT_VERB_TIMEOUT, _verb_timeout

    monkeypatch.delenv("AGENT_INDEX_PROVIDER_TIMEOUT", raising=False)
    assert _verb_timeout() == DEFAULT_VERB_TIMEOUT


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
