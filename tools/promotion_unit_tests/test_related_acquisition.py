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
    probes = load("source_probes")
    related = load("related_acquisition")
    config = load("config_acquisition")
    return SimpleNamespace(related=related, config=config, stub=stub, load=load, probes=probes)


def test_entry_uncertainty_survives_complete_activation_without_discarding_positive(api, monkeypatch):
    root = Path("synthetic-plugin")
    plugin = ActivePlugin("sample@example", "sample", "example", root, ("global",))
    finding = Finding("plugin-activation", "unknown@example", "indeterminate", "unreadable-source", detail="private settings body")
    owner = ActivationReport(ScanAuthority.COMPLETE, {
        plugin.source: EntryDecision.active(plugin),
        finding.entry: EntryDecision.indeterminate(finding),
    })
    monkeypatch.delenv("ACQUISITION_TEST_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(api.related, "has_plugin_manifest", lambda *a: True)
    monkeypatch.setattr(api.related, "probe_source", lambda *a: True)
    api.stub("related", INREPO_DIRNAME=".agent-worktrees", RELATED_FILENAME="related.yaml",
             INSTALLED_PLUGINS_ENV="ACQUISITION_TEST_PLUGIN_ROOT",
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
    if marker_state == "regular-file":
        assert all(f.reason == "incorrect-kind" for f in report.findings)
    else:
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


@pytest.mark.parametrize("outcome", ["missing", "wrong-kind", "loop", "file"])
def test_source_probe_classifies_one_following_stat_without_boolean_suppression(api, monkeypatch, outcome):
    import errno
    import os
    import stat

    path = Path("synthetic-source")
    calls = []
    original = os.stat

    def observed(candidate, *args, **kwargs):
        if candidate != path:
            return original(candidate, *args, **kwargs)
        calls.append((candidate, args, kwargs))
        if outcome == "missing":
            raise FileNotFoundError(candidate)
        if outcome == "loop":
            raise OSError(errno.ELOOP, "private lookup body")
        return SimpleNamespace(st_mode=stat.S_IFDIR if outcome == "wrong-kind" else stat.S_IFREG)

    monkeypatch.setattr(os, "stat", observed)
    observations = []
    assert api.probes.probe_source(path, "file", observations, "source") is (outcome == "file")
    assert calls == [(path, (), {})]
    if outcome == "loop":
        assert observations[0].authority is ScanAuthority.INDETERMINATE
    elif outcome == "wrong-kind":
        assert observations[0].findings[0].reason == "incorrect-kind"
    elif outcome == "missing":
        assert observations[0].authority is ScanAuthority.ABSENT
    else:
        assert observations == []
    assert "private lookup body" not in repr(observations)


def test_manifest_helpers_keep_later_positive_without_hiding_first_failure(api, monkeypatch):
    import errno
    import json
    import os
    import stat

    root = Path("synthetic-plugin")
    first, second = root / "plugin.json", root / ".claude-plugin" / "plugin.json"
    calls = []
    original = os.stat

    def observed(candidate, *args, **kwargs):
        if candidate not in (first, second):
            return original(candidate, *args, **kwargs)
        calls.append(candidate)
        if candidate == first:
            raise OSError(errno.ELOOP, "private manifest body")
        return SimpleNamespace(st_mode=stat.S_IFREG)

    monkeypatch.setattr(os, "stat", observed)
    observations = []
    assert api.related.has_plugin_manifest(root, observations)
    assert calls == [first, second]
    assert observations[0].authority is ScanAuthority.INDETERMINATE
    calls.clear()

    def read(candidate, *args, **kwargs):
        calls.append(candidate)
        if candidate == first:
            raise OSError(errno.ELOOP, "private manifest body")
        assert candidate == second
        return '{"name":"sample"}'

    api.stub("related", json=json)
    monkeypatch.setattr(Path, "read_text", read)
    assert api.related.plugin_manifest_name(root, observations) == "sample"
    assert calls == [first, second]
    assert any(o.authority is ScanAuthority.INDETERMINATE for o in observations)
    assert "private manifest body" not in repr(observations)


@pytest.mark.parametrize("operation", ["installed-anchor", "anchor-key"])
def test_resolution_helpers_keep_legacy_fallback_and_observe_failure_once(api, monkeypatch, operation):
    import errno
    import os

    anchor, installed = Path("synthetic-anchor"), Path("synthetic-installed")
    api.stub("related", _PluginContributionAnchor=type("PluginAnchor", (str,), {}),
             installed_plugins_root=lambda: installed)
    calls = []
    original = Path.resolve

    def resolve(candidate, *args, **kwargs):
        if candidate not in (anchor, installed):
            return original(candidate, *args, **kwargs)
        calls.append(candidate)
        raise OSError(errno.ELOOP, "private resolution body")

    monkeypatch.setattr(Path, "resolve", resolve)
    observations = []
    if operation == "installed-anchor":
        assert api.related.is_installed_plugin_anchor(anchor, observations) is False
        assert calls == [installed]
    else:
        assert api.related.anchor_key(anchor, observations) == os.path.normcase(os.path.abspath(str(anchor)))
        assert calls == [anchor]
    assert observations[0].authority is ScanAuthority.INDETERMINATE
    assert "private resolution body" not in repr(observations)



def _default_root_owner(api, monkeypatch):
    api.stub("config")
    environment = {"synthetic": "repository-identity-environment"}
    api.stub("git_ops", repository_identity_env=lambda: environment)
    api.stub("repos")
    boundary = ModuleType("agent_procutil")
    boundary.no_window_kwargs = lambda: {"creationflags": 1234}
    monkeypatch.setitem(sys.modules, "agent_procutil", boundary)
    sources = api.load("config_sources")
    owner = api.load("state_root")
    assert owner.no_window_kwargs is boundary.no_window_kwargs
    return sources, owner, environment


@pytest.mark.parametrize("outcome", ["timeout", "os-error", "nonzero", "empty", "none-output", "success"])
def test_default_git_root_owner_reports_one_unchanged_lookup_without_private_output(api, monkeypatch, outcome):
    import subprocess

    _, owner, environment = _default_root_owner(api, monkeypatch)
    calls = []

    def lookup(argv, **kwargs):
        calls.append((argv, kwargs))
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="private Git output", stderr="private Git error")
        if outcome == "os-error":
            raise OSError("private Git error")
        return SimpleNamespace(returncode=128 if outcome == "nonzero" else 0,
                               stdout=" root " if outcome == "success" else None if outcome == "none-output" else " ",
                               stderr="private Git error")

    monkeypatch.setattr(owner.subprocess, "run", lookup)
    legacy = owner._git_toplevel("synthetic-cwd")
    assert legacy == ("root" if outcome == "success" else None)
    assert len(calls) == 1
    old_call = calls.pop()
    report = owner._git_toplevel_report("synthetic-cwd")
    assert report.value == legacy
    assert calls == [old_call]
    argv, options = calls[0]
    assert argv == ["git", "-C", "synthetic-cwd", "rev-parse", "--show-toplevel"]
    assert options == {"capture_output": True, "text": True, "timeout": 10,
                       "env": environment, "stdin": subprocess.DEVNULL, "creationflags": 1234}
    assert report.authority is (ScanAuthority.COMPLETE if outcome == "success" else ScanAuthority.INDETERMINATE)
    assert "private Git" not in repr(report.observations)
    assert report.reusable is False


@pytest.mark.parametrize("selection", ["explicit", "timeout-fallback"])
def test_config_sources_uses_root_owner_once_and_preserves_explicit_bypass(api, monkeypatch, selection):
    import subprocess

    sources, owner, _ = _default_root_owner(api, monkeypatch)
    config = SimpleNamespace(repo_name="sample", default_repo=SimpleNamespace(
        anchor="fallback", requires_external_state_root=False, stateless=False,
    ))
    api.stub("machine_config", machine_config_root=lambda *a, **k: None,
             machine_config_root_report=lambda *a, **k: sources.AcquisitionReport("machine", None, ()))
    calls = []

    def lookup(argv, **kwargs):
        calls.append((argv, kwargs))
        if selection == "explicit":
            raise AssertionError("explicit base must not call Git")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], stderr="private Git error")

    monkeypatch.setattr(owner.subprocess, "run", lookup)
    report = sources.config_source_anchors_report(
        config, base_anchor="explicit" if selection == "explicit" else None, cwd="synthetic-cwd",
    )
    assert [(s.anchor, s.origin) for s in report.value] == [("explicit" if selection == "explicit" else "fallback", "harness")]
    assert len(calls) == (0 if selection == "explicit" else 1)
    assert report.authority is (ScanAuthority.COMPLETE if selection == "explicit" else ScanAuthority.INDETERMINATE)
    assert "private Git" not in repr(report.observations)



def test_default_root_fixture_loads_without_ambient_procutil(api, monkeypatch):
    attempts = []

    class DenyRealProcutil:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "agent_procutil":
                attempts.append(fullname)
                raise ModuleNotFoundError("real procutil is outside the pure boundary", name=fullname)
            return None

    monkeypatch.delitem(sys.modules, "agent_procutil", raising=False)
    monkeypatch.setattr(sys, "meta_path", [DenyRealProcutil(), *sys.meta_path])
    _, owner, _ = _default_root_owner(api, monkeypatch)
    boundary = sys.modules["agent_procutil"]
    assert isinstance(boundary, ModuleType)
    assert owner.no_window_kwargs is boundary.no_window_kwargs
    assert owner.no_window_kwargs() == {"creationflags": 1234}
    assert attempts == []
