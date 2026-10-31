"""Negotiated CLI deltas retain strict legacy parser and recovery boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_index.sources.providers import CliSourceConnector, ProviderError, ProviderManifest

FAMILY = "forge:repo"
DECLARATION = {
    "contract": "agent-index.source-delta",
    "versions": [1],
    "family": FAMILY,
    "account": "principal-a",
    "target": "https://forge.example.invalid/api",
}
ENTRY = {
    "source": FAMILY, "path": "new.md", "content": "new", "language": "markdown",
    "metadata": {"revision": "item-2"},
}
DELTA = {
    "version": 1, "upserts": [ENTRY],
    "deletions": [{"source": FAMILY, "path": "gone.md"}],
    "commit": "delta-tip", "complete": True,
}
PROVIDER = '''
import argparse
import json
import sys
from pathlib import Path

root = Path(__file__).parent
config = json.loads((root / "responses.json").read_text())
parser = argparse.ArgumentParser()
parser.add_argument("verb", choices=[
    "content-current-commit", "content-discover", "content-discover-changed",
    "content-list-paths",
])
parser.add_argument("--source", required=True)
parser.add_argument("--last-commit")
if config["new"]:
    parser.add_argument("--delta-version", type=int, choices=[1])
if config.get("snapshot_cli"):
    parser.add_argument("--snapshot-version", type=int, choices=[1])
    parser.add_argument("--snapshot")
if config.get("acquisition_cli"):
    parser.add_argument("--acquisition-page-version", type=int, choices=[1])
if config.get("snapshot_cli") or config.get("acquisition_cli"):
    parser.add_argument("--cursor")
args = parser.parse_args()
declaration = config["metadata"].get("source_delta")
canonical = declaration.get("family", args.source) if isinstance(declaration, dict) else args.source
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if args.verb == "content-current-commit":
    response = config["metadata"]
elif args.verb == "content-list-paths":
    paths = {canonical: ["old.md"]}
    if "snapshot_entries" in config:
        paths = {}
        for entry in config["snapshot_entries"]:
            paths.setdefault(entry["source"], []).append(entry["path"])
    response = {"paths": paths}
elif getattr(args, "snapshot_version", None) is not None:
    if config.get("fail_snapshot") and args.cursor is not None:
        sys.stderr.write("snapshot temporarily unavailable")
        sys.exit(7)
    response = config["snapshot_pages"][args.cursor or "first"]
elif getattr(args, "acquisition_page_version", None) is not None:
    if config.get("fail_acquisition") and args.cursor is not None:
        sys.stderr.write("acquisition temporarily unavailable")
        sys.exit(7)
    if config.get("truncate_acquisition"):
        print('{"acquisition_page_version": 1, "upserts": [')
        sys.exit(0)
    response = config["acquisition_pages"][args.cursor or "first"]
elif getattr(args, "delta_version", None) is not None:
    if config.get("fail_delta"):
        sys.stderr.write("delta refused after acceptance")
        sys.exit(7)
    if config.get("truncate_delta"):
        print('{"version": 1, "upserts": [')
        sys.exit(0)
    response = config["delta"]
else:
    response = {"entries": config.get("snapshot_entries", [{
        "source": canonical, "path": "old.md", "content": "old", "language": "markdown",
    }])}
if args.verb != "content-current-commit" and isinstance(response, dict) and config["new"]:
    delta = getattr(args, "delta_version", None) is not None
    if config.get("echo_scope" if delta else "echo_snapshot_scope", True):
        response = {**response, "source_delta": config.get(
            "delta_scope" if delta else "snapshot_scope", declaration,
        )}
print(json.dumps(response))
'''


class Receiver:
    def __init__(self, root: Path, *, new: bool = True) -> None:
        self.root = root
        self.script = root / "provider.py"
        self.script.write_text(PROVIDER, encoding="utf-8")
        self.config: dict = {
            "new": new,
            "metadata": {"commit": "metadata-tip", **(
                {"source_delta": dict(DECLARATION)} if new else {}
            )},
            "delta": dict(DELTA),
        }
        self.save()

    def save(self) -> None:
        (self.root / "responses.json").write_text(json.dumps(self.config), encoding="utf-8")

    def connector(self, source: str = "forge") -> CliSourceConnector:
        self.save()
        return CliSourceConnector(source, ProviderManifest(
            source_name="forge", command=(sys.executable, str(self.script)),
        ))

    def calls(self) -> list[list[str]]:
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            capture_output=True, text=True, check=False, timeout=10,
            **no_window_kwargs(),
        )


def test_new_caller_legacy_receiver_reconciles_without_new_flags(tmp_path, caplog):
    receiver = Receiver(tmp_path, new=False)
    connector = receiver.connector()
    assert connector.source_name == "forge"
    assert connector.delta_available is False
    assert connector.current_commit() == "metadata-tip"
    assert connector.recovery_identity == ("forge", sys.executable, str(receiver.script))
    assert connector.discover()[0].path == "old.md"
    assert connector.discover_changed("base")[0].path == "old.md"
    assert connector.list_paths() == {"forge": {"old.md"}}
    with pytest.raises(ProviderError, match="not negotiated"):
        connector.discover_delta("base")
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover", "--source", "forge"],
        ["content-discover-changed", "--source", "forge", "--last-commit", "base"],
        ["content-list-paths", "--source", "forge"],
    ]
    assert "reconcil" in caplog.text.lower()


def test_old_caller_new_receiver_retains_four_verb_baseline(tmp_path):
    receiver = Receiver(tmp_path)
    result = receiver.invoke("content-current-commit", "--source", "forge")
    assert result.returncode == 0
    assert json.loads(result.stdout)["commit"] == "metadata-tip"
    for verb in ("content-discover", "content-discover-changed"):
        result = receiver.invoke(verb, "--source", "forge")
        assert result.returncode == 0
        assert json.loads(result.stdout)["entries"][0]["path"] == "old.md"
        assert json.loads(result.stdout)["entries"][0]["source"] == FAMILY
    result = receiver.invoke("content-list-paths", "--source", "forge")
    assert result.returncode == 0
    assert json.loads(result.stdout)["paths"] == {FAMILY: ["old.md"]}
    assert receiver.invoke("content-discover", "--source", "forge", "--typo").returncode == 2


@pytest.mark.parametrize("first", ["source_name", "recovery_identity", "delta_available", "cursor"])
def test_metadata_is_one_snapshot_and_canonical_family_is_stable(tmp_path, first):
    receiver = Receiver(tmp_path)
    connector = receiver.connector()
    if first == "cursor":
        connector.current_commit()
    else:
        getattr(connector, first)
    receiver.config["metadata"]["commit"] = "later"
    receiver.save()
    assert connector.current_commit() == "metadata-tip"
    assert connector.source_name == FAMILY
    assert connector.delta_available is True
    assert connector.source_name == FAMILY
    delta = connector.discover_delta("base")
    assert delta.upserts[0].metadata == {"revision": "item-2"}
    assert delta.deletions[0].path == "gone.md"
    assert delta.commit == "delta-tip"  # Never substitute the metadata probe's cursor.
    delta.validate(FAMILY)
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover-changed", "--source", "forge", "--last-commit", "base",
         "--delta-version", "1"],
    ]


def test_delta_without_base_and_full_reconciliation_remain_separate(tmp_path):
    receiver = Receiver(tmp_path)
    connector = receiver.connector()
    assert connector.discover_delta(None).complete is True
    assert connector.discover()[0].path == "old.md"
    assert connector.discover_changed(None)[0].path == "old.md"
    assert connector.list_paths() == {FAMILY: {"old.md"}}
    assert receiver.calls()[1] == [
        "content-discover-changed", "--source", "forge", "--delta-version", "1",
    ]
    assert all("--delta-version" not in args for args in receiver.calls()[2:])


@pytest.mark.parametrize("mode", ["v1", "legacy", "future"])
@pytest.mark.parametrize("full", [True, False])
def test_collection_and_journal_preserve_canonical_family(tmp_path, mode, full):
    from agent_index.indexing.crawl import collect_source_changes
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
    from agent_index.indexing.state import IndexState, SourceState

    receiver = Receiver(tmp_path, new=mode != "legacy")
    if mode == "future":
        receiver.config["metadata"]["source_delta"]["versions"] = [2]
        receiver.config["echo_snapshot_scope"] = False
    connector = receiver.connector()
    canonical = FAMILY if mode == "v1" else "forge"
    entry_source = FAMILY if mode != "legacy" else "forge"
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed(entry_source, "gone.md", chunk_count=1)
    paths.mark_indexed("other:repo", "outside.md", chunk_count=1)
    state = IndexState(sources={"alias": SourceState(last_commit="base")})
    state.sources["alias"].acquisition_scope = recovery_scope("alias", connector)
    state.stored_families["alias"] = canonical
    manifest = collect_source_changes(
        "alias", state=state, path_index=paths, full=full,
        source_aware=True, connector=connector,
    )
    delta = mode == "v1" and not full
    assert manifest.stored_family == manifest.accounting_family == canonical
    assert [(entry.source, entry.path) for entry in manifest.upserts] == [
        (entry_source, "new.md" if delta else "old.md"),
    ]
    assert [(entry.source, entry.path) for entry in manifest.deletions] == [
        (entry_source, "gone.md"),
    ]
    assert manifest.commit == ("delta-tip" if delta else "metadata-tip")
    assert state.sources["alias"].last_commit == "base"
    journal = PendingCrawls(paths)
    scope = recovery_scope("configuration", connector)
    journal.save(manifest, scope=scope, full=full)
    assert journal.load("alias", scope) == (manifest, full)
    assert paths.get_entry(entry_source, "gone.md") is not None
    assert paths.get_entry("other:repo", "outside.md") is not None
    expected = [["content-current-commit", "--source", "forge"]]
    if full:
        expected.append(["content-discover", "--source", "forge"])
    else:
        if not delta:
            expected.append(["content-list-paths", "--source", "forge"])
        expected.append([
            "content-discover-changed", "--source", "forge", "--last-commit", "base",
            *(["--delta-version", "1"] if delta else []),
        ])
    assert receiver.calls() == expected


@pytest.mark.parametrize("field", ["account", "target"])
def test_full_crawl_retargeting_cannot_accept_tombstones_or_journal(tmp_path, field, monkeypatch):
    from agent_index.indexing.crawl import collect_source_changes
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
    from agent_index.indexing.state import IndexState, SourceState

    receiver = Receiver(tmp_path)
    connector = receiver.connector()
    assert connector.source_name == FAMILY
    scope = recovery_scope("configuration", connector)
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed(FAMILY, "gone.md", chunk_count=1)
    journal = PendingCrawls(paths)
    state = IndexState(sources={"alias": SourceState(last_commit="base")})
    receiver.config["metadata"]["source_delta"][field] += ":retargeted"
    receiver.save()
    parsed = []
    original_parser = connector._entries

    def record_entries(data, verb):
        parsed.append(data)
        return original_parser(data, verb)

    monkeypatch.setattr(connector, "_entries", record_entries)
    with pytest.raises(ProviderError, match="scope"):
        manifest = collect_source_changes(
            "alias", state=state, path_index=paths, full=True,
            source_aware=True, connector=connector,
        )
        journal.save(manifest, scope=scope, full=True)
    assert parsed == []
    assert journal.sources() == []
    assert paths.get_entry(FAMILY, "gone.md") is not None
    assert state.sources["alias"].last_commit == "base"
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover", "--source", "forge"],
    ]


def _snapshot_operation(connector, operation):
    method = getattr(connector, operation)
    return method("base") if operation == "discover_changed" else method()


@pytest.mark.parametrize("operation", ["discover", "discover_changed", "list_paths"])
@pytest.mark.parametrize("echo", [
    "missing", {"versions": [2]}, {"contract": "other"},
    {"family": "forge:repo:other"}, {"account": "principal-b"},
    {"target": "https://other.example.invalid/api"},
])
def test_negotiated_snapshot_operations_require_matching_echo(tmp_path, operation, echo):
    receiver = Receiver(tmp_path)
    if echo == "missing":
        receiver.config["echo_snapshot_scope"] = False
    else:
        receiver.config["snapshot_scope"] = {**DECLARATION, **echo}
    connector = receiver.connector()
    assert connector.delta_available
    with pytest.raises(ProviderError):
        _snapshot_operation(connector, operation)
    assert len(receiver.calls()) == 2
    assert receiver.calls()[-1][0] == "content-" + operation.replace("_", "-")
    assert all("--delta-version" not in args for args in receiver.calls())


@pytest.mark.parametrize("operation", ["discover", "discover_changed", "list_paths"])
def test_matching_negotiated_snapshot_echo_preserves_old_syntax(tmp_path, operation):
    receiver = Receiver(tmp_path)
    connector = receiver.connector()
    assert connector.source_name == FAMILY
    result = _snapshot_operation(connector, operation)
    if operation == "list_paths":
        assert result == {FAMILY: {"old.md"}}
    else:
        assert result[0].source == FAMILY
    assert len(receiver.calls()) == 2
    assert all("--delta-version" not in args for args in receiver.calls())


@pytest.mark.parametrize("operation", ["discover", "discover_changed", "list_paths"])
@pytest.mark.parametrize("new", [True, False])
def test_unnegotiated_snapshot_calls_do_not_probe_or_require_echo(tmp_path, operation, new):
    receiver = Receiver(tmp_path, new=new)
    receiver.config["echo_snapshot_scope"] = False
    connector = receiver.connector()
    assert _snapshot_operation(connector, operation)
    assert len(receiver.calls()) == 1
    assert receiver.calls()[0][0] == "content-" + operation.replace("_", "-")
    assert "--delta-version" not in receiver.calls()[0]


@pytest.mark.parametrize("field", ["account", "target"])
def test_runtime_retarget_after_negotiation_is_refused_before_return(tmp_path, field, monkeypatch):
    receiver = Receiver(tmp_path)
    connector = receiver.connector()
    assert connector.current_commit() == "metadata-tip"
    original_identity = connector.recovery_identity
    receiver.config["metadata"]["source_delta"][field] += ":retargeted"
    receiver.save()
    parsed = []
    original_parser = connector._entries

    def record_entries(data, verb):
        parsed.append(data)
        return original_parser(data, verb)

    monkeypatch.setattr(connector, "_entries", record_entries)
    with pytest.raises(ProviderError, match="scope"):
        connector.discover_delta("base")
    assert parsed == []
    assert connector.source_name == FAMILY
    assert connector.recovery_identity == original_identity
    assert receiver.calls() == [
        ["content-current-commit", "--source", "forge"],
        ["content-discover-changed", "--source", "forge", "--last-commit", "base",
         "--delta-version", "1"],
    ]


@pytest.mark.parametrize(("field", "value"), [
    ("contract", "other"), ("versions", [2]), ("versions", [True]),
    ("family", "forge:repo:other"), ("family", "forge:other"),
    ("account", "principal-b"), ("target", "https://other.example.invalid/api"),
])
def test_delta_scope_echo_must_match_negotiated_contract_and_scope(tmp_path, field, value):
    receiver = Receiver(tmp_path)
    receiver.config["delta_scope"] = {**DECLARATION, field: value}
    with pytest.raises(ProviderError):
        receiver.connector().discover_delta("base")
    assert len(receiver.calls()) == 2
    assert "--delta-version" in receiver.calls()[-1]


@pytest.mark.parametrize("field", ["contract", "versions", "family", "account", "target"])
def test_delta_scope_echo_requires_complete_declaration(tmp_path, field):
    receiver = Receiver(tmp_path)
    receiver.config["delta_scope"] = {key: value for key, value in DECLARATION.items() if key != field}
    with pytest.raises(ProviderError):
        receiver.connector().discover_delta(None)
    assert len(receiver.calls()) == 2


@pytest.mark.parametrize("value", [None, [], "scope"])
def test_delta_scope_echo_requires_object(tmp_path, value):
    receiver = Receiver(tmp_path)
    receiver.config["delta_scope"] = value
    with pytest.raises(ProviderError):
        receiver.connector().discover_delta(None)
    assert len(receiver.calls()) == 2


def test_delta_response_without_scope_echo_never_retries_legacy(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["echo_scope"] = False
    with pytest.raises(ProviderError, match="source_delta"):
        receiver.connector().discover_delta("base")
    assert len(receiver.calls()) == 2
    assert "--delta-version" in receiver.calls()[-1]


def test_matching_scope_echo_can_narrow_support_to_negotiated_v1(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["versions"] = [2, 1]
    receiver.config["delta_scope"] = {**DECLARATION, "versions": [1]}
    assert receiver.connector().discover_delta(None).commit == "delta-tip"
    assert len(receiver.calls()) == 2


def test_unknown_valid_versions_select_visible_legacy_reconciliation(tmp_path, caplog):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["versions"] = [2]
    connector = receiver.connector()
    assert connector.delta_available is False
    assert connector.source_name == "forge"
    assert connector.discover_changed("base")[0].path == "old.md"
    assert "reconcil" in caplog.text.lower()
    assert "version" in caplog.text.lower()
    assert all("--delta-version" not in args for args in receiver.calls())


def test_supported_version_is_selected_from_explicit_set(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["versions"] = [2, 1]
    connector = receiver.connector()
    assert connector.delta_available is True
    assert connector.discover_delta(None).version == 1


@pytest.mark.parametrize(("field", "value"), [
    ("contract", "other"), ("versions", []), ("versions", [True]), ("versions", [0]),
    ("versions", [1, 1]), ("versions", "1"), ("family", "other:repo"),
    ("family", "forge:"), ("family", " forge:repo"), ("family", ""),
    ("account", ""), ("account", None), ("target", ""), ("target", 3),
    ("target", "https://user:pass@example.invalid/api"),
    ("target", "https://example.invalid/api?token=opaque"),
])
def test_invalid_declaration_refuses_before_acquisition(tmp_path, field, value):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["source_delta"][field] = value
    connector = receiver.connector()
    with pytest.raises(ProviderError, match="source_delta"):
        connector.discover_delta(None)
    assert receiver.calls() == [["content-current-commit", "--source", "forge"]]


@pytest.mark.parametrize("field", ["contract", "versions", "family", "account", "target"])
def test_declaration_fields_cannot_be_implicitly_defaulted(tmp_path, field):
    receiver = Receiver(tmp_path)
    del receiver.config["metadata"]["source_delta"][field]
    with pytest.raises(ProviderError, match="source_delta"):
        receiver.connector().current_commit()


def test_family_cannot_expand_invoked_subtree(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["family"] = "forge"
    with pytest.raises(ProviderError, match="source_delta"):
        receiver.connector(FAMILY).source_name


@pytest.mark.parametrize("field", ["target", "account", "family"])
def test_retargeting_changes_recovery_scope_for_same_command(tmp_path, field):
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope

    receiver = Receiver(tmp_path)
    connector = receiver.connector()
    original = connector.recovery_identity
    scope = recovery_scope("configuration", connector)
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    journal.save(
        CrawlManifest(source="alias", stored_family=FAMILY), scope=scope, full=False,
    )
    receiver.config["metadata"]["source_delta"][field] += ":different"
    retargeted = receiver.connector()
    changed = retargeted.recovery_identity
    assert original != changed
    assert receiver.connector().recovery_identity == changed
    with pytest.raises(ValueError, match="different source/account"):
        journal.load("alias", recovery_scope("configuration", retargeted))
    assert journal.load("alias", scope) is not None


def test_command_and_requested_subtree_are_in_recovery_scope(tmp_path):
    receiver = Receiver(tmp_path)
    first = receiver.connector()
    manifest = ProviderManifest("forge", (sys.executable, "-u", str(receiver.script)))
    assert first.recovery_identity != CliSourceConnector("forge", manifest).recovery_identity
    assert first.recovery_identity != receiver.connector(FAMILY).recovery_identity


def test_provider_rollback_does_not_reuse_cached_capability_across_connectors(tmp_path):
    receiver = Receiver(tmp_path)
    prior = receiver.connector().recovery_identity
    receiver.config["new"] = False
    receiver.config["metadata"] = {"commit": None}
    rollback = receiver.connector()
    assert rollback.delta_available is False
    assert rollback.recovery_identity != prior
    assert rollback.current_commit() is None
    assert rollback.discover_changed("base")[0].path == "old.md"


@pytest.mark.parametrize("field", ["version", "upserts", "deletions", "commit", "complete"])
def test_delta_requires_all_fields(tmp_path, field):
    receiver = Receiver(tmp_path)
    del receiver.config["delta"][field]
    with pytest.raises(ProviderError):
        receiver.connector().discover_delta(None)
    assert len(receiver.calls()) == 2


@pytest.mark.parametrize(("field", "value"), [
    ("version", True), ("version", 2), ("upserts", None), ("deletions", {}),
    ("commit", 1), ("complete", False), ("complete", 1),
    ("upserts", [{**ENTRY, "content": 1}]),
    ("upserts", [{**ENTRY, "metadata": []}]),
    ("upserts", [{**ENTRY, "source": "forge:other"}]),
    ("upserts", [{**ENTRY, "source": "outside"}]),
    ("upserts", [{**ENTRY, "path": ""}]),
    ("upserts", [ENTRY, ENTRY]),
    ("deletions", [{"source": FAMILY, "path": "new.md"}]),
    ("deletions", [{"source": FAMILY, "path": ""}]),
    ("deletions", [{"source": "forge:other", "path": "gone.md"}]),
    ("deletions", [{"source": FAMILY}]),
    ("deletions", [{"source": FAMILY, "path": 1}]),
    ("deletions", ["gone.md"]),
    ("deletions", [{"source": FAMILY, "path": "gone.md"}] * 2),
])
def test_invalid_delta_refuses_without_legacy_retry(tmp_path, field, value):
    receiver = Receiver(tmp_path)
    receiver.config["delta"][field] = value
    with pytest.raises(ProviderError):
        receiver.connector().discover_delta("base")
    assert len(receiver.calls()) == 2
    assert "--delta-version" in receiver.calls()[-1]


@pytest.mark.parametrize("failure", ["fail_delta", "truncate_delta"])
def test_process_failure_never_replays_legacy_discovery(tmp_path, failure):
    receiver = Receiver(tmp_path)
    receiver.config[failure] = True
    with pytest.raises(ProviderError, match="exited 7|malformed JSON"):
        receiver.connector().discover_delta("base")
    assert len(receiver.calls()) == 2


def test_empty_complete_delta_does_not_infer_tombstones_from_metadata(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["delta"] = {
        "version": 1, "upserts": [], "deletions": [], "commit": None, "complete": True,
    }
    delta = receiver.connector().discover_delta(None)
    assert delta.deletions == delta.upserts == []
    assert delta.commit is None
    assert len(receiver.calls()) == 2


def test_declared_subtree_upserts_and_tombstones_are_valid(tmp_path):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["extra"] = {"future": True}
    receiver.config["delta"]["upserts"] = [{**ENTRY, "source": FAMILY + ":issues"}]
    receiver.config["delta"]["deletions"] = [{"source": FAMILY + ":issues", "path": "old"}]
    delta = receiver.connector().discover_delta("base")
    assert delta.upserts[0].source == delta.deletions[0].source == FAMILY + ":issues"


@pytest.mark.parametrize("value", [None, [], "v1"])
def test_nonobject_optional_declaration_is_invalid(tmp_path, value):
    receiver = Receiver(tmp_path)
    receiver.config["metadata"]["source_delta"] = value
    with pytest.raises(ProviderError, match="source_delta"):
        receiver.connector().source_name


@pytest.mark.parametrize("value", [None, [], {"entries": []}])
def test_non_delta_responses_are_not_treated_as_empty(tmp_path, value):
    receiver = Receiver(tmp_path)
    receiver.config["delta"] = value
    with pytest.raises(ProviderError, match="requires"):
        receiver.connector().discover_delta(None)
    assert len(receiver.calls()) == 2


@pytest.mark.parametrize("phase", ["before", "metadata", "acquisition"])
def test_cancellation_checked_around_each_process(tmp_path, phase):
    receiver = Receiver(tmp_path)
    connector = receiver.connector()

    def cancel() -> None:
        calls = receiver.calls()
        if phase == "before" or len(calls) >= (1 if phase == "metadata" else 2):
            raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError, match="cancelled"):
        connector.discover_delta(None, cancel_check=cancel)
    assert len(receiver.calls()) == {"before": 0, "metadata": 1, "acquisition": 2}[phase]


@pytest.mark.parametrize("operation", ["discover", "discover_changed", "list_paths"])
def test_legacy_cancellation_after_process(tmp_path, operation):
    receiver = Receiver(tmp_path, new=False)
    connector = receiver.connector()

    def cancel() -> None:
        if receiver.calls():
            raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError, match="cancelled"):
        method = getattr(connector, operation)
        if operation == "discover_changed":
            method(None, cancel_check=cancel)
        else:
            method(cancel_check=cancel)
    assert len(receiver.calls()) == 1
