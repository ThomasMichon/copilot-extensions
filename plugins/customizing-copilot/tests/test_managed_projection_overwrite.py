"""Consent-scoped repairs retain ownership and transactional edit protection."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_instruction_delivery import delivery, fixture, projections, worker
import projection_reflect as reflect
import projection_reflect_consent as consent


def opt_in(repo: Path, **overrides: object) -> Path:
    path = repo.joinpath(*consent.CONSENT_PATH_PARTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": consent.CONSENT_SCHEMA, "version": 1, "enabled": True,
        "reconcilerAgent": "projection-reconciler", "dispatchLabel": "projection-conflict",
        "trustedMarketplaces": ["market"], "overwriteExistingManaged": True,
    } | overrides))
    return path


@pytest.mark.parametrize("value", [None, 1, "true", [], {}])
def test_managed_overwrite_consent_requires_boolean(tmp_path: Path, value: object) -> None:
    opt_in(tmp_path, overwriteExistingManaged=value)
    assert consent.load_consent(tmp_path) is None


def test_managed_overwrite_consent_default_and_explicit_false(tmp_path: Path) -> None:
    path = opt_in(tmp_path, overwriteExistingManaged=False)
    assert not consent.load_consent(tmp_path).overwrite_existing_managed
    raw = json.loads(path.read_text())
    del raw["overwriteExistingManaged"]
    path.write_text(json.dumps(raw))
    assert not consent.load_consent(tmp_path).overwrite_existing_managed


def artifacts(repo: Path, spec: object) -> list[Path]:
    return [
        repo / spec.destination,
        repo / delivery.fallback_destination(spec.destination),
        repo / projections.LOCK_RELATIVE,
    ]


def test_managed_overwrite_repairs_pair_and_recomputes_current_source(tmp_path: Path) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    paths = artifacts(repo, spec)
    paths[0].write_bytes(paths[0].read_bytes().replace(b"Before dependent action", b"Edited instruction"))
    paths[1].write_bytes(paths[1].read_bytes().replace(b"Never assume authorization.", b"Edited body."))
    assert projections.sync_repository(repo, [source]).blocking
    opt_in(repo)
    template = source.payload_root / "instructions/rules.instructions.md"
    template.write_bytes(template.read_bytes().replace(b"authorization.", b"authorization!"))
    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["market"], overwrite_existing_managed=True
    )
    assert outcome.has_actionable_change and outcome.bypass_eligible
    assert not outcome.needs_conflict_dispatch
    assert b"Never assume authorization!" in paths[1].read_bytes()
    assert not projections.scan_repository(repo, [source]).blocking
    assert not worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["market"], overwrite_existing_managed=True
    ).needs_pr


@pytest.mark.parametrize("case", ["disabled", "unmarked", "owner", "scope", "lock", "source", "indirection"])
def test_managed_overwrite_refuses_missing_authority(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    paths = artifacts(repo, spec)
    opt_in(repo, enabled=case != "disabled")
    if case == "unmarked":
        paths[1].write_bytes(b"unowned content")
    elif case in {"owner", "scope"}:
        old, new = (b"market", b"other") if case == "owner" else (b'applyTo: "**"', b'applyTo: "*.py"')
        paths[0].write_bytes(paths[0].read_bytes().replace(old, new))
    elif case == "lock":
        paths[2].write_text("{}")
    elif case == "source":
        (source.payload_root / "instructions/rules.instructions.md").write_text("invalid template")
    elif case == "indirection":
        original = projections._safe_destination

        def safe(root: Path, relative: object) -> Path:
            if root / str(relative) == paths[1]:
                raise ValueError("unsafe indirection")
            return original(root, relative)

        monkeypatch.setattr(projections, "_safe_destination", safe)
    before = [p.read_bytes() for p in paths]
    result = projections.sync_repository(repo, [source], overwrite_existing_managed=True)
    assert result.blocking
    assert before == [p.read_bytes() for p in paths]


@pytest.mark.parametrize("case", ["revoke", "concurrent", "lock-race", "write-failure"])
def test_managed_overwrite_rechecks_and_rolls_back_pair(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    paths = artifacts(repo, spec)
    for path in paths[:2]:
        path.write_bytes(path.read_bytes() + b"\nhand edit\n")
    before = [p.read_bytes() for p in paths]
    consent_path = opt_in(repo)
    original = projections._atomic_write
    calls = 0

    def write(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1 and case == "write-failure":
            original(path, content)
            return
        if calls == 2 and case == "write-failure":
            raise OSError("injected failure")
        original(path, content)
        if calls == 1:
            if case == "revoke":
                consent_path.write_text("{}")
            elif case == "concurrent":
                paths[0].write_bytes(b"concurrent edit")
            elif case == "lock-race":
                paths[2].write_bytes(b"concurrent lock edit")

    monkeypatch.setattr(projections, "_atomic_write", write)
    result = projections.sync_repository(repo, [source], overwrite_existing_managed=True)
    assert result.blocking
    assert paths[1].read_bytes() == before[1]
    assert paths[2].read_bytes() == (b"concurrent lock edit" if case == "lock-race" else before[2])
    assert paths[0].read_bytes() == (b"concurrent edit" if case == "concurrent" else before[0])


def test_overlap_warning_is_successful_noop_but_unknown_and_blocking_conflict() -> None:
    warning = SimpleNamespace(check="projection-overlap", severity="warning")
    for finding in [
        SimpleNamespace(check="projection-overlap", severity="blocking"),
        SimpleNamespace(check="new-check", severity="warning"),
        SimpleNamespace(check="projection-overlap", severity=None),
    ]:
        assert reflect.classify_findings([finding]).conflict == (finding,)
    decision = reflect.bypass_decision(
        findings=[warning], changed_lock_entries=[], trusted_marketplaces=[]
    )
    outcome = worker.SyncOutcome((), False, (warning,), decision)
    assert outcome.audit_warnings == (warning,)
    assert not outcome.needs_pr and not outcome.needs_conflict_dispatch


@pytest.mark.parametrize("enabled", [False, True])
def test_managed_overwrite_cli_uses_live_consent(
    tmp_path: Path, enabled: bool, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    path = repo / spec.destination
    path.write_bytes(path.read_bytes() + b"\nhand edit\n")
    opt_in(repo, overwriteExistingManaged=enabled)
    monkeypatch.setattr(projections, "discover_enabled_sources", lambda *a, **kw: [source])
    assert worker.main([str(repo), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["needsConflictDispatch"] is (not enabled)
    assert (b"hand edit" in path.read_bytes()) is (not enabled)


def test_overlap_warning_only_cli_returns_success_without_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    repo, source, _ = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    opt_in(repo)
    scan = projections.Result(operation="scan")
    scan.add(projections.WARNING, "projection-overlap", "policy", "overlapping scopes")
    monkeypatch.setattr(projections, "scan_repository", lambda *a, **kw: scan)
    monkeypatch.setattr(projections, "discover_enabled_sources", lambda *a, **kw: [source])
    assert worker.main([str(repo), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["needsPr"] and not result["needsConflictDispatch"]
    assert result["auditWarnings"][0]["check"] == "projection-overlap"


@pytest.mark.parametrize("case", ["untrusted", "unpinned", "stale-trust"])
def test_fallback_only_repair_uses_owner_and_live_bypass_policy(
    tmp_path: Path, case: str,
) -> None:
    repo, source, spec = fixture(tmp_path)
    source.is_local_checkout = False
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    fallback.write_bytes(fallback.read_bytes() + b"\nhand edit\n")
    opt_in(
        repo, trustedMarketplaces=["other"] if case != "unpinned" else ["market"],
        requireImmutablePin=case == "unpinned",
    )
    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["market"], overwrite_existing_managed=True
    )
    assert outcome.has_actionable_change
    assert not outcome.lock_updated
    assert not outcome.bypass_eligible
    assert not outcome.needs_conflict_dispatch
    assert outcome.bypass.reasons


def test_managed_overwrite_uses_consent_changed_while_acquiring_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    fallback.write_bytes(fallback.read_bytes() + b"\nhand edit\n")
    opt_in(repo)
    original = projections.repository_sync_lock

    @contextmanager
    def acquire(root: Path):
        with original(root):
            opt_in(repo, trustedMarketplaces=["other"])
            yield

    monkeypatch.setattr(projections, "repository_sync_lock", acquire)
    outcome = worker.run_sync_pass(
        repo, [source], trusted_marketplaces=["market"], overwrite_existing_managed=True
    )
    assert outcome.has_actionable_change
    assert not outcome.bypass_eligible
    assert "outside the trusted-source allowlist" in outcome.bypass.reasons[0]


def test_scheduler_template_forwards_managed_overwrite_consent() -> None:
    template = Path(__file__).resolve().parents[1] / (
        "skills/setting-up-instruction-sync-worker/references/templates/scheduler-config.md"
    )
    assert "overwrite_existing_managed=consent.overwrite_existing_managed" in template.read_text()


def test_owned_content_edit_after_comparison_is_authoritatively_superseded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    expected = fallback.read_bytes()
    fallback.write_bytes(expected + b"\nunsupported edit\n")
    opt_in(repo)
    original = projections._atomic_write

    def write(path: Path, content: bytes) -> None:
        path.write_bytes(b"racing unsupported content edit")
        original(path, content)

    monkeypatch.setattr(projections, "_atomic_write", write)
    assert not projections.sync_repository(repo, [source], overwrite_existing_managed=True).blocking
    assert fallback.read_bytes() == expected


def test_unowned_creation_between_compare_and_publication_is_never_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, source, spec = fixture(tmp_path)
    path = repo / delivery.fallback_destination(spec.destination)
    original = projections.transaction.create_exclusive

    def create(target: Path, content: bytes) -> None:
        target.write_bytes(b"unowned racing creation")
        original(target, content)

    monkeypatch.setattr(projections.transaction, "create_exclusive", create)
    assert projections.sync_repository(repo, [source]).blocking
    assert path.read_bytes() == b"unowned racing creation"
    assert not (repo / spec.destination).exists()


@pytest.mark.parametrize("case", ["consent", "lock"])
def test_final_write_rechecks_safety_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    repo, source, spec = fixture(tmp_path)
    assert not projections.sync_repository(repo, [source]).blocking
    fallback = repo / delivery.fallback_destination(spec.destination)
    before = fallback.read_bytes() + b"\nedit\n"
    fallback.write_bytes(before)
    consent_path = opt_in(repo)
    lock = repo / projections.LOCK_RELATIVE
    original = projections._atomic_write

    def write(path: Path, content: bytes) -> None:
        original(path, content)
        if case == "consent":
            consent_path.write_text("{}")
        else:
            lock.write_bytes(b"changed ownership")

    monkeypatch.setattr(projections, "_atomic_write", write)
    assert projections.sync_repository(repo, [source], overwrite_existing_managed=True).blocking
    assert fallback.read_bytes() == before


def test_empty_transaction_final_recheck_returns_original_error() -> None:
    def fail() -> None:
        raise OSError("consent withdrawn")

    with pytest.raises(OSError, match="empty transaction.*consent withdrawn"):
        projections.transaction.write(
            [], read=lambda p: None, atomic_write=lambda p, c: None, after_write=fail
        )
