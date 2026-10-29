"""Reset inventory persists when independently checked reuse evidence is unusable."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
from typing import Any

import pytest

import accumulate_bumps
import install_footprint_contract as guard
import materialize_main


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "plugins/agent-mcp"
PATH = PAYLOAD / "scripts/uninstall-profile.json"
SPEC = importlib.util.spec_from_file_location(
    "footprint_test_subject", ROOT / "libs/installer-engine/installer_engine_footprint.py",
)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subject
SPEC.loader.exec_module(subject)


def _record() -> dict[str, Any]:
    return subject.decode(PATH.read_text(encoding="utf-8"))


def _seed(root: Path) -> None:
    slot = root / "versions/1.2.3"
    slot.mkdir(parents=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        '{"schema_version":3,"service":"agent-mcp"}', encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(
        '{"version":"1.2.3","completed_at":"fixture-time","pid":123}', encoding="utf-8",
    )


@pytest.mark.parametrize("condition", (
    "cold", "current", "missing-deployment", "corrupt-deployment", "duplicate-fields",
    "foreign-service", "unsupported-schema", "boolean-schema", "traversal-selection",
    "missing-completion", "foreign-completion", "boolean-pid", "unknown-revision",
))
def test_recognition_is_read_only_and_never_discards_reset_inventory(
    tmp_path: Path, condition: str,
) -> None:
    root = tmp_path / "runtime"
    record = _record()
    manifest = subject.parse(record, "agent-mcp")
    expected = deepcopy(manifest.profile)
    if condition != "cold":
        _seed(root)
    changes = {
        "corrupt-deployment": ("deploy-manifest.json", "{"),
        "duplicate-fields": ("deploy-manifest.json", '{"schema_version":3,"schema_version":3,"service":"agent-mcp"}'),
        "foreign-service": ("deploy-manifest.json", '{"schema_version":3,"service":"other"}'),
        "unsupported-schema": ("deploy-manifest.json", '{"schema_version":4,"service":"agent-mcp"}'),
        "boolean-schema": ("deploy-manifest.json", '{"schema_version":true,"service":"agent-mcp"}'),
        "traversal-selection": ("current-version", "../other"),
        "foreign-completion": ("versions/1.2.3/.install-complete.json", '{"version":"9","completed_at":"","pid":1}'),
        "boolean-pid": ("versions/1.2.3/.install-complete.json", '{"version":"1.2.3","completed_at":"","pid":true}'),
    }
    if condition in changes:
        name, content = changes[condition]
        (root / name).write_text(content, encoding="utf-8")
    if condition.startswith("missing-"):
        name = "deploy-manifest.json" if condition == "missing-deployment" else "versions/1.2.3/.install-complete.json"
        (root / name).unlink()
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = subject.recognize(manifest, root, footprint_revision=999 if condition == "unknown-revision" else 1)
    assert result.matched is (condition == "current")
    assert manifest.profile == expected
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert root.exists() is (condition != "cold")


def test_historical_match_is_not_compared_to_the_incoming_revision(tmp_path: Path) -> None:
    record = _record()
    previous = record["history"][-1]["snapshot"]
    incoming = deepcopy(previous)
    incoming["profile"]["root_entries"].append("new-generated-file")
    incoming["profile"]["bin_entries"].remove("resolve-runtime.ps1")
    incoming["effects"]["runtime-control"]["entries"].append("new-control")
    change = subject.delta(previous, incoming)
    assert change["introduced"] == ["root_entries:new-generated-file"]
    assert change["retired"] == ["bin_entries:resolve-runtime.ps1"]
    assert change["changed"] == ["effects:runtime-control"]
    revision = len(record["history"]) + 1
    record["history"].append({"revision": revision, "snapshot": incoming, "delta": change})
    subject.require_append_only(_record(), record, "agent-mcp")
    manifest = subject.parse(record, "agent-mcp")
    _seed(tmp_path)
    assert manifest.revision == revision
    assert "resolve-runtime.ps1" in manifest.history[0]["profile"]["bin_entries"]
    assert "resolve-runtime.ps1" not in manifest.profile["bin_entries"]
    assert subject.recognize(manifest, tmp_path).matched
    assert not subject.recognize(manifest, tmp_path, footprint_revision=revision).matched


@pytest.mark.parametrize("condition", ("cold", "compatible", "missing", "corrupt", "conflict"))
def test_construction_plan_uses_fresh_without_rewriting_unusable_inputs(
    tmp_path: Path, condition: str,
) -> None:
    root = tmp_path / "runtime"
    if condition != "cold":
        _seed(root)
    if condition == "missing":
        (root / "deploy-manifest.json").unlink()
    if condition == "corrupt":
        (root / "versions/1.2.3/.install-complete.json").write_bytes(b"{")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    calls = []

    def admission(observed: Path, version: str) -> str:
        calls.append((observed, version))
        return "content-conflict" if condition == "conflict" else "reuse"

    plan = subject.plan_construction(subject.parse(_record(), "agent-mcp"), root, "1.2.3", admission=admission)
    assert plan.mode == ("incremental" if condition == "compatible" else "fresh")
    if condition in {"cold", "compatible"}:
        assert plan.version == "1.2.3"
    else:
        assert plan.version.startswith("1.2.3+repair.")
        assert not os.path.lexists(root / "versions" / plan.version)
    assert bool(calls) is (condition in {"compatible", "conflict"})
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_recognized_replacement_reuses_the_same_qualified_target(tmp_path: Path) -> None:
    _seed(tmp_path)
    selected = "1.2.3+repair." + "a" * 32
    (tmp_path / "versions/1.2.3").rename(tmp_path / "versions" / selected)
    (tmp_path / "current-version").write_text(selected, encoding="utf-8")
    marker = tmp_path / "versions" / selected / ".install-complete.json"
    record = json.loads(marker.read_text())
    record["version"] = selected
    marker.write_text(json.dumps(record), encoding="utf-8")
    plan = subject.plan_construction(
        subject.parse(_record(), "agent-mcp"), tmp_path, "1.2.3",
        admission=lambda root, version: "reuse" if version == selected else "health-repair-required",
    )
    assert plan.version == selected and plan.mode == "incremental"


def test_occupied_incomplete_fallback_selects_distinct_construction(tmp_path: Path) -> None:
    _seed(tmp_path)
    target = tmp_path / "versions/1.2.2"
    target.mkdir()
    (target / "unfinished").write_bytes(b"keep")
    (tmp_path / "last-known-good").write_text("1.2.2", encoding="utf-8")
    plan = subject.plan_construction(
        subject.parse(_record(), "agent-mcp"), tmp_path, "1.2.2",
        admission=lambda root, version: "construct",
    )
    assert plan.mode == "fresh" and plan.version.startswith("1.2.2+repair.")
    assert not (tmp_path / "versions" / plan.version).exists()
    assert (target / "unfinished").read_bytes() == b"keep"


@pytest.mark.parametrize("condition", ("oversized", "hardlinked", "symlinked"))
def test_unusable_marker_paths_select_no_match(tmp_path: Path, condition: str) -> None:
    root = tmp_path / "runtime"
    _seed(root)
    path = root / "deploy-manifest.json"
    if condition == "oversized":
        path.write_text(" " * 65537, encoding="utf-8")
    elif condition == "hardlinked":
        os.link(path, tmp_path / "shared-marker")
    else:
        target = tmp_path / "redirected-marker"
        path.replace(target)
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("native symlink creation unavailable")
    manifest = subject.parse(_record(), "agent-mcp")
    inventory = deepcopy(manifest.profile)
    assert not subject.recognize(manifest, root).matched
    assert manifest.profile == inventory


@pytest.mark.parametrize("mutation", ("omitted-delta", "rewritten-history", "unsafe-producer", "disposable-data"))
def test_invalid_history_or_protection_is_rejected(mutation: str) -> None:
    old = _record()
    record = deepcopy(old)
    snapshot = record["history"][0]["snapshot"]
    if mutation == "omitted-delta":
        snapshot["profile"]["root_entries"].append("new-file")
    elif mutation == "rewritten-history":
        snapshot["profile"]["root_entries"].append("new-file")
        record["history"][0]["delta"] = subject.delta(None, snapshot)
    elif mutation == "unsafe-producer":
        snapshot["producers"]["../outside.py"] = "0" * 64
        record["history"][0]["delta"] = subject.delta(None, snapshot)
    else:
        snapshot["effects"]["configuration-and-data"]["disposition"] = "reset"
        record["history"][0]["delta"] = subject.delta(None, snapshot)
    with pytest.raises(subject.FootprintError):
        if mutation == "rewritten-history":
            subject.require_append_only(old, record, "agent-mcp")
        else:
            subject.parse(record, "agent-mcp")


@pytest.mark.parametrize("change", ("edited-installer", "new-producer"))
def test_producer_output_change_requires_manifest_and_delta(tmp_path: Path, change: str) -> None:
    manifest = subject.parse(_record(), "agent-mcp")
    for name in manifest.producers:
        target = tmp_path / name
        shutil.copytree(PAYLOAD / name, target, ignore=shutil.ignore_patterns("__pycache__"))
    assert subject.producer_problems(manifest, tmp_path) == []
    if change == "edited-installer":
        path = tmp_path / "scripts/init.sh"
        path.write_text(path.read_text(encoding="utf-8") + '\nmkdir -p "$INSTALL_DIR/untracked-runtime"\n', encoding="utf-8")
        scope = "scripts"
    else:
        (tmp_path / "src/untracked_producer.py").write_text("new_footprint = True\n", encoding="utf-8")
        scope = "src"
    assert subject.producer_problems(manifest, tmp_path) == [
        f"{scope}: producer changed without its footprint revision/delta",
    ]


def test_native_mcp_adapter_consumes_the_manifest_profile() -> None:
    spec = importlib.util.spec_from_file_location("mcp_footprint_adapter_test", PAYLOAD / "scripts/uninstall_runtime.py")
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    profile = adapter._profile()
    expected = subject.parse(_record(), "agent-mcp").profile
    assert profile.root_entries == tuple(expected["root_entries"])
    assert profile.bin_entries == tuple(expected["bin_entries"])
    assert profile.local_bin_entries == tuple(expected["local_bin_entries"])


@pytest.mark.parametrize("unusable", ["completion", "selector"])
def test_fresh_plan_runs_the_existing_constructor_and_keeps_broken_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unusable: str,
) -> None:
    root = tmp_path / "runtime"
    _seed(root)
    old = root / ("versions/1.2.3/.install-complete.json" if unusable == "completion" else "current-version")
    old.write_bytes(b"{")
    selection = (root / "current-version").read_bytes()
    spec = importlib.util.spec_from_file_location("footprint_constructor_test", PAYLOAD / "scripts/versioned_runtime.py")
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"command": "complete", "payload_hash": "a" * 64}) + "\n"
        + '{"command":"settled"}\n',
    ))
    assert runtime.construction_session(
        root, "1.2.3", payload_hash="a" * 64, footprint=PATH, plugin="agent-mcp",
    ) == 0
    fresh = [path for path in (root / "versions").iterdir() if path.name != "1.2.3"]
    assert len(fresh) == 1
    assert runtime.is_complete(root, fresh[0].name, expect_hash="a" * 64)
    assert old.read_bytes() == b"{"
    assert (root / "current-version").read_bytes() == selection


@pytest.mark.parametrize("unusable", ["directory", "oversize"])
def test_footprint_constructor_skips_unusable_fallback_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unusable: str,
) -> None:
    root = tmp_path / "runtime"
    _seed(root)
    fallback = root / "versions/1.2.2/.install-complete.json"
    fallback.parent.mkdir()
    if unusable == "directory":
        fallback.mkdir()
    else:
        fallback.write_bytes(b" " * 65537)
    (root / "last-known-good").write_text("1.2.2", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fallback_constructor_test", PAYLOAD / "scripts/versioned_runtime.py")
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"command": "complete", "payload_hash": "a" * 64}) + "\n"
        + '{"command":"settled"}\n',
    ))
    assert runtime.construction_session(
        root, "1.2.4", payload_hash="a" * 64, footprint=PATH, plugin="agent-mcp",
    ) == 0
    assert runtime.is_complete(root, "1.2.4", expect_hash="a" * 64)
    assert (root / "last-known-good").read_text() == "1.2.2"
    assert fallback.is_dir() if unusable == "directory" else fallback.stat().st_size == 65537


@pytest.mark.parametrize("damage", ["oversized-marker", "invalid-config", "invalid-path"])
def test_actual_admission_repairs_noncurrent_target_without_changing_incumbents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    root = tmp_path / "runtime"
    _seed(root)
    desired = root / "versions/1.2.4"
    desired.mkdir()
    marker = desired / ".install-complete.json"
    content = json.dumps({
        "version": "1.2.4", "completed_at": "fixture", "pid": 1, "payload_hash": "a" * 64,
    }).encode()
    marker.write_bytes(content + b" " * (65536 - len(content)))
    spec = importlib.util.spec_from_file_location("damaged_target_test", PAYLOAD / "scripts/versioned_runtime.py")
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    assert runtime.check_admission(root, "1.2.4", payload_hash="a" * 64) == runtime.ADMIT_REUSE
    if damage == "oversized-marker":
        marker.write_bytes(marker.read_bytes() + b" ")
        assert not runtime.is_complete(root, "1.2.4")
    elif damage == "invalid-config":
        (desired / "pyvenv.cfg").write_bytes(b"\xff")
    else:
        site = desired / "Lib/site-packages"
        site.mkdir(parents=True)
        (site / "invalid.pth").write_bytes(b"\xff")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert runtime.check_admission(root, "1.2.4", payload_hash="a" * 64) == runtime.ADMIT_HEALTH_REPAIR_REQUIRED
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"command": "complete", "payload_hash": "a" * 64}) + "\n"
        + '{"command":"settled"}\n',
    ))
    assert runtime.construction_session(
        root, "1.2.4", payload_hash="a" * 64, footprint=PATH, plugin="agent-mcp",
    ) == 0
    fresh = [p for p in (root / "versions").iterdir() if p.name not in {"1.2.3", "1.2.4"}]
    assert len(fresh) == 1 and fresh[0].name.startswith("1.2.4+repair.")
    assert runtime.is_complete(root, fresh[0].name, expect_hash="a" * 64)
    assert {name: (root / name).read_bytes() for name in before} == before


def test_generated_package_metadata_is_not_a_source_producer(tmp_path: Path) -> None:
    source = tmp_path / "src/agent_mcp"
    source.mkdir(parents=True)
    (source / "module.py").write_text("value = 1\n", encoding="utf-8")
    expected = subject.producer_digest(tmp_path, "src")
    metadata = tmp_path / "src/agent_mcp.egg-info"
    metadata.mkdir()
    (metadata / "PKG-INFO").write_text("generated development metadata\n", encoding="utf-8")
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-312.pyc").write_bytes(b"generated bytecode")
    (source / "module.pyc").write_bytes(b"legacy bytecode")
    assert subject.producer_digest(tmp_path, "src") == expected


def test_promoted_mcp_materialization_preserves_footprint_coverage(tmp_path: Path) -> None:
    payload = tmp_path / "plugins/agent-mcp"
    shutil.copytree(PAYLOAD, payload, ignore=materialize_main._ignore)
    manifest = subject.parse(_record(), "agent-mcp")
    assert subject.producer_problems(manifest, payload) == []
    version = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert accumulate_bumps._write_source_fallbacks(payload, version, "99.0.0") == 1
    log = materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=PAYLOAD, dest_consumer_dir=payload,
        canonical_root=ROOT, dest_root=tmp_path,
    )
    assert not any(line.startswith("SKIP") for line in log), log
    assert subject.producer_problems(manifest, payload) == []
    source = payload / "src/agent_mcp/__init__.py"
    source.write_text(source.read_text(encoding="utf-8") + "\nnew_producer = True\n", encoding="utf-8")
    assert subject.producer_problems(manifest, payload) == [
        "src: producer changed without its footprint revision/delta",
    ]


def test_existing_install_contract_includes_footprint_guard() -> None:
    spec = importlib.util.spec_from_file_location("footprint_install_guard_test", ROOT / "tools/check-install-contract.py")
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.check_footprints is guard.check
    assert guard.check(ROOT) == []


def test_source_guard_checks_published_history(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = _record()
    previous["history"][0]["snapshot"]["effects"]["runtime-control"]["entries"].append("lost-old-control")
    previous["history"][0]["delta"] = subject.delta(None, previous["history"][0]["snapshot"])
    for index in range(1, len(previous["history"])):
        previous["history"][index]["delta"] = subject.delta(
            previous["history"][index - 1]["snapshot"], previous["history"][index]["snapshot"],
        )
    monkeypatch.setattr(guard, "_baseline", lambda *args: json.dumps(previous))
    problems = guard.check(ROOT)
    assert len(problems) == 1
    assert "append-only" in problems[0]


def test_changed_producer_scope_cannot_hide_new_output(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record()
    record["history"][-1]["snapshot"]["producers"] = {"scripts/init.sh": "0" * 64}
    record["history"][-1]["delta"] = subject.delta(
        record["history"][-2]["snapshot"] if len(record["history"]) > 1 else None,
        record["history"][-1]["snapshot"],
    )
    original = Path.read_text
    monkeypatch.setattr(
        Path, "read_text",
        lambda path, *args, **kwargs: json.dumps(record) if path == PATH else original(path, *args, **kwargs),
    )
    problems = guard.check(ROOT)
    assert len(problems) == 1
    assert "complete scripts and src trees" in problems[0]


@pytest.mark.parametrize("event", ("local", "pull_request", "push"))
def test_history_baseline_tracks_publication_not_unpublished_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str,
) -> None:
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    if event == "pull_request":
        monkeypatch.setenv("GITHUB_BASE_REF", "dev")
    calls = []

    def git(repo: Path, *arguments: str) -> str:
        assert repo == tmp_path
        calls.append(arguments)
        if arguments[0] == "merge-base":
            return "a" * 40 + "\n"
        if arguments[0] == "ls-tree":
            return "existing manifest\0"
        if arguments[0] == "show":
            return "previous published manifest"
        return "verified\n"

    monkeypatch.setattr(guard, "_git", git)
    assert guard._baseline(tmp_path, "manifest.json") == "previous published manifest"
    expected = {"local": "a" * 40, "pull_request": "refs/remotes/origin/dev", "push": "HEAD^"}[event]
    assert ("show", f"{expected}:manifest.json") in calls
    assert (calls[0] == ("merge-base", "HEAD", "refs/remotes/origin/dev")) is (event == "local")
