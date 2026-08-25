from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    WarningTracker,
    atomic_write_text,
    scan_directory,
)


def _finding(entry: str, reason: str, *, status: str = "inactive") -> Finding:
    return Finding(
        registry="demo.d",
        entry=entry,
        status=status,
        reason=reason,
    )


def test_finding_is_json_friendly_and_fingerprint_stable():
    finding = Finding(
        registry="demo.d",
        entry="/x.json",
        status="inactive",
        reason="missing-target",
        target="/gone",
        owner="p@m",
        remedy="remove it",
    )
    assert finding.to_dict() == {
        "registry": "demo.d",
        "entry": "/x.json",
        "status": "inactive",
        "reason": "missing-target",
        "target": "/gone",
        "owner": "p@m",
        "remedy": "remove it",
    }
    assert finding.fingerprint() == finding.fingerprint()
    json.dumps(finding.to_dict())


def test_entry_decision_invariants():
    assert EntryDecision.active("value").status is EntryStatus.ACTIVE
    assert EntryDecision.advisory("value", _finding("x", "legacy")).findings
    assert EntryDecision.inactive(_finding("x", "bad")).value is None
    assert EntryDecision.indeterminate(_finding("x", "io")).value is None
    with pytest.raises(ValueError):
        EntryDecision(status=EntryStatus.ACTIVE)
    with pytest.raises(ValueError):
        EntryDecision(status=EntryStatus.INACTIVE, value="unexpected")
    with pytest.raises(ValueError):
        EntryDecision.advisory("value")
    with pytest.raises(ValueError):
        EntryDecision.inactive()
    with pytest.raises(ValueError):
        EntryDecision.indeterminate()
    with pytest.raises(ValueError):
        EntryDecision(
            status=EntryStatus.ACTIVE,
            value="value",
            findings=(_finding("x", "unexpected"),),
        )


def test_scan_absent_is_authoritative_empty(tmp_path):
    snapshot = scan_directory(
        tmp_path / "absent",
        lambda _path: EntryDecision.active("unused"),
        registry="demo.d",
    )
    assert snapshot.authority is ScanAuthority.ABSENT
    assert snapshot.reconcile({"old": "value"}) == {}


def test_scan_non_directory_is_indeterminate_and_keeps_previous(tmp_path):
    path = tmp_path / "registry"
    path.write_text("not a directory", encoding="utf-8")
    snapshot = scan_directory(
        path,
        lambda _path: EntryDecision.active("unused"),
        registry="demo.d",
    )
    assert snapshot.authority is ScanAuthority.INDETERMINATE
    assert snapshot.findings[0].reason == "registry-indeterminate"
    assert snapshot.reconcile({"old": "value"}) == {"old": "value"}


def test_complete_scan_is_sorted_and_isolates_entry_verdicts(tmp_path):
    (tmp_path / "b.json").write_text("inactive", encoding="utf-8")
    (tmp_path / "a.json").write_text("active", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("ignored", encoding="utf-8")

    def classify(path: Path) -> EntryDecision[str]:
        if path.read_text(encoding="utf-8") == "active":
            return EntryDecision.active(path.name)
        return EntryDecision.inactive(_finding(str(path), "invalid-entry"))

    snapshot = scan_directory(
        tmp_path,
        classify,
        registry="demo.d",
        suffixes=(".json",),
    )
    assert snapshot.authority is ScanAuthority.COMPLETE
    assert list(snapshot.decisions) == [
        str(tmp_path / "a.json"),
        str(tmp_path / "b.json"),
    ]
    assert snapshot.reconcile() == {str(tmp_path / "a.json"): "a.json"}
    assert [finding.reason for finding in snapshot.findings] == ["invalid-entry"]


def test_entry_oserror_is_indeterminate_and_retains_only_that_prior_value(tmp_path):
    good = tmp_path / "good.json"
    unreadable = tmp_path / "unreadable.json"
    removed = tmp_path / "removed.json"
    for path in (good, unreadable, removed):
        path.write_text("x", encoding="utf-8")

    def classify(path: Path) -> EntryDecision[str]:
        if path == unreadable:
            raise PermissionError("temporarily denied")
        if path == removed:
            return EntryDecision.inactive(_finding(str(path), "not-enabled"))
        return EntryDecision.active("new-good")

    snapshot = scan_directory(tmp_path, classify, registry="demo.d")
    desired = snapshot.reconcile(
        {
            str(good): "old-good",
            str(unreadable): "old-unreadable",
            str(removed): "old-removed",
            "gone-entry": "old-gone",
        }
    )
    assert desired == {
        str(good): "new-good",
        str(unreadable): "old-unreadable",
    }
    assert snapshot.decisions[str(unreadable)].status is EntryStatus.INDETERMINATE
    assert any(
        finding.entry == str(unreadable) and finding.reason == "entry-indeterminate"
        for finding in snapshot.findings
    )


def test_complete_empty_scan_removes_previous(tmp_path):
    snapshot = scan_directory(
        tmp_path,
        lambda _path: EntryDecision.active("unused"),
        registry="demo.d",
    )
    assert snapshot.authority is ScanAuthority.COMPLETE
    assert snapshot.reconcile({"old": "value"}) == {}


def test_suffix_matching_directory_is_definitively_invalid(tmp_path):
    entry = tmp_path / "not-a-file.json"
    entry.mkdir()
    called = False

    def classify(_path: Path) -> EntryDecision[str]:
        nonlocal called
        called = True
        return EntryDecision.active("unexpected")

    snapshot = scan_directory(
        tmp_path,
        classify,
        registry="demo.d",
        suffixes=(".json",),
    )
    assert called is False
    assert snapshot.decisions[str(entry)].status is EntryStatus.INACTIVE
    assert snapshot.findings[0].reason == "invalid-entry"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are unavailable")
def test_fifo_is_rejected_without_calling_classifier(tmp_path):
    entry = tmp_path / "blocking.json"
    os.mkfifo(entry)
    called = False

    def classify(_path: Path) -> EntryDecision[str]:
        nonlocal called
        called = True
        return EntryDecision.active("unexpected")

    snapshot = scan_directory(
        tmp_path,
        classify,
        registry="demo.d",
        suffixes=(".json",),
    )
    assert called is False
    assert snapshot.decisions[str(entry)].status is EntryStatus.INACTIVE


def test_symlink_or_reparse_entry_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("valid", encoding="utf-8")
    entry = tmp_path / "linked.json"
    try:
        entry.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    snapshot = scan_directory(
        tmp_path,
        lambda _path: EntryDecision.active("unexpected"),
        registry="demo.d",
        suffixes=(".json",),
    )
    assert snapshot.decisions[str(entry)].status is EntryStatus.INACTIVE
    assert snapshot.findings[0].reason == "invalid-entry"


def test_indeterminate_snapshot_keeps_previous():
    snapshot: ScanSnapshot[str] = ScanSnapshot(
        registry="demo.d",
        authority=ScanAuthority.INDETERMINATE,
        findings=(_finding("demo.d", "registry-indeterminate", status="indeterminate"),),
    )
    assert snapshot.reconcile({"old": "value"}) == {"old": "value"}


def test_warning_tracker_caps_deduplicates_and_reports_recovery():
    findings = tuple(_finding(f"/{i}.json", "missing-target") for i in range(5))
    tracker = WarningTracker(limit=2, repeat_after_seconds=60)
    first = tracker.select(findings, now=100)
    assert len(first.emitted) == 2
    assert first.suppressed == 3
    assert first.recovered == 0

    duplicate = tracker.select(findings, now=110)
    assert duplicate.emitted == ()
    assert duplicate.suppressed == 0

    repeated = tracker.select(findings, now=161)
    assert len(repeated.emitted) == 2
    assert repeated.suppressed == 3

    recovered = tracker.select(findings[:1], now=162)
    assert recovered.recovered == 4


def test_warning_tracker_validates_limits():
    with pytest.raises(ValueError):
        WarningTracker(limit=-1)
    with pytest.raises(ValueError):
        WarningTracker(repeat_after_seconds=-1)


def test_warning_fingerprint_ignores_rendering_metadata_but_not_reason_or_target():
    base = Finding(
        registry="demo.d",
        entry="/entry.json",
        status="inactive",
        reason="missing-target",
        target="/gone",
        owner="one@m",
        remedy="first remedy",
        detail="first detail",
    )
    rendering_changed = Finding(
        registry="demo.d",
        entry="/entry.json",
        status="indeterminate",
        reason="missing-target",
        target="/gone",
        owner="two@m",
        remedy="second remedy",
        detail="second detail",
    )
    reason_changed = Finding(
        registry="demo.d",
        entry="/entry.json",
        status="inactive",
        reason="not-enabled",
        target="/gone",
    )
    target_changed = Finding(
        registry="demo.d",
        entry="/entry.json",
        status="inactive",
        reason="missing-target",
        target="/other",
    )
    assert base.fingerprint() == rendering_changed.fingerprint()
    assert base.fingerprint() != reason_changed.fingerprint()
    assert base.fingerprint() != target_changed.fingerprint()

    tracker = WarningTracker(limit=10, repeat_after_seconds=60)
    assert tracker.select((base,), now=0).emitted == (base,)
    assert tracker.select((rendering_changed,), now=1).emitted == ()
    assert set(tracker.select((reason_changed, target_changed), now=2).emitted) == {
        reason_changed,
        target_changed,
    }


def test_atomic_write_text_replaces_exact_entry(tmp_path):
    path = tmp_path / "registry" / "entry.json"
    atomic_write_text(path, "one")
    assert path.read_text(encoding="utf-8") == "one"
    peer = path.with_name("peer.json")
    peer.write_text("peer", encoding="utf-8")

    atomic_write_text(path, "two")
    assert path.read_text(encoding="utf-8") == "two"
    assert peer.read_text(encoding="utf-8") == "peer"
    assert not list(path.parent.glob("*.tmp"))
