"""Pure acquisition metadata; filesystem/activation/drop-in owners are boundaries."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from dropin_registry import EntryDecision, EntryStatus, Finding, ScanAuthority, ScanSnapshot
from plugin_activation import ActivationReport, ActivePlugin

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/src/agent_worktrees"


@pytest.fixture
def api(monkeypatch):
    name = "promotion_related_acquisition"
    package = ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)

    def stub(child, **fields):
        module = ModuleType(f"{name}.{child}")
        module.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        setattr(package, child, module)
        return module

    def load(child):
        spec = importlib.util.spec_from_file_location(f"{name}.{child}", SOURCE / f"{child}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        setattr(package, child, module)
        spec.loader.exec_module(module)
        return module

    load("acquisition_report")
    related = load("related_acquisition")
    config = load("config_acquisition")
    return SimpleNamespace(related=related, config=config, stub=stub, load=load)


def test_entry_uncertainty_survives_complete_activation_without_discarding_positive(api, monkeypatch):
    root = Path("synthetic-plugin")
    plugin = ActivePlugin("sample@example", "sample", "example", root, ("global",))
    finding = Finding("plugin-activation", "unknown@example", "indeterminate", "unreadable-source", detail="private settings body")
    owner = ActivationReport(ScanAuthority.COMPLETE, {
        plugin.source: EntryDecision.active(plugin),
        finding.entry: EntryDecision.indeterminate(finding),
    })
    monkeypatch.delenv("ACQUISITION_TEST_PLUGIN_ROOT", raising=False)
    api.stub("related", INSTALLED_PLUGINS_ENV="ACQUISITION_TEST_PLUGIN_ROOT",
             related_path=lambda root: SimpleNamespace(is_file=lambda: True),
             _PluginContributionAnchor=lambda path, name: path)
    report = api.related.installed_plugin_related_anchors_report(report=owner)
    assert report.value == [str(root)]
    assert report.authority is ScanAuthority.INDETERMINATE
    decision = next(o for o in report.observations if o.source == finding.entry)
    assert decision.status is owner.decisions[finding.entry].status
    assert any(f.entry == finding.entry and f.reason == finding.reason for f in report.findings)
    assert "private settings body" not in repr(report.observations)
    assert report.reusable is False
    assert report.missing_coverage and report.reuse_blockers


@pytest.mark.parametrize("decision_status", [EntryStatus.INDETERMINATE, None, EntryStatus.ACTIVE],
                         ids=["entry-uncertain", "registry-unavailable", "registry-with-entry"])
def test_retained_dropin_data_is_not_fresh_confirmed_positive(api, decision_status):
    entry, target = Path("synthetic-pointer"), Path("synthetic-target")
    finding = Finding("config.d", str(entry), "indeterminate", "unreadable-source", detail="private YAML body")
    contribution = SimpleNamespace(owner="sample@example", target=target, raw_config={"setting": "retained-value"})
    if decision_status is EntryStatus.INDETERMINATE:
        decisions = {str(entry): EntryDecision.indeterminate(finding)}
    elif decision_status is EntryStatus.ACTIVE:
        decisions = {str(entry): EntryDecision.active(SimpleNamespace(raw_config={"setting": "unconfirmed"}))}
    else:
        decisions = {}
    owner = SimpleNamespace(
        authority=ScanAuthority.COMPLETE if decision_status is EntryStatus.INDETERMINATE else ScanAuthority.INDETERMINATE,
        findings=(finding,), active_entries={str(entry): contribution}, active_configs=[contribution],
        snapshot=ScanSnapshot("config.d", ScanAuthority.COMPLETE, decisions),
    )
    calls = []

    def scan(*args, **kwargs):
        calls.append("scan")
        return owner

    api.stub("config", _deep_merge=lambda base, override: {**base, **override})
    api.stub("config_dropins", scan_config_dropin_registry=scan, warn_config_dropin_findings=lambda report: None)
    report = api.config._load_config_d_report(Path("synthetic-config.d"))
    assert calls == ["scan"]
    assert report.value == contribution.raw_config
    assert report.authority is ScanAuthority.INDETERMINATE
    observation = next(o for o in report.observations if o.source == str(entry))
    assert observation.retained is True
    assert observation.status is decision_status
    assert observation.owner == contribution.owner and observation.target == str(target)
    assert "retained-value" not in repr(report.observations)
    assert "private YAML body" not in repr(report.observations)
    assert report.reusable is False


def test_machine_root_resolution_failure_is_owner_uncertainty(api):
    calls = []

    def project_dir(project=None):
        calls.append(project)
        raise ValueError("synthetic private resolution failure")

    api.stub("config", project_dir=project_dir)
    owner = api.load("machine_config")
    report = owner.machine_config_root_report("base", project="sample")
    assert calls == ["sample"]
    assert report.value is None
    assert report.authority is ScanAuthority.INDETERMINATE
    assert report.findings[0].detail == "ValueError"
    assert "synthetic private resolution failure" not in repr(report.observations)
    assert report.reusable is False


def test_machine_marker_failure_keeps_later_positive_and_marker_order(api, monkeypatch):
    import stat

    api.stub("config", project_dir=lambda project=None: "overlay")
    owner = api.load("machine_config")
    calls = []
    expected = [owner.os.path.join("overlay", marker) for marker in owner._MARKERS]
    original_stat = owner.os.stat

    def observed_stat(path, *args, **kwargs):
        if path not in expected:
            return original_stat(path, *args, **kwargs)
        calls.append(path)
        assert not args and not kwargs  # Default stat follows a symlink target.
        if path == expected[0]:
            raise PermissionError("synthetic private marker failure")
        assert path == expected[1]
        return SimpleNamespace(st_mode=stat.S_IFDIR)

    monkeypatch.setattr(owner.os, "stat", observed_stat)
    report = owner.machine_config_root_report("base", project="sample")
    assert calls == expected
    assert report.value == "overlay"
    assert report.authority is ScanAuthority.INDETERMINATE
    assert [(o.source, o.authority) for o in report.observations] == [
        (expected[0], ScanAuthority.INDETERMINATE),
        (expected[1], ScanAuthority.COMPLETE),
        ("overlay", ScanAuthority.COMPLETE),
    ]
    assert "synthetic private marker failure" not in repr(report.observations)


@pytest.mark.parametrize("marker_state", ["missing", "regular-file", "first-directory"])
def test_machine_marker_classification_keeps_absence_and_short_circuit(api, monkeypatch, marker_state):
    import stat

    api.stub("config", project_dir=lambda project=None: "overlay")
    owner = api.load("machine_config")
    calls = []
    expected = [owner.os.path.join("overlay", marker) for marker in owner._MARKERS]
    original_stat = owner.os.stat

    def observed_stat(path, *args, **kwargs):
        if path not in expected:
            return original_stat(path, *args, **kwargs)
        calls.append(path)
        if marker_state == "missing":
            raise FileNotFoundError(path)
        return SimpleNamespace(st_mode=stat.S_IFDIR if marker_state == "first-directory" else stat.S_IFREG)

    monkeypatch.setattr(owner.os, "stat", observed_stat)
    report = owner.machine_config_root_report("base", project="sample")
    if marker_state == "first-directory":
        assert calls == [owner.os.path.join("overlay", owner._MARKERS[0])]
        assert report.value == "overlay"
        assert report.authority is ScanAuthority.COMPLETE
    else:
        assert calls == [owner.os.path.join("overlay", marker) for marker in owner._MARKERS]
        assert report.value is None
        assert report.authority is ScanAuthority.ABSENT
    assert not report.findings


@pytest.mark.parametrize("fault_field", ["role", "delegate"])
def test_related_entry_report_isolates_conversion_fault_at_field_boundary(api, monkeypatch, fault_field):
    document = {"related": {
        "first": {}, "broken": {fault_field: 123}, "last": {},
    }}

    def convert(value):
        if value == 123:
            raise AttributeError("synthetic private conversion body")
        return ""

    fields = {
        "yaml": SimpleNamespace(safe_load=lambda text: document),
        "RelatedConfig": lambda **kw: SimpleNamespace(primary=kw.get("primary", ""), related=kw.get("related", {})),
        "RelatedEntry": lambda **kw: SimpleNamespace(**kw),
        "normalize_role": convert, "_parse_delegate": convert,
    }
    for name in ["_parse_locus", "normalize_ownership", "normalize_audience", "_parse_ai_attribution", "_parse_plugins", "_parse_related_pr"]:
        fields[name] = lambda value: None
    api.stub("related", **fields)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: "synthetic document")
    observations = []
    value = api.related.parse_file(Path("related.yaml"), observations)
    assert set(value.related) == {"first", "last"}
    assert any(o.authority is ScanAuthority.INDETERMINATE for o in observations)
    assert "synthetic private conversion body" not in repr(observations)
    with pytest.raises(AttributeError):
        api.related.parse_file(Path("related.yaml"))
