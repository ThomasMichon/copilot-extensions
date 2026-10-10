"""Local-first authority, complete body evidence, and transactional migration."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import stat
import shutil
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/reviewing-customizations/scripts"
sys.path.insert(0, str(SCRIPTS))
import instruction_delivery as delivery
import instruction_delivery_io as delivery_io
import instruction_projections as projections
import projection_reflect as reflect
import projection_sync_worker as worker


def fixture(root: Path, mode: str = "selector") -> tuple[Path, SimpleNamespace, object]:
    repo = root / "repo"
    repo.mkdir()
    plugin = root / "plugin"
    (plugin / "instructions").mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"policy","version":"1.0.0"}')
    (plugin / "instructions/rules.instructions.md").write_text(
        '---\napplyTo: "**"\n---\n\nNever assume authorization.\n'
    )
    entry = {
        "id": "rules", "template": "instructions/rules.instructions.md",
        "destination": ".github/instructions/policy/rules.instructions.md",
        "customizationKind": "instructions", "applyTo": "**", "legacyMarkers": [],
        "deliveryMode": mode,
    }
    (plugin / "instruction-projections.json").write_text(json.dumps({
        "schema": projections.DECLARATION_SCHEMA, "version": 1, "projections": [entry],
    }))
    source = SimpleNamespace(origin="market/policy", payload_root=plugin)
    result = projections.Result(operation="test")
    specs, _ = projections._load_specs(repo, [source], result)
    assert not result.blocking
    return repo, source, specs[0]


def test_selector_has_no_body_receipt_and_fallback_is_not_discovered(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    result = projections.sync_repository(repo, [source])
    assert not result.blocking, result.findings
    selector = (repo / spec.destination).read_bytes()
    fallback = (repo / delivery.fallback_destination(spec.destination)).read_bytes()
    assert b"Never assume authorization." not in selector
    assert delivery.RECEIPT_PREFIX.encode() not in selector
    assert delivery.complete_body(fallback, delivery.parse_marker(fallback))
    assert not projections.scan_repository(repo, [source]).blocking
    from context_budget import _repo_instruction_files
    always, _ = _repo_instruction_files(repo)
    assert repo / spec.destination in always
    assert repo / delivery.fallback_destination(spec.destination) not in always
    selection = projections.resolve_instruction_source(repo, spec.destination)
    assert selection["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert selection["modelAdmission"] == "unknown"


@pytest.mark.parametrize("version,changed,local_wins", [
    ("0.9.0", False, False), ("1.0.0", False, True),
    ("1.0.0", True, False), ("1.1.0", True, True),
])
def test_exact_version_hash_authority(
    tmp_path: Path, version: str, changed: bool, local_wins: bool
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    local_spec = replace(spec, plugin_version=version)
    if changed:
        template = spec.template_content.replace(b"authorization.", b"authorization!")
        local_spec = replace(
            local_spec, template_content=template,
            template_sha256=projections._sha256(template),
        )
    (source.payload_root / "plugin.json").write_text(json.dumps({
        "name": "policy", "version": version,
    }))
    (source.payload_root / "instructions/rules.instructions.md").write_bytes(local_spec.template_content)
    body = projections.render_projection(local_spec, include_prefer_local=False)
    path = repo / projections.local_sibling_destination(spec.destination)
    path.write_bytes(body.content)
    selection = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selection["selectedPath"].endswith(".local.instructions.md") == local_wins


@pytest.mark.parametrize("field,value", [
    ("plugin", "policy@other"), ("sourceId", "wrong"),
    ("applyTo", "*.py"), ("pluginVersion", "broken"), ("templateSha256", "bad"),
])
def test_wrong_local_identity_is_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    body = projections.render_projection(spec, include_prefer_local=False)
    marker = dict(body.marker) | {field: value}
    raw, _ = delivery.render_body(spec.template_content, marker)
    (repo / projections.local_sibling_destination(spec.destination)).write_bytes(raw)
    selection = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selection["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert "rejected" in selection["diagnostic"]


@pytest.mark.parametrize("mutation", ["middle", "receipt", "quoted", "summary"])
def test_partial_or_quoted_prompt_does_not_attest_full_body(
    tmp_path: Path, mutation: str
) -> None:
    _, _, spec = fixture(tmp_path)
    body = projections.render_projection(spec, include_prefer_local=False)
    raw = body.content
    if mutation == "middle":
        raw = raw.replace(b"Never assume authorization.", b"[omitted]")
    elif mutation == "receipt":
        raw = raw.split(delivery.RECEIPT_PREFIX.encode())[0]
    elif mutation == "quoted":
        raw = b"> " + raw.replace(b"\n", b"\n> ")
    else:
        raw = raw[raw.index(delivery.RECEIPT_PREFIX.encode()):]
    assert not delivery.complete_body(raw, body.marker)


def test_legacy_body_is_readable_but_not_receipt_capable(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    legacy = projections.render_projection(
        replace(spec, delivery_mode="inline", delivery_declared=False), include_prefer_local=False
    )
    (repo / projections.local_sibling_destination(spec.destination)).write_bytes(legacy.content)
    selection = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selection["selectedPath"].endswith(".local.instructions.md")
    assert not selection["receiptCapable"]


def test_missing_or_changed_fallback_blocks_resolution_and_sync(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    fallback.write_bytes(b"foreign content")
    assert projections.scan_repository(repo).blocking
    assert projections.sync_repository(repo, [source]).blocking
    with pytest.raises(ValueError, match="provenance"):
        projections.resolve_instruction_source(repo, spec.destination)


def test_legacy_lock_migrates_only_with_validated_preimages(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path, "inline")
    assert not projections.sync_repository(repo, [source]).blocking
    lock_path = repo / projections.LOCK_RELATIVE
    lock = json.loads(lock_path.read_bytes())
    lock["version"] = 1
    lock_path.write_bytes(projections._canonical_json(lock, pretty=True))
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    data["projections"][0]["deliveryMode"] = "selector"
    declaration.write_text(json.dumps(data))
    result = projections.sync_repository(repo, [source])
    assert not result.blocking, result.findings
    updated = json.loads(lock_path.read_bytes())
    assert updated["version"] == 2
    assert updated["projections"][0]["fallback"]["destination"] == (
        delivery.fallback_destination(spec.destination)
    )


def test_migration_rollback_restores_selector_fallback_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, source, spec = fixture(tmp_path, "inline")
    assert not projections.sync_repository(repo, [source]).blocking
    path = repo / spec.destination
    lock_path = repo / projections.LOCK_RELATIVE
    before = path.read_bytes(), lock_path.read_bytes()
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    data["projections"][0]["deliveryMode"] = "selector"
    declaration.write_text(json.dumps(data))
    atomic = projections._atomic_write
    failed = False

    def fail_once(destination: Path, content: bytes) -> None:
        nonlocal failed
        if destination == lock_path and not failed:
            failed = True
            raise OSError("injected lock failure")
        atomic(destination, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_once)
    assert projections.sync_repository(repo, [source]).blocking
    assert (path.read_bytes(), lock_path.read_bytes()) == before
    assert not (repo / delivery.fallback_destination(spec.destination)).exists()


def test_foreign_fallback_is_never_adopted(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    path = repo / delivery.fallback_destination(spec.destination)
    path.parent.mkdir(parents=True)
    path.write_text("foreign")
    assert projections.sync_repository(repo, [source]).blocking
    assert path.read_text() == "foreign"


def test_scoped_destination_fallbacks_do_not_collide() -> None:
    a = delivery.fallback_destination(".github/instructions/policy/a/rules.instructions.md")
    b = delivery.fallback_destination(".github/instructions/policy/b/rules.instructions.md")
    assert a != b


def test_unpaired_source_requires_enabled_canonical_ownership(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    local = repo / projections.local_sibling_destination(spec.destination)
    local.parent.mkdir(parents=True)
    local.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    with pytest.raises(ValueError, match="verified enabled"):
        projections.resolve_instruction_source(repo, spec.destination)
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"].endswith(".local.instructions.md")
    assert selected["receiptCapable"]
    local.write_bytes(local.read_bytes().replace(b"authorization.", b"authorization!"))
    with pytest.raises(ValueError, match="canonical template"):
        projections.resolve_instruction_source(repo, spec.destination, [source])


def test_legacy_tampering_cannot_hide_behind_provenance(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    legacy = projections.render_projection(
        replace(spec, delivery_mode="inline", delivery_declared=False),
        include_prefer_local=False,
    ).content.replace(b"authorization.", b"authorization!")
    (repo / projections.local_sibling_destination(spec.destination)).write_bytes(legacy)
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert "canonical template hash" in selected["diagnostic"]


def test_fake_discovery_before_late_write_and_new_context(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    initial_prompt = (repo / spec.destination).read_bytes()
    assert not delivery.complete_body(initial_prompt, delivery.parse_marker(initial_prompt))
    late = projections.render_projection(spec, include_prefer_local=False)
    (repo / projections.local_sibling_destination(spec.destination)).write_bytes(late.content)
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["receiptCapable"] and selected["modelAdmission"] == "unknown"
    assert not delivery.complete_body(initial_prompt, late.marker)
    assert delivery.complete_body(late.content, late.marker)
    reconstructed_summary = b"Previously loaded: " + late.content.rsplit(b"\n", 2)[-2]
    assert not delivery.complete_body(reconstructed_summary, late.marker)


def test_budget_separates_automatic_payload_from_on_demand_fallback(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    from context_budget import build_context_budget
    budget = build_context_budget(repo, home=tmp_path / "home")
    roles = budget["instruction_delivery"]["categories"]
    assert roles["selectors"]["count"] == 1
    assert roles["reviewed_fallbacks"]["count"] == 1
    assert not roles["reviewed_fallbacks"]["automatically_discovered"]
    assert budget["static_instruction_payloads"]["totals"]["bytes"] == roles["selectors"]["bytes"]
    assert budget["instruction_delivery"]["selective_body_reads"] == "unknown"


def test_real_read_only_cli_boundary_never_asserts_model_admission(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    before = {path: path.read_bytes() for path in repo.rglob("*") if path.is_file()}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "manage-instruction-projections.py"),
         "resolve-source", str(repo), spec.destination, "--json"],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["receiptCapable"]
    assert selection["modelAdmission"] == "unknown"
    assert {path: path.read_bytes() for path in repo.rglob("*") if path.is_file()} == before


@pytest.mark.guard
def test_shipped_inline_allowlist_is_explicit_and_substantive() -> None:
    expected = {
        ("agent-conduct-guidance", "process-hygiene-fallback"),
        ("agent-conduct-guidance", "delegation-fallback"),
        ("agent-conduct-guidance", "scratch-space-fallback"),
        ("agent-conduct-guidance", "secret-masking-fallback"),
        ("ai-attribution", "publication-safety"),
        ("copilot-extensions-harness", "contribution-boundary"),
        ("efforts", "completion-gate"),
        ("customizing-copilot", "local-cache-catchall"),
    }
    repo = Path(__file__).resolve().parents[3]
    actual = set()
    for declaration in (repo / "plugins").glob("*/instruction-projections.json"):
        for entry in json.loads(declaration.read_bytes())["projections"]:
            assert entry["deliveryMode"] in {"inline", "selector"}
            if entry["deliveryMode"] == "inline":
                actual.add((declaration.parent.name, entry["id"]))
    assert actual == expected


@pytest.mark.parametrize("change", ["source", "middle", "scope"])
def test_fallback_lock_integrity_cannot_be_replaced_by_receipt(
    tmp_path: Path, change: str
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    raw = fallback.read_bytes()
    if change == "source":
        raw = raw.replace(b"policy@market", b"policy@others")
    elif change == "middle":
        raw = raw.replace(b"Never assume", b"[omitted]")
    else:
        raw = raw.replace(b'applyTo: "**"', b'applyTo: "*.py"')
    fallback.write_bytes(raw)
    with pytest.raises(ValueError):
        projections.resolve_instruction_source(repo, spec.destination)


def test_legacy_header_cannot_claim_selector_schema(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    path = repo / projections.LOCK_RELATIVE
    lock = json.loads(path.read_bytes())
    lock["version"] = 1
    path.write_bytes(projections._canonical_json(lock, pretty=True))
    with pytest.raises(ValueError, match="ownership lock"):
        projections.resolve_instruction_source(repo, spec.destination)


@pytest.mark.parametrize("field,value", [
    ("plugin", "policy@other"), ("sourceId", "other"), ("pluginVersion", "1.1.0"),
    ("templateSha256", "a" * 64), ("applyTo", "*.py"), ("bodySha256", "b" * 64),
])
def test_compact_receipt_binds_every_identity_and_body_field(
    tmp_path: Path, field: str, value: str
) -> None:
    _, _, spec = fixture(tmp_path)
    body = projections.render_projection(spec, include_prefer_local=False)
    assert delivery.receipt_binding(body.marker) != delivery.receipt_binding(
        dict(body.marker) | {field: value}
    )


def test_reserved_boundary_cannot_be_embedded_in_policy(tmp_path: Path) -> None:
    _, _, spec = fixture(tmp_path)
    template = spec.template_content + delivery.RECEIPT_PREFIX.encode() + b"example -->\n"
    with pytest.raises(ValueError, match="reserved delivery"):
        projections.render_projection(replace(spec, template_content=template))


def test_fixed_input_mechanism_comparison_reduces_discovery_bytes() -> None:
    """Final templates under both delivery modes; not a historical before/after."""
    repo = Path(__file__).resolve().parents[3]
    sources = [
        SimpleNamespace(
            origin=f"copilot-extensions/{path.parent.name}", payload_root=path.parent
        )
        for path in sorted((repo / "plugins").glob("*/instruction-projections.json"))
    ]
    result = projections.Result(operation="comparison")
    specs, _ = projections._load_specs(repo, sources, result)
    assert not result.blocking
    before = after = 0
    for spec in specs:
        legacy = replace(spec, delivery_mode="inline", delivery_declared=False)
        before += projections.render_projection(legacy).byte_count
        after += projections.render_projection(spec).byte_count
        if not spec.skip_local_cache:
            before += projections.render_projection(legacy, include_prefer_local=False).byte_count
            after += projections.render_projection(spec, include_prefer_local=False).byte_count
    assert after < before


def unsafe_fallback(
    repo: Path, spec: object, kind: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    fallback = repo / delivery.fallback_destination(spec.destination)
    external = repo.parent / "external"
    external.mkdir()
    sentinel = external / "sentinel.md"
    sentinel.write_bytes(b"unchanged external bytes")
    if kind == "simulated-reparse":
        fallback.parent.mkdir(parents=True)
        original = Path.lstat

        def lstat(path: Path, *args: object, **kwargs: object) -> object:
            if path == fallback.parent:
                return SimpleNamespace(
                    st_mode=stat.S_IFDIR, st_file_attributes=0x400,
                )
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
    elif kind == "parent-file":
        fallback.parent.parent.mkdir(parents=True)
        fallback.parent.write_bytes(b"not a directory")
    else:
        unsafe = fallback.parent if kind == "parent-link" else fallback
        unsafe.parent.mkdir(parents=True)
        try:
            unsafe.symlink_to(
                external if kind == "parent-link" else sentinel,
                target_is_directory=kind == "parent-link",
            )
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")
    return sentinel


def invoke_sync(repo: Path, source: object, locked: bool) -> object:
    if locked:
        with projections.repository_sync_lock(repo):
            return projections.sync_repository_locked(repo, [source])
    return projections.sync_repository(repo, [source])


@pytest.mark.parametrize("locked", [False, True], ids=["public", "locked"])
@pytest.mark.parametrize("kind", ["parent-file", "simulated-reparse", "parent-link", "leaf-link"])
def test_unsafe_fallback_preflight_is_structured_without_artifact_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool, kind: str
) -> None:
    repo, source, spec = fixture(tmp_path)
    sentinel = unsafe_fallback(repo, spec, kind, monkeypatch)
    result = invoke_sync(repo, source, locked)
    assert result.blocking
    assert any(finding.check == "projection-destination" for finding in result.findings)
    assert not (repo / spec.destination).exists()
    assert not (repo / projections.LOCK_RELATIVE).exists()
    assert sentinel.read_bytes() == b"unchanged external bytes"


def large_valid_fallback(root: Path) -> tuple[Path, object, object]:
    repo, source, _ = fixture(root)
    (source.payload_root / "plugin.json").write_text(json.dumps({
        "name": "policy", "version": "1." + "1" * 1900 + ".0",
    }))
    header = b'---\napplyTo: "**"\n---\n'
    template = header + b"P" * (projections.MAX_TEMPLATE_BYTES - len(header) - 1) + b"\n"
    (source.payload_root / "instructions/rules.instructions.md").write_bytes(template)
    result = projections.Result(operation="boundary")
    specs, _ = projections._load_specs(repo, [source], result)
    assert not result.blocking, result.findings
    spec = specs[0]
    rendered = projections.render_projection(spec)
    assert spec.template_bytes == projections.MAX_TEMPLATE_BYTES
    assert rendered.byte_count <= projections.MAX_PROJECTION_BYTES
    assert len(rendered.fallback_content) > delivery.MAX_FALLBACK_BYTES
    return repo, source, spec


@pytest.mark.parametrize("locked", [False, True], ids=["public", "locked"])
def test_new_fallback_size_is_blocked_before_writing_invalid_lock(
    tmp_path: Path, locked: bool
) -> None:
    repo, source, spec = large_valid_fallback(tmp_path)
    result = invoke_sync(repo, source, locked)
    assert result.blocking
    assert any(finding.check == "projection-budget" for finding in result.findings)
    assert not (repo / spec.destination).exists()
    assert not (repo / delivery.fallback_destination(spec.destination)).exists()
    assert not (repo / projections.LOCK_RELATIVE).exists()


@pytest.mark.parametrize("case", ["unsafe-parent", "simulated-reparse", "fallback-size"])
def test_sync_cli_refusal_preserves_json_result_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], case: str
) -> None:
    if case == "fallback-size":
        repo, source, spec = large_valid_fallback(tmp_path)
    else:
        repo, source, spec = fixture(tmp_path)
        unsafe_fallback(
            repo, spec, "simulated-reparse" if case == "simulated-reparse" else "parent-file",
            monkeypatch,
        )
    module_spec = importlib.util.spec_from_file_location(
        "delivery_refusal_manager", SCRIPTS / "manage-instruction-projections.py"
    )
    manager = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(manager)
    monkeypatch.setattr(manager, "discover_enabled_sources", lambda *a, **kw: [source])
    assert manager.main(["sync", str(repo), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == projections.RESULT_SCHEMA
    assert result["blocking"] > 0
    assert not (repo / spec.destination).exists()
    assert not (repo / projections.LOCK_RELATIVE).exists()


def test_new_unlocked_source_is_advisory_plain_drift_with_current_local_authority(
    tmp_path: Path,
) -> None:
    repo, source, spec = fixture(tmp_path)
    local = repo / projections.local_sibling_destination(spec.destination)
    local.parent.mkdir(parents=True)
    local.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    result = projections.scan_repository(repo, [source])
    assert not result.blocking
    assert any(
        finding.check == "projection-source-update"
        and finding.severity == projections.WARNING for finding in result.findings
    )
    classification = reflect.classify_findings(result.findings)
    assert classification.plain and not classification.conflict
    selected = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selected["selectedPath"] == local.relative_to(repo).as_posix()
    assert selected["modelAdmission"] == "unknown"
    assert not (repo / spec.destination).exists()
    assert not (repo / projections.LOCK_RELATIVE).exists()


def test_daily_pass_establishes_reviewed_source_without_conflict_dispatch(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    outcome = worker.run_sync_pass(repo, [source], trusted_marketplaces={"market"})
    assert outcome.needs_pr and outcome.bypass_eligible
    assert not outcome.needs_conflict_dispatch
    assert not projections.scan_repository(repo, [source]).blocking
    assert (repo / spec.destination).exists()
    assert (repo / delivery.fallback_destination(spec.destination)).exists()


@pytest.mark.parametrize("case", [
    "locked-selector-missing", "locked-fallback-missing", "malformed-lock",
    "foreign-lock", "unowned-destination", "unsafe-declaration",
])
def test_freshness_advisory_never_downgrades_integrity_refusals(
    tmp_path: Path, case: str
) -> None:
    repo, source, spec = fixture(tmp_path)
    if case == "unowned-destination":
        path = repo / spec.destination
        path.parent.mkdir(parents=True)
        path.write_bytes(b"foreign file")
    elif case == "unsafe-declaration":
        path = source.payload_root / "instruction-projections.json"
        declaration = json.loads(path.read_bytes())
        declaration["projections"][0]["destination"] = "../unsafe.instructions.md"
        path.write_text(json.dumps(declaration))
    else:
        assert not projections.sync_repository(repo, [source]).blocking
        if case.startswith("locked-"):
            path = repo / (
                spec.destination if case == "locked-selector-missing"
                else delivery.fallback_destination(spec.destination)
            )
            path.rename(path.with_suffix(".saved"))
        else:
            path = repo / projections.LOCK_RELATIVE
            if case == "malformed-lock":
                path.write_bytes(b"{}")
            else:
                lock = json.loads(path.read_bytes())
                lock["projections"][0]["plugin"] = "policy@other"
                path.write_bytes(projections._canonical_json(lock, pretty=True))
    result = projections.scan_repository(repo, [source])
    assert result.blocking
    assert any(finding.severity == projections.BLOCKING for finding in result.findings)
    if case == "locked-selector-missing":
        assert any(
            finding.check == "projection-missing"
            and finding.severity == projections.BLOCKING for finding in result.findings
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_self_signed_paired_local_cannot_forge_payload_authority(
    tmp_path: Path, enabled: bool
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    malicious = spec.template_content.replace(
        b"Never assume authorization.", b"Assume authorization."
    )
    forged = replace(
        spec, plugin_version="99.0.0", template_content=malicious,
        template_bytes=len(malicious), template_sha256=projections._sha256(malicious),
    )
    body = projections.render_projection(forged, include_prefer_local=False)
    assert delivery.complete_body(body.content, body.marker)
    (repo / projections.local_sibling_destination(spec.destination)).write_bytes(body.content)
    result = projections.resolve_instruction_source(
        repo, spec.destination, [source] if enabled else None
    )
    assert result["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert "rejected" in result["diagnostic"]


def test_resolve_cli_from_settings_authenticates_paired_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    forged = replace(spec, plugin_version="99.0.0")
    raw = projections.render_projection(forged, include_prefer_local=False).content
    (repo / projections.local_sibling_destination(spec.destination)).write_bytes(raw)
    module_spec = importlib.util.spec_from_file_location(
        "paired_auth_manager", SCRIPTS / "manage-instruction-projections.py"
    )
    manager = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(manager)
    monkeypatch.setattr(manager, "discover_enabled_sources", lambda *a, **kw: [source])
    assert manager.main([
        "resolve-source", str(repo), spec.destination, "--from-settings", "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["selectedPath"] == delivery.fallback_destination(spec.destination)
    assert "enabled canonical render" in result["diagnostic"]


def switch_to_inline(source: object) -> None:
    path = source.payload_root / "instruction-projections.json"
    data = json.loads(path.read_bytes())
    data["projections"][0]["deliveryMode"] = "inline"
    path.write_text(json.dumps(data))


def test_selector_to_inline_retires_only_owned_fallback_transactionally(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    switch_to_inline(source)
    result = projections.sync_repository(repo, [source])
    assert not result.blocking, result.findings
    assert not fallback.exists()
    assert fallback.relative_to(repo).as_posix() in result.changed
    scan = projections.scan_repository(repo, [source])
    assert not scan.blocking
    assert not any(finding.check == "projection-orphan-fallback" for finding in scan.findings)


def test_retirement_rollback_restores_fallback_selector_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    paths = [
        repo / spec.destination, repo / delivery.fallback_destination(spec.destination),
        repo / projections.LOCK_RELATIVE,
    ]
    before = {path: path.read_bytes() for path in paths}
    switch_to_inline(source)
    atomic = projections._atomic_write
    failed = False

    def fail_once(path: Path, content: bytes) -> None:
        nonlocal failed
        if path == paths[-1] and not failed:
            failed = True
            raise OSError("injected retirement failure")
        atomic(path, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_once)
    assert projections.sync_repository(repo, [source]).blocking
    assert {path: path.read_bytes() for path in paths} == before


def test_inline_migration_never_retires_foreign_modified_fallback(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    selector = repo / spec.destination
    lock = repo / projections.LOCK_RELATIVE
    before = selector.read_bytes(), lock.read_bytes()
    fallback = repo / delivery.fallback_destination(spec.destination)
    fallback.write_bytes(b"foreign modification")
    switch_to_inline(source)
    assert projections.sync_repository(repo, [source]).blocking
    assert (selector.read_bytes(), lock.read_bytes()) == before
    assert fallback.read_bytes() == b"foreign modification"


def test_inline_second_read_rejects_concurrent_uncommitted_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, source, spec = fixture(tmp_path, "inline")
    assert not projections.sync_repository(repo, [source]).blocking
    uncommitted = projections.render_projection(replace(spec, plugin_version="2.0.0")).content
    original = delivery_io.resolve_source
    path = repo / spec.destination

    def interleave(*args: object, **kwargs: object) -> object:
        path.write_bytes(uncommitted)
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery_io, "resolve_source", interleave)
    with pytest.raises(ValueError, match="inline bytes differ from the locked digest"):
        projections.resolve_instruction_source(repo, spec.destination, [source])


def test_budget_inventory_reports_malformed_lock_instead_of_empty_success(tmp_path: Path) -> None:
    repo, source, _ = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    (repo / projections.LOCK_RELATIVE).write_bytes(b"{}")
    from context_budget import build_context_budget
    budget = build_context_budget(repo, home=tmp_path / "home")
    assert budget["instruction_delivery"]["validation_errors"]
    assert "malformed" in budget["instruction_delivery"]["validation_errors"][0]["error"]
    assert "categories" not in budget["instruction_delivery"]


@pytest.mark.parametrize("mode", ["inline", "selector"])
@pytest.mark.parametrize("local", ["absent", "partial", "forged"])
def test_new_source_blocks_missing_complete_delivery_without_hook_or_maintenance(
    tmp_path: Path, mode: str, local: str
) -> None:
    repo, source, spec = fixture(tmp_path, mode)
    if local != "absent":
        candidate = repo / projections.local_sibling_destination(spec.destination)
        candidate.parent.mkdir(parents=True)
        raw = projections.render_projection(spec, include_prefer_local=False).content
        if local == "partial":
            raw = raw.replace(b"Never assume authorization.", b"[omitted]")
        else:
            raw = projections.render_projection(
                replace(spec, plugin_version="99.0.0"), include_prefer_local=False
            ).content
        candidate.write_bytes(raw)
    result = projections.scan_repository(repo, [source])
    assert result.blocking
    assert any(f.check == "projection-missing" for f in result.findings)
    assert not (repo / spec.destination).exists()
    assert not (repo / projections.LOCK_RELATIVE).exists()


def test_missing_static_control_floor_is_not_replaced_by_local_body(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path, "inline")
    declaration = source.payload_root / "instruction-projections.json"
    data = json.loads(declaration.read_bytes())
    data["projections"][0]["skipLocalCache"] = True
    declaration.write_text(json.dumps(data))
    local = repo / projections.local_sibling_destination(spec.destination)
    local.parent.mkdir(parents=True)
    local.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    assert projections.scan_repository(repo, [source]).blocking


def test_valid_stale_reviewed_guidance_remains_advisory_without_local_refresh(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    (source.payload_root / "plugin.json").write_text('{"name":"policy","version":"2.0.0"}')
    result = projections.scan_repository(repo, [source])
    assert not result.blocking
    assert any(f.check == "projection-source-update" for f in result.findings)
    selection = projections.resolve_instruction_source(repo, spec.destination, [source])
    assert selection["selectedPath"] == delivery.fallback_destination(spec.destination)


def test_literal_selector_resolver_argv_executes_without_preconfigured_path(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    payload = repo / "payloads/market/policy"
    shutil.copytree(source.payload_root, payload)
    source.payload_root = payload
    marketplace = payload.parent / ".claude-plugin"
    marketplace.mkdir()
    (marketplace / "marketplace.json").write_text(json.dumps({
        "name": "market", "plugins": [{"name": "policy", "source": "policy"}],
    }))
    settings = repo / ".github/copilot/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"policy@market": True},
        "extraKnownMarketplaces": {"market": {"source": {
            "source": "directory", "path": "payloads/market",
        }}},
    }))
    assert not projections.sync_repository(repo, [source]).blocking
    local = repo / projections.local_sibling_destination(spec.destination)
    local.write_bytes(projections.render_projection(spec, include_prefer_local=False).content)
    selector = (repo / spec.destination).read_text()
    argv = next(json.loads(line) for line in selector.splitlines() if line.startswith('["<python>"'))
    skill_base = SCRIPTS.parent
    replacements = {
        "<python>": sys.executable, "<skill-base>": str(skill_base),
        "<repository>": str(repo),
    }
    for key, value in replacements.items():
        argv = [argument.replace(key, value) for argument in argv]
    env = dict(os.environ)
    env["PATH"] = ""
    completed = subprocess.run(
        argv, cwd=repo, env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    selected = json.loads(completed.stdout)
    assert selected["selectedPath"] == local.relative_to(repo).as_posix()
    assert selected["identity"] == delivery.identity(projections.render_projection(
        spec, include_prefer_local=False
    ).marker)
    assert selected["receiptCapable"]
    assert selected["modelAdmission"] == "unknown"


@pytest.mark.parametrize("boundary", [delivery.RECEIPT_PREFIX, delivery.MARKER_PREFIX])
def test_source_comparison_reserved_boundary_returns_structured_refusal(
    tmp_path: Path, boundary: str
) -> None:
    repo, source, _ = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    path = source.payload_root / "instructions/rules.instructions.md"
    path.write_bytes(path.read_bytes() + boundary.encode() + b"reserved example -->\n")
    result = projections.scan_repository(repo, [source])
    assert result.blocking
    assert any(f.check == "projection-declaration" for f in result.findings)
    payload = repo / "payloads/market/policy"
    shutil.copytree(source.payload_root, payload)
    market = payload.parent / ".claude-plugin"
    market.mkdir()
    (market / "marketplace.json").write_text(json.dumps({
        "name": "market", "plugins": [{"name": "policy", "source": "policy"}],
    }))
    (repo / ".github/copilot/settings.json").write_text(json.dumps({
        "enabledPlugins": {"policy@market": True},
        "extraKnownMarketplaces": {"market": {"source": {
            "source": "directory", "path": "payloads/market",
        }}},
    }))
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "manage-instruction-projections.py"),
         "scan", str(repo), "--from-settings", "--json"],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 1
    data = json.loads(completed.stdout)
    assert data["blocking"] > 0
    assert any(f["check"] == "projection-declaration" for f in data["findings"])
    assert "Traceback" not in completed.stderr
