"""Reset inventory persists when independently checked reuse evidence is unusable."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from importlib.machinery import SourceFileLoader
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
LEGACY = ROOT / "tools/fixtures/installed-footprint-v2.json"
LEGACY_READER = ROOT / "tools/fixtures/installed_footprint_v2_reader.py.fixture"
SPEC = importlib.util.spec_from_file_location(
    "footprint_test_subject", ROOT / "libs/installer-engine/installer_engine_footprint.py",
)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subject
SPEC.loader.exec_module(subject)


def _record() -> dict[str, Any]:
    return subject.decode(PATH.read_text(encoding="utf-8"))


def _legacy_record() -> dict[str, Any]:
    return subject.decode(LEGACY.read_text(encoding="utf-8"))


def _assess(previous: dict[str, Any], current: dict[str, Any]) -> None:
    current["revision"] = previous["revision"] + 1
    change = subject.contract_delta(
        subject.parse(previous, "agent-mcp"), subject.parse(current, "agent-mcp"),
    )
    current["change"] = {
        "from_revision": previous["revision"], "delta": change,
        "dispositions": {
            key: {"action": "preserve-unknown", "reason": "Support retired; uncertain remnants remain."}
            for key in change["retired"]
        },
    }


def test_current_document_preserves_real_v2_effective_contract() -> None:
    old = subject.parse(_legacy_record(), "agent-mcp")
    current = subject.parse(_record(), "agent-mcp")
    assert old.serialization_version == 2 and current.serialization_version == 3
    assert old.revision == current.revision == 36
    assert old.profile == current.profile
    assert old.effects == current.effects
    assert old.recognizers == current.recognizers
    assert subject.contract_delta(old, current) == {"introduced": [], "changed": [], "retired": []}
    subject.require_transition(_legacy_record(), _record(), "agent-mcp")
    assert PATH.stat().st_size < 65536 < LEGACY.stat().st_size


def test_actual_old_reader_remains_paired_with_its_old_document() -> None:
    loader = SourceFileLoader("legacy_footprint_reader", str(LEGACY_READER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    loader.exec_module(old)
    before = LEGACY.read_bytes()
    manifest = old.parse(old.decode(old._manifest_text(LEGACY)), "agent-mcp")
    assert manifest.profile == subject.parse(_record(), "agent-mcp").profile
    assert len(manifest.history) == 36
    with pytest.raises(old.FootprintError, match="Expected footprint fields"):
        old.parse(_record(), "agent-mcp")
    assert LEGACY.read_bytes() == before


@pytest.mark.parametrize("document", ["v1", "v2", "v3"])
def test_actual_uninstall_inventory_supports_document_upgrade_and_rollback(
    tmp_path: Path, document: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(PAYLOAD / "scripts/installer_engine_footprint.py", scripts / "installer_engine_footprint.py")
    shutil.copyfile(PAYLOAD / "scripts/installer_engine_uninstall.py", scripts / "installer_engine_uninstall.py")
    path = scripts / "uninstall-profile.json"
    expected = subject.parse(_record(), "agent-mcp").profile
    record = {"v1": expected, "v2": _legacy_record(), "v3": _record()}[document]
    path.write_text(json.dumps(record), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "upgrade_uninstall_inventory", scripts / "installer_engine_uninstall.py",
    )
    assert spec is not None and spec.loader is not None
    inventory = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = inventory
    spec.loader.exec_module(inventory)
    before = path.read_bytes()
    profile = inventory.profile_for(payload, "agent-mcp", path)
    assert profile.root_entries == tuple(expected["root_entries"])
    assert profile.bin_entries == tuple(expected["bin_entries"])
    assert path.read_bytes() == before
    path.write_text(before.decode("utf-8").replace('{"version":', '{"version":1,"version":', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate footprint field"):
        inventory.profile_for(payload, "agent-mcp", path)


@pytest.mark.parametrize("document", ["v2", "v3"])
def test_actual_constructor_loads_only_the_same_payload_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: str,
) -> None:
    scripts = tmp_path / "payload/scripts"
    scripts.mkdir(parents=True)
    for source in (PAYLOAD / "scripts").glob("versioned_runtime*.py"):
        shutil.copyfile(source, scripts / source.name)
    shutil.copyfile(PAYLOAD / "scripts/installer_engine_footprint.py", scripts / "installer_engine_footprint.py")
    declaration = scripts / "uninstall-profile.json"
    declaration.write_bytes(LEGACY.read_bytes() if document == "v2" else PATH.read_bytes())
    spec = importlib.util.spec_from_file_location("paired_constructor", scripts / "versioned_runtime.py")
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    root = tmp_path / "runtime"
    _seed(root)
    before = declaration.read_bytes()
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        '{"command":"complete","payload_hash":"' + "a" * 64 + '"}\n{"command":"settled"}\n',
    ))
    assert runtime.construction_session(
        root, "1.2.4", payload_hash="a" * 64, footprint=declaration, plugin="agent-mcp",
    ) == 0
    assert runtime.is_complete(root, "1.2.4", expect_hash="a" * 64)
    assert declaration.read_bytes() == before
    foreign = tmp_path / "foreign-uninstall-profile.json"
    foreign.write_bytes(before)
    with pytest.raises(ValueError, match="this payload"):
        runtime.construction_session(
            root, "1.2.5", payload_hash="a" * 64, footprint=foreign, plugin="agent-mcp",
        )
    assert not (root / "versions/1.2.5").exists()


def test_repeated_source_hash_refreshes_do_not_grow_the_contract() -> None:
    previous = _record()
    size = len(json.dumps(previous, sort_keys=True))
    for number in range(100):
        current = deepcopy(previous)
        current["producers"]["scripts"] = f"{number:064x}"
        subject.require_transition(previous, current, "agent-mcp")
        assert current["revision"] == 36
        assert current["contract"]["recognizers"] == previous["contract"]["recognizers"]
        assert len(json.dumps(current, sort_keys=True)) == size
        previous = current


@pytest.mark.parametrize("change", ["add-artifact", "remove-artifact", "edit-effect", "retire-effect-entry",
                                  "retire-legacy-support", "change-scope"])
def test_semantic_changes_require_an_exact_deliberate_transition(change: str) -> None:
    previous = _record()
    current = deepcopy(previous)
    contract = current["contract"]
    if change == "add-artifact":
        contract["profile"]["root_entries"].append("generated-state")
    elif change == "remove-artifact":
        contract["profile"]["root_entries"].remove("venv")
    elif change == "edit-effect":
        contract["effects"]["resident-processes"]["scope"] = "runtime-root"
    elif change == "retire-effect-entry":
        contract["effects"]["runtime-control"]["entries"].remove("serve.sock")
    elif change == "retire-legacy-support":
        contract["recognizers"].clear()
    else:
        current["producers"]["other-scope"] = "0" * 64
    with pytest.raises(subject.FootprintError, match="next revision and exact assessment"):
        subject.require_transition(previous, current, "agent-mcp")
    _assess(previous, current)
    subject.require_transition(previous, current, "agent-mcp")
    if change == "retire-effect-entry":
        assert current["change"]["delta"]["retired"] == ["effects:runtime-control:entry:serve.sock"]
    current["change"]["delta"]["introduced"].append("missing-declaration")
    with pytest.raises(subject.FootprintError):
        subject.require_transition(previous, current, "agent-mcp")


def test_retirement_requires_explicit_preservation_not_an_arbitrary_target() -> None:
    previous = _record()
    current = deepcopy(previous)
    current["contract"]["profile"]["root_entries"].remove("venv")
    _assess(previous, current)
    identity = "root_entries:venv"
    current["change"]["dispositions"].clear()
    with pytest.raises(subject.FootprintError, match="explicit disposition"):
        subject.parse(current, "agent-mcp")
    current["change"]["dispositions"][identity] = {
        "action": "retained", "reason": "Cannot substitute a different artifact.",
        "target": "root_entries:versions",
    }
    with pytest.raises(subject.FootprintError, match="not retained"):
        subject.require_transition(previous, current, "agent-mcp")


def test_equivalent_legacy_effect_can_move_to_an_explicit_named_rule() -> None:
    previous = _record()
    current = deepcopy(previous)
    effects = current["contract"]["effects"]
    effects["supported-legacy-processes"] = effects.pop("legacy-runtime-processes")
    _assess(previous, current)
    for identity in current["change"]["delta"]["retired"]:
        current["change"]["dispositions"][identity] = {
            "action": "retained", "reason": "Same supported predecessor artifacts and custody.",
            "target": identity.replace("legacy-runtime-processes", "supported-legacy-processes"),
        }
    subject.require_transition(previous, current, "agent-mcp")


def test_retiring_a_layout_selects_fresh_without_discarding_the_incumbent(tmp_path: Path) -> None:
    _seed(tmp_path)
    previous = _record()
    current = deepcopy(previous)
    current["contract"]["recognizers"].clear()
    _assess(previous, current)
    subject.require_transition(previous, current, "agent-mcp")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    manifest = subject.parse(current, "agent-mcp")
    plan = subject.plan_construction(
        manifest, tmp_path, "1.2.3",
        admission=lambda *_: pytest.fail("Unsupported incumbent must not become an approval prerequisite"),
    )
    assert plan.mode == "fresh" and plan.recognition.reason == "contracts-unsupported"
    assert manifest.profile == previous["contract"]["profile"]
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("mutation", ["unknown-schema", "boolean-version", "boolean-revision", "mandatory-field",
                                    "unknown-recognizer", "unsafe-scope", "unsafe-entry", "boolean-profile",
                                    "duplicate-effect", "invalid-disposition"])
def test_unknown_or_malformed_incoming_semantics_are_refused(mutation: str) -> None:
    record = _record()
    if mutation == "unknown-schema":
        record["version"] = 999
    elif mutation == "boolean-version":
        record["version"] = True
    elif mutation == "boolean-revision":
        record["revision"] = True
    elif mutation == "mandatory-field":
        record["contract"]["required_policy"] = "unsupported"
    elif mutation == "unknown-recognizer":
        record["contract"]["recognizers"]["legacy-complete-v1"]["kind"] = "unknown-provider"
    elif mutation == "unsafe-scope":
        record["producers"] = {"../outside": "0" * 64}
    elif mutation == "unsafe-entry":
        record["contract"]["profile"]["root_entries"].append("../outside")
    elif mutation == "boolean-profile":
        record["contract"]["profile"]["version"] = True
    elif mutation == "duplicate-effect":
        entries = record["contract"]["effects"]["runtime-control"]["entries"]
        entries.append(entries[0])
    else:
        record["contract"]["profile"]["root_entries"].remove("venv")
        _assess(_record(), record)
        record["change"]["dispositions"]["root_entries:venv"]["action"] = []
    with pytest.raises(subject.FootprintError):
        subject.parse(record, "agent-mcp")


@pytest.mark.parametrize("kind,limit", [("marker", 65536), ("manifest", 1 << 20)])
def test_exact_read_boundaries(tmp_path: Path, kind: str, limit: int) -> None:
    path = tmp_path / "declaration.json"
    path.write_bytes(b" " * limit)
    reader = subject._marker_text if kind == "marker" else subject._manifest_text
    assert len(reader(path)) == limit
    path.write_bytes(b" " * (limit + 1))
    with pytest.raises(subject.FootprintError, match=f"{kind} exceeds"):
        reader(path)


@pytest.mark.parametrize("damage", ["directory", "hardlink", "symlink", "unreadable", "duplicate"])
def test_trusted_manifest_read_failures_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    path = tmp_path / "declaration.json"
    path.write_bytes(PATH.read_bytes())
    if damage == "directory":
        path.unlink()
        path.mkdir()
    elif damage == "hardlink":
        os.link(path, tmp_path / "shared")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(PATH)
    elif damage == "unreadable":
        original = Path.open

        def denied(candidate: Path, *args: Any, **kwargs: Any) -> Any:
            if candidate == path:
                raise PermissionError("Fixture declaration cannot be read")
            return original(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    else:
        path.write_text('{"version":3,"version":2}', encoding="utf-8")
    with pytest.raises((subject.FootprintError, OSError)):
        subject.parse(subject.decode(subject._manifest_text(path)), "agent-mcp")


def test_complete_history_uses_manifest_budget_without_expanding_marker_budget(tmp_path: Path) -> None:
    path = tmp_path / "uninstall-profile.json"
    content = json.dumps(_legacy_record(), indent=4).encode("utf-8")
    assert len(content) > 65536
    path.write_bytes(content)
    observed = subject.parse(subject.decode(subject._manifest_text(path)), "agent-mcp")
    assert observed.profile == subject.parse(_legacy_record(), "agent-mcp").profile
    assert not hasattr(observed, "history")
    with pytest.raises(subject.FootprintError, match="marker exceeds"):
        subject._marker_text(path)


def test_oversized_manifest_is_refused_before_decode(tmp_path: Path) -> None:
    path = tmp_path / "uninstall-profile.json"
    path.write_bytes(b" " * ((1 << 20) + 1))
    with pytest.raises(subject.FootprintError, match="manifest exceeds"):
        subject._manifest_text(path)


def _seed(root: Path) -> None:
    slot = root / "versions/1.2.3"
    slot.mkdir(parents=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        '{"schema_version":3,"service":"agent-mcp","runtime_version":"1.2.3"}', encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(
        '{"version":"1.2.3","completed_at":"fixture-time","pid":123}', encoding="utf-8",
    )


@pytest.mark.parametrize("condition", (
    "cold", "current", "missing-deployment", "corrupt-deployment", "duplicate-fields",
    "foreign-service", "unsupported-schema", "boolean-schema", "traversal-selection",
    "missing-completion", "foreign-completion", "boolean-pid", "unknown-revision",
    "missing-runtime-version", "stale-deployment",
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
        "missing-runtime-version": ("deploy-manifest.json", '{"schema_version":3,"service":"agent-mcp"}'),
        "stale-deployment": ("deploy-manifest.json", '{"schema_version":3,"service":"agent-mcp","runtime_version":"1.2.2"}'),
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
    if condition in {"missing-deployment", "missing-completion"}:
        name = "deploy-manifest.json" if condition == "missing-deployment" else "versions/1.2.3/.install-complete.json"
        (root / name).unlink()
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = subject.recognize(manifest, root, footprint_revision=999 if condition == "unknown-revision" else 1)
    assert result.matched is (condition == "current")
    assert manifest.profile == expected
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert root.exists() is (condition != "cold")


def test_historical_match_is_not_compared_to_the_incoming_revision(tmp_path: Path) -> None:
    record = _legacy_record()
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
    manifest = subject.parse(record, "agent-mcp")
    _seed(tmp_path)
    assert manifest.revision == revision
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
    (tmp_path / "deploy-manifest.json").write_text(
        json.dumps({"schema_version": 3, "service": "agent-mcp", "runtime_version": selected}),
        encoding="utf-8",
    )
    marker = tmp_path / "versions" / selected / ".install-complete.json"
    record = json.loads(marker.read_text())
    record["version"] = selected
    marker.write_text(json.dumps(record), encoding="utf-8")
    plan = subject.plan_construction(
        subject.parse(_record(), "agent-mcp"), tmp_path, "1.2.3",
        admission=lambda root, version: "reuse" if version == selected else "health-repair-required",
    )
    assert plan.version == selected and plan.mode == "incremental"


@pytest.mark.parametrize("old_slot", ("present", "missing"))
def test_failed_fresh_integration_cannot_become_incremental_on_retry(
    tmp_path: Path, old_slot: str,
) -> None:
    _seed(tmp_path)
    if old_slot == "missing":
        (tmp_path / "versions/1.2.3").rename(tmp_path / "removed-slot")
    manifest = subject.parse(_record(), "agent-mcp")
    initial = subject.plan_construction(
        manifest, tmp_path, "1.2.3", admission=lambda *_: "health-repair-required",
    )
    assert initial.mode == "fresh" and initial.version != "1.2.3"
    selected = initial.version
    slot = tmp_path / "versions" / selected
    slot.mkdir()
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": selected, "completed_at": "fixture-time", "pid": 123}),
        encoding="utf-8",
    )
    (tmp_path / "current-version").write_text(selected, encoding="utf-8")
    plan = subject.plan_construction(
        manifest, tmp_path, "1.2.3",
        admission=lambda *_: pytest.fail("Incomplete integration must not reuse construction"),
    )
    assert plan.mode == "fresh" and plan.version != selected


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


@pytest.mark.parametrize("mutation", ("omitted-delta", "unsafe-producer", "disposable-data"))
def test_invalid_history_or_protection_is_rejected(mutation: str) -> None:
    old = _legacy_record()
    record = deepcopy(old)
    snapshot = record["history"][0]["snapshot"]
    if mutation == "omitted-delta":
        snapshot["profile"]["root_entries"].append("new-file")
    elif mutation == "unsafe-producer":
        snapshot["producers"]["../outside.py"] = "0" * 64
        record["history"][0]["delta"] = subject.delta(None, snapshot)
    else:
        snapshot["effects"]["configuration-and-data"]["disposition"] = "reset"
        record["history"][0]["delta"] = subject.delta(None, snapshot)
    with pytest.raises(subject.FootprintError):
        subject.parse(record, "agent-mcp")


@pytest.mark.parametrize("change", ("edited-installer", "new-producer"))
def test_producer_output_change_requires_current_digest(tmp_path: Path, change: str) -> None:
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
        f"{scope}: producer changed without refreshing its source digest",
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
        "src: producer changed without refreshing its source digest",
    ]


def test_existing_install_contract_includes_footprint_guard() -> None:
    spec = importlib.util.spec_from_file_location("footprint_install_guard_test", ROOT / "tools/check-install-contract.py")
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.check_footprints is guard.check
    assert guard.check(ROOT) == []


def test_source_guard_checks_published_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = _record()
    previous["contract"]["effects"]["runtime-control"]["entries"].append("lost-old-control")
    monkeypatch.setattr(guard, "_baseline", lambda *args: json.dumps(previous))
    problems = guard.check(ROOT)
    assert len(problems) == 1
    assert "Semantic footprint changes require" in problems[0]


def test_changed_producer_scope_cannot_hide_new_output(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record()
    record["producers"] = {"scripts/init.sh": "0" * 64}
    original = Path.open
    monkeypatch.setattr(
        Path, "open",
        lambda path, mode="r", *args, **kwargs: (
            io.BytesIO(json.dumps(record).encode("utf-8"))
            if path == PATH and mode == "rb" else original(path, mode, *args, **kwargs)
        ),
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
