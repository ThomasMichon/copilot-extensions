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



def test_config_report_request_collection_restores_nested_failure_and_opaque_owners(api):
    value = object()
    failure = ValueError("synthetic private failure")
    inner = []
    calls = []

    def load(path, **options):
        calls.append((path, options))
        api.config.full_load_started()
        api.config.opaque_source(str(path))
        if path == "failed":
            raise failure
        if path == "outer":
            original = api.config.current_observations()
            inner.append(api.config.load_config_report("inner"))
            with pytest.raises(ValueError) as caught:
                api.config.load_config_report("failed")
            assert caught.value is failure
            assert api.config.current_observations() is original
        return value

    api.stub("config", load_config=load)
    outer = api.config.load_config_report("outer", project="sample",
        include_control_plane_related_pr=False, include_knowledge_preferences=False)
    assert api.config.current_observations() is None
    assert outer.value is inner[0].value is value
    assert {o.source for o in outer.observations} == {"load-config", "execution-machine", "outer"}
    assert {o.source for o in inner[0].observations} == {"load-config", "execution-machine", "inner"}
    assert calls[0] == ("outer", dict(project="sample", include_control_plane_related_pr=False,
                                    include_knowledge_preferences=False))
    assert outer.authority is ScanAuthority.INDETERMINATE
    assert "synthetic private failure" not in repr(outer.observations)
    assert outer.reusable is False


def test_config_report_unattributed_cached_value_is_not_replayed(api):
    calls = []
    value = object()

    def load(*args, **kwargs):
        calls.append((args, kwargs))
        return value

    api.stub("config", load_config=load)
    report = api.config.load_config_report("selected")
    assert len(calls) == 1
    assert report.value is value
    assert report.authority is ScanAuthority.INDETERMINATE
    assert [f.reason for f in report.findings] == ["cached-config-unattributed"]
    assert report.reusable is False


@pytest.mark.parametrize("mode", ["absent", "empty", "malformed", "partial", "snapshot", "replacement"])
def test_committed_config_report_preserves_canonical_acquisition_and_snapshot_metadata(api, monkeypatch, mode):
    calls = []
    text = {"empty": b"", "malformed": b"marker: [\n", "replacement": b"marker: \xff\n"}.get(mode, b"marker: fixture\n")
    packet = b"a" * 40 + b" blob " + str(len(text)).encode() + b"\n" + text + b"\n"

    def git(*args, **kwargs):
        calls.append(args)
        if args[0] == "symbolic-ref":
            return SimpleNamespace(returncode=1 if mode == "absent" else 0,
                                   stdout="refs/remotes/origin/main\n")
        if mode == "partial":
            if args == ("show", "origin/main:later.yaml"):
                return SimpleNamespace(returncode=0, stdout="marker: fixture\n")
            return SimpleNamespace(returncode=128, stdout="")
        assert args == ("cat-file", "--batch")
        return SimpleNamespace(returncode=0, stdout=packet)

    original_exists = api.config.source_exists

    def absent(path, stage):
        class AbsentPath(str):
            def exists(self):
                return False
        return original_exists(AbsentPath(str(path)), stage)

    api.stub("git_ops", git=git, GitError=RuntimeError)
    monkeypatch.setattr(api.config, "source_exists", absent)
    source = api.load("inrepo_config_source")
    candidates = (Path("first.yaml"), Path("later.yaml")) if mode == "partial" else (Path("config.yaml"),)
    if mode == "snapshot":
        with source.resolution_budget(reuse_snapshots=True):
            legacy = source.load_config_base(Path("synthetic-anchor"), candidates)
            legacy["marker"] = "caller mutation"
            report = source.load_config_base_report(Path("synthetic-anchor"), candidates)
        assert len(calls) == 2
    else:
        report = source.load_config_base_report(Path("synthetic-anchor"), candidates)
    expected_value = {"marker": "\ufffd"} if mode == "replacement" else {"marker": "fixture"} if mode in ("partial", "snapshot") else {}
    assert report.value == expected_value
    expected = "complete" if mode == "snapshot" else "indeterminate"
    assert report.authority.value == expected
    assert report.reusable is False
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["absent", "empty", "value", "malformed", "encoding", "unreadable"])
def test_strict_mapping_owner_preserves_original_read_and_consumer_provenance(api, mode):
    import errno

    api.stub("config")
    api.stub("config_dropins", scan_config_dropin_registry=lambda *a, **k: None)
    api.load("repos_registry_reader")
    api.load("repos_registry_transaction")
    owner = api.load("repos_configuration_sources")
    api.stub("git_ops", git=lambda *a, **k: None)
    consumer = api.load("inrepo_config_source")
    calls = []
    failure = OSError(errno.ELOOP, "private native failure body")
    content = {"empty": b"", "malformed": b"private_setting: [\n", "encoding": b"\xff"}.get(
        mode, b"setting: private-value\n",
    )

    class SyntheticSource:
        def __str__(self):
            return "synthetic-strict.yaml"

        def read_bytes(self):
            calls.append("read")
            if mode == "absent":
                raise FileNotFoundError("private disappearance body")
            if mode == "unreadable":
                raise failure
            return content

    path = SyntheticSource()
    expected_error = OSError if mode == "unreadable" else owner.ConfigurationError
    operations = (owner.mapping, owner.mapping_report,
                  lambda selected: consumer.load_config_file(selected, strict=True))
    for operation in operations:
        calls.clear()
        with api.config.capture_config_observations() as observations:
            if mode in ("malformed", "encoding", "unreadable"):
                with pytest.raises(expected_error) as caught:
                    operation(path)
                if mode == "unreadable":
                    assert caught.value is failure
                else:
                    assert caught.value.code == "invalid-composition"
            else:
                result = operation(path)
                value = result.value if operation is owner.mapping_report else result
                assert value == ({"setting": "private-value"} if mode == "value" else {})
                if operation is owner.mapping_report:
                    assert result.authority.value == ("absent" if mode == "absent" else "complete")
                    assert result.reusable is False
                    assert "stable-owner-input-frontier" in result.missing_coverage
                    observations = result.observations
                assert any(o.stage == "config-source-bytes" and o.source == str(path)
                           for o in observations)
                assert not any(o.source == "strict-config-file" for o in observations)
        assert calls == ["read"]
        assert api.config.current_observations() is None
        assert all(text not in repr(observations) for text in (
            "private_setting", "private-value", "private native failure body", "private disappearance body",
        ))


def _registered_owner(api, monkeypatch):
    sdk = ModuleType("config_migrate")
    sdk.ManagedFile = lambda *a, **k: None
    sdk.SchemaRegistry = lambda: SimpleNamespace(register=lambda *a, **k: None)
    sdk.migrate_doc = lambda doc, *args: (doc, False)
    sdk.run = lambda *args: []
    monkeypatch.setitem(sys.modules, "config_migrate", sdk)
    migration = api.load("config_migrations")

    class Registry:
        def __init__(self, **fields):
            self.repos, self.srcroot, self.account_map = {}, {}, {}
            self.__dict__.update(fields)

    api.stub("repos", VALID_CLASSES=("reference", "singleton", "worktree", "knowledge"),
             _LEGACY_TYPE_MAP={}, RepoEntry=lambda **fields: SimpleNamespace(**fields), ReposRegistry=Registry,
             normalize_class=lambda value: value or "reference",
             normalize_visibility=lambda value: value if value in ("public", "internal", "private") else "")
    owner = api.load("repos_registry_reader")
    api.load("repos_registry_transaction")
    return owner, migration


@pytest.mark.parametrize("mode", ["absent", "empty", "partial", "fields", "malformed", "unreadable", "unattributed-migration"])
def test_registered_owner_report_retains_original_acquisition_and_partial_values(api, monkeypatch, mode):
    owner, migration = _registered_owner(api, monkeypatch)
    if mode == "unattributed-migration":
        monkeypatch.setattr(migration, "migrate_loaded", lambda doc, schema_id: doc)
    calls = []
    content = {
        "empty": b"{}\n", "partial": b"repos:\n  good: {default_branch: dev}\n  bad: []\n",
        "fields": b"srcroot: []\naccount_map: []\nrepos:\n  good: {default_branch: dev}\n  lossy: {agent: maybe, tags: wrong, remote: [], visibility: unknown}\n",
        "malformed": b"repos: [\n",
    }.get(mode, b"{}\n")

    class SyntheticSource:
        def __str__(self):
            return "synthetic-registry.yaml"

        def resolve(self):
            calls.append("resolve")
            return self

        def read_bytes(self):
            calls.append("read")
            if mode == "absent":
                raise FileNotFoundError("private disappearance")
            if mode == "unreadable":
                raise PermissionError("private registry read failure")
            return content

    path = SyntheticSource()
    legacy = owner.read_registry(path)
    expected_calls = list(calls)
    calls.clear()
    report = owner.read_registry_report(path)
    assert calls == expected_calls
    assert calls[0] == "read" and calls.count("read") == 1
    assert report.value._baseline == legacy._baseline
    assert set(report.value.repos) == set(legacy.repos)
    assert report.authority.value == ("absent" if mode == "absent" else "complete" if mode == "empty" else "indeterminate")
    if mode in ("partial", "fields"):
        assert report.value.repos["good"].default_branch == "dev"
    assert report.reusable is False
    assert "private registry read failure" not in repr(report.observations)
    assert "private disappearance" not in repr(report.observations)


@pytest.mark.parametrize("mode", ["unavailable", "error", "success", "unchanged"])
def test_original_migration_branch_report_preserves_identity_without_second_attempt(api, monkeypatch, mode):
    _, migration = _registered_owner(api, monkeypatch)
    original, changed, calls = {"private-setting": "private-value"}, {"schema_version": 1}, []

    def migrate(doc, schema_id, registry):
        calls.append(doc)
        if mode == "error":
            raise ValueError("private migration failure body")
        return (original, False) if mode == "unchanged" else (changed, True)

    monkeypatch.setattr(migration, "_AVAILABLE", mode != "unavailable")
    monkeypatch.setattr(migration, "migrate_doc", migrate)
    with api.config.capture_config_observations() as observations:
        value = migration.migrate_loaded(original, migration.SCHEMA_REPOS)
    assert value is (changed if mode == "success" else original)
    assert calls == ([] if mode == "unavailable" else [original])
    assert any(o.stage == "config-migration" and o.source == migration.SCHEMA_REPOS
               and o.authority.value == ("complete" if mode in ("success", "unchanged") else "indeterminate")
               for o in observations)
    assert all(text not in repr(observations) for text in ("private-setting", "private-value", "private migration failure body"))


@pytest.mark.parametrize("mode", ["owned", "failed", "replacement"])
def test_registry_consumer_boundary_uses_only_original_request_observations(api, mode):
    from promotion_related_acquisition.acquisition_report import observe

    calls = []
    with api.config.capture_config_observations() as observations:
        observe(observations, "old-source", "repository-registry", ScanAuthority.COMPLETE)
        start = api.config.registry_call_started()
        if mode == "owned":
            calls.append("original-owner")
            observe(observations, "selected-source", "repository-registry", ScanAuthority.COMPLETE)
        elif mode == "failed":
            calls.append("original-owner")
            api.config.registry_call_failed("selected-source", PermissionError("private helper failure"))
        api.config.registry_call_finished(start, "selected-source")
    assert calls == ([] if mode == "replacement" else ["original-owner"])
    assert any(f.reason == "owner-coverage-unavailable" for o in observations for f in o.findings) == (mode != "owned")
    assert any(o.stage == "registry-consumer" for o in observations) == (mode == "failed")
    assert "private helper failure" not in repr(observations)


@pytest.mark.parametrize("kind, mode", [
    ("mapping", "empty"), ("mapping", "populated"), ("mapping", "partial"),
    ("registry", "empty"), ("registry", "populated"), ("registry", "partial"),
    ("base", "empty"), ("base", "populated"), ("base", "partial"),
])
def test_report_wrappers_retain_missing_owner_uncertainty_without_replay(api, monkeypatch, kind, mode):
    api.stub("config")
    api.stub("config_dropins", scan_config_dropin_registry=lambda *a, **k: None)
    registered, migration = _registered_owner(api, monkeypatch)
    transaction = sys.modules[registered.__package__ + ".repos_registry_transaction"]
    mapping = api.load("repos_configuration_sources")
    api.stub("git_ops", git=lambda *a, **k: pytest.fail("report replayed Git acquisition"))
    base = api.load("inrepo_config_source")
    calls = []

    class SyntheticSource:
        def __str__(self):
            return "synthetic-replacement.yaml"

        def read_bytes(self):
            calls.append("read")
            return b"{}\n"

        def exists(self):
            pytest.fail("report added a presence probe")

    path = SyntheticSource()
    if kind == "registry":
        registry_type = sys.modules[registered.__package__ + ".repos"].ReposRegistry
        value = registry_type(repos={} if mode == "empty" else {"good": SimpleNamespace(name="good")})
    else:
        value = {} if mode == "empty" else {"marker": "available"}

    def replacement(*args, **kwargs):
        calls.append("owner")
        if mode == "partial":
            transaction.read_bytes(path)
            if kind == "registry":
                migration.migrate_loaded({}, migration.SCHEMA_REPOS)
                calls.append("migration")
        return value

    monkeypatch.setattr(mapping, "parse_mapping", lambda *a: pytest.fail("report replayed parsing"))
    if kind == "mapping":
        monkeypatch.setattr(mapping, "mapping", replacement)
        report = mapping.mapping_report(path)
    elif kind == "registry":
        monkeypatch.setattr(registered, "read_registry", replacement)
        report = registered.read_registry_report(path)
    else:
        monkeypatch.setattr(base, "load_config_base", replacement)
        report = base.load_config_base_report(path, (Path("config.yaml"),))
    expected = ["owner", "read", "migration"] if mode == "partial" and kind == "registry" else (
        ["owner", "read"] if mode == "partial" else ["owner"])
    assert calls == expected
    assert report.value is value
    assert report.authority is ScanAuthority.INDETERMINATE
    assert any(f.reason == "owner-coverage-unavailable" for f in report.findings)


class _ProjectsSource:
    def __init__(self, calls, content, *, present=True, error=None, on_read=None):
        self.calls, self.content = calls, content
        self.present, self.error, self.on_read = present, error, on_read

    def __str__(self):
        return "synthetic-projects.yaml"

    def exists(self):
        self.calls.append("exists")
        return self.present

    def read_text(self, *, encoding):
        assert encoding == "utf-8"
        self.calls.append("read")
        if self.error is not None:
            raise self.error
        if self.on_read is not None:
            self.on_read()
        return self.content


def _projects_owner(api, monkeypatch):
    registered, migration = _registered_owner(api, monkeypatch)
    strict = api.load("strict_yaml")
    owner = api.load("projects_registry")
    return owner, migration, strict, registered


@pytest.mark.parametrize("mode", [
    "empty-document", "empty-mapping", "populated", "negative-exists", "read-error",
    "malformed", "invalid-shape", "migration-unavailable", "migration-error",
])
def test_projects_owner_retains_original_acquisition_outcomes(api, monkeypatch, mode):
    import yaml

    owner, migration, strict, _ = _projects_owner(api, monkeypatch)
    calls, decoded = [], []
    error = PermissionError("private original projects read")
    content = {
        "empty-document": "", "empty-mapping": "{}\n", "malformed": "projects: [\n",
        "invalid-shape": "projects: []\n",
    }.get(mode, "schema_version: 2\nprojects: {sample: {default_branch: 42, base_repo: true}}\n")
    source = _ProjectsSource(calls, content, present=mode != "negative-exists",
                             error=error if mode == "read-error" else None)
    native_parse = strict._StrictLoader.get_single_data

    def parse(loader):
        calls.append("parse")
        value = native_parse(loader)
        decoded.append(value)
        return value

    def migrate(doc, schema_id, registry):
        calls.append("migrate")
        assert schema_id == migration.SCHEMA_PROJECTS
        if mode == "migration-error":
            raise ValueError("private original migration body")
        return doc, False

    monkeypatch.setattr(strict._StrictLoader, "get_single_data", parse)
    monkeypatch.setattr(migration, "migrate_doc", migrate)
    monkeypatch.setattr(migration, "_AVAILABLE", mode != "migration-unavailable")
    value = None
    with api.config.capture_config_observations() as observations:
        if mode == "read-error":
            with pytest.raises(PermissionError) as caught:
                owner.parse_projects_registry(source)
            assert caught.value is error
        elif mode in ("malformed", "invalid-shape"):
            with pytest.raises((ValueError, yaml.YAMLError)):
                owner.parse_projects_registry(source)
        else:
            value = owner.parse_projects_registry(source)
    expected = ["exists"]
    if mode != "negative-exists":
        expected.append("read")
    if mode not in ("negative-exists", "read-error"):
        expected.append("parse")
    if mode not in ("negative-exists", "read-error", "malformed", "empty-document", "migration-unavailable"):
        expected.append("migrate")
    assert calls == expected
    if mode in ("negative-exists", "empty-document", "empty-mapping"):
        assert value == {"projects": {}}
    elif mode not in ("read-error", "malformed", "invalid-shape"):
        assert value is decoded[0]
        assert value["projects"]["sample"] == {"default_branch": 42, "base_repo": True}
    complete = mode in ("empty-document", "empty-mapping", "populated")
    owned = [o for o in observations if o.source == str(source) and o.stage == "projects-registry"]
    assert len(owned) == 1
    assert owned[0].authority is (ScanAuthority.COMPLETE if complete else ScanAuthority.INDETERMINATE)
    assert not any(o.authority is ScanAuthority.ABSENT for o in owned)
    if mode in ("migration-unavailable", "migration-error"):
        reason = "migration-unavailable" if mode == "migration-unavailable" else "migration-failed"
        assert any(o.source == migration.SCHEMA_PROJECTS and f.reason == reason
                   for o in observations for f in o.findings)
    assert api.config.current_observations() is None
    assert all(text not in repr(observations) for text in (
        "private original projects read", "private original migration body", "base_repo", "default_branch",
    ))


@pytest.mark.parametrize("mode", ["repository-first", "projects-fallback", "repository-failure", "opaque-reader"])
def test_adoption_defaults_keep_original_order_and_replacement_opacity(api, monkeypatch, mode):
    owner, _migration, _strict, registered = _projects_owner(api, monkeypatch)
    calls, discarded = [], []
    branch = "registry" if mode == "repository-first" else ""

    class RepositorySource:
        def __str__(self):
            return "synthetic-repos.yaml"

        def resolve(self):
            calls.append("repository-resolve")
            return self

        def read_bytes(self):
            calls.append("repository-read")
            return f"repos: {{sample: {{default_branch: '{branch}'}}}}\n".encode("utf-8")

    repository_source = RepositorySource()
    projects_source = _ProjectsSource(
        calls, "schema_version: 2\nprojects: {sample: {default_branch: 42, base_repo: true}}\n",
    )

    def repository():
        calls.append("repository")
        if mode == "repository-failure":
            raise PermissionError("private repository consumer failure")
        return registered.read_registry(repository_source)

    def projects():
        calls.append("projects")
        return owner.parse_projects_registry(projects_source)

    def replacement():
        discarded.append(projects())
        return {"projects": {"sample": {"default_branch": "replacement", "base_repo": False}}}

    monkeypatch.setattr(sys.modules[owner.__package__ + ".repos"], "read_registry", repository, raising=False)
    api.stub("installer", read_projects_registry=replacement if mode == "opaque-reader" else projects,
             _ORIGINAL_PROJECTS_READER=projects)
    helper = api.load("config_adoption_defaults")
    with api.config.capture_config_observations() as observations:
        value = helper.resolve_adoption_defaults("sample", "windows")
    assert calls[0] == "repository" and calls.count("repository") == calls.count("projects") == 1
    assert calls.count("repository-read") == (mode != "repository-failure")
    assert calls[-3:] == ["projects", "exists", "read"]
    assert value == ({"default_branch": "replacement", "base_repo": False} if mode == "opaque-reader" else {
        "default_branch": branch or "42", "base_repo": True,
    })
    assert any(o.source == str(projects_source) and o.stage == "projects-registry"
               and o.authority is ScanAuthority.COMPLETE for o in observations)
    assert any(o.source == "adoption-projects-registry" and o.findings for o in observations) == (mode == "opaque-reader")
    assert any(o.source == "adoption-repository-registry" and o.findings for o in observations) == (mode == "repository-failure")
    assert len(discarded) == (mode == "opaque-reader")
    assert not any(o.source == "adoption-default-registries" for o in observations)
    assert "private repository consumer failure" not in repr(observations)
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["same-globals-migration", "self-restoring-loader", "read-time-swap"])
def test_projects_callbacks_bind_actual_callable_identity_and_selection(api, monkeypatch, mode):
    from types import FunctionType

    owner, migration, strict, _ = _projects_owner(api, monkeypatch)
    calls, discarded = [], []
    value = {"schema_version": 2, "projects": {"sample": {"base_repo": True}}}
    native_loader = owner.load_projects_document
    native_parse = strict._StrictLoader.get_single_data

    def parse(loader):
        calls.append("parse")
        return native_parse(loader)

    def restore_loader(content):
        discarded.append(native_loader(content))
        monkeypatch.setattr(owner, "load_projects_document", native_loader)
        return value

    def swapped_loader(content):
        calls.append("replacement")
        return {"schema_version": 2, "projects": {"sample": {"base_repo": False}}}

    def replacement_migration(doc, schema_id):
        namespace = globals()
        original = namespace["_ORIGINAL_MIGRATE_LOADED"](doc, schema_id)
        namespace["_cohort_discarded"].append(original)
        return namespace["_cohort_value"]

    monkeypatch.setattr(strict._StrictLoader, "get_single_data", parse)
    if mode == "same-globals-migration":
        monkeypatch.setattr(migration, "_cohort_discarded", discarded, raising=False)
        monkeypatch.setattr(migration, "_cohort_value", value, raising=False)
        wrapper = FunctionType(replacement_migration.__code__, vars(migration))
        monkeypatch.setattr(migration, "migrate_loaded", wrapper)
    elif mode == "self-restoring-loader":
        monkeypatch.setattr(owner, "load_projects_document", restore_loader)
    source = _ProjectsSource(
        calls, "schema_version: 2\nprojects: {sample: {base_repo: true}}\n" if mode == "read-time-swap" else "projects: {}\n",
        on_read=(lambda: monkeypatch.setattr(owner, "load_projects_document", swapped_loader))
        if mode == "read-time-swap" else None,
    )
    with api.config.capture_config_observations() as observations:
        result = owner.parse_projects_registry(source)
    assert calls == ["exists", "read", "parse"]
    assert result["projects"]["sample"]["base_repo"] is True
    if mode == "read-time-swap":
        assert owner.load_projects_document is swapped_loader
    else:
        assert result is value and len(discarded) == 1 and discarded[0] is not value
    if mode == "self-restoring-loader":
        assert owner.load_projects_document is native_loader
    assert any(o.stage == "projects-parser" and o.authority is ScanAuthority.COMPLETE for o in observations)
    assert any(o.source == migration.SCHEMA_PROJECTS and o.stage == "config-migration"
               and o.authority is ScanAuthority.COMPLETE for o in observations)
    owned = [o for o in observations if o.source == str(source) and o.stage == "projects-registry"]
    assert len(owned) == 1
    assert owned[0].authority is (ScanAuthority.COMPLETE if mode == "read-time-swap" else ScanAuthority.INDETERMINATE)
    assert api.config.current_observations() is None


def _knowledge_owner(api, monkeypatch):
    import builtins
    import io
    import os
    import stat

    boundary = ModuleType("agent_procutil")
    boundary.no_window_kwargs = lambda: {"creationflags": 1234}
    monkeypatch.setitem(sys.modules, "agent_procutil", boundary)
    _registered_owner(api, monkeypatch)
    transport = ModuleType("machine_transport")
    for name in (
        "MachineEntry", "SSHEnvironment", "find_machine_entry", "machine_name", "detect_platform",
        "find_machine_metadata", "merge_machines_yaml", "normalize_machine_entries", "parse_machines_yaml_file",
    ):
        setattr(transport, name, SimpleNamespace)
    monkeypatch.setitem(sys.modules, "machine_transport", transport)
    for name in ("project_state", "machine_instructions"):
        api.stub(name)
    registry = api.load("registry_paths")
    monkeypatch.setattr(registry, "installation_context", lambda: {})
    api.stub("publication_deadline", DEFAULT_PUSH_TIMEOUT=30)
    api.stub("codename_config", CodenameConfig=SimpleNamespace, parse_codename=lambda value: value)
    api.stub("pr_observation_config", ObservationConfig=SimpleNamespace, parse_observation=lambda value: value)
    api.stub("pr_publication_config", PublicationChecksConfig=SimpleNamespace,
             parse_publication_checks=lambda value: value, parse_string_tuple=tuple)
    api.stub("pr_publication_labels", PublicationOrigin=SimpleNamespace, parse_origin=lambda value: value)
    api.stub("pr_related_binding", parse_repository=lambda value: value)
    api.stub("config_cache", ConfigCacheSession=SimpleNamespace,
             cached_load_config_scope=lambda: None, memoize_in_scope=lambda fn, *a, **k: fn(*a, **k))
    calls = []

    def git(*args, **kwargs):
        calls.append(("git", str(kwargs["cwd"])))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    api.stub("git_ops", git=git, GitError=RuntimeError)
    api.load("inrepo_config_source")
    owner = api.load("config")
    launch = Path("synthetic-launch")
    knowledge = Path("synthetic-knowledge")
    launch_file = launch / owner.CANONICAL_INREPO_CONFIG_DIR / "config.yaml"
    knowledge_file = knowledge / owner.CANONICAL_INREPO_CONFIG_DIR / "config.yaml"
    documents = {
        launch_file: "stateless: true\n",
        knowledge_file: "headless: true\ncopilot_profiles: [{name: portable}]\nsrcroot: excluded\n",
    }
    state = SimpleNamespace(registered=True, on_read=None, errors={}, parsed={})
    native_exists, native_stat, native_open, native_load = Path.exists, os.stat, builtins.open, owner.yaml.load

    def exists(path):
        if path.parts and path.parts[0] in (str(launch), str(knowledge)):
            calls.append(("exists", str(path)))
            return path in documents and documents[path] is not None
        return native_exists(path)

    def source_stat(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path) in documents:
            calls.append(("stat", str(path)))
            return SimpleNamespace(st_mode=stat.S_IFREG)
        return native_stat(path, *args, **kwargs)

    def read(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path) in documents:
            path = Path(path)
            calls.append(("read", str(path)))
            assert kwargs["encoding"] == "utf-8"
            if path in state.errors:
                raise state.errors[path]
            if state.on_read is not None:
                state.on_read(path)
            stream = io.StringIO(documents[path])
            stream.name = str(path)
            return stream
        return native_open(path, *args, **kwargs)

    def parse(stream, *args, **kwargs):
        name = getattr(stream, "name", None)
        path = Path(name) if name is not None else None
        if path in documents:
            calls.append(("parse", str(path)))
            assert kwargs["Loader"] is owner._YamlSafeLoader
        value = native_load(stream, *args, **kwargs)
        if path in documents:
            state.parsed[path] = value
        return value

    def registry():
        calls.append(("registry", "knowledge"))
        entry = SimpleNamespace(local_path=lambda platform: str(knowledge))
        return SimpleNamespace(repos={"knowledge": entry} if state.registered else {})

    monkeypatch.setattr(sys.modules[owner.__package__ + ".repos"], "read_registry", registry, raising=False)
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(os, "stat", source_stat)
    monkeypatch.setattr(builtins, "open", read)
    monkeypatch.setattr(owner.yaml, "load", parse)
    machine = {"knowledge_repo": "knowledge", "repos": {"launch": {"anchor": str(launch)}}}
    return SimpleNamespace(owner=owner, machine=machine, documents=documents, state=state, calls=calls,
                           launch=launch_file, knowledge=knowledge_file)


def _knowledge_report(api, source):
    owner = source.owner
    with api.config.capture_config_observations() as observations:
        value = api.config.knowledge_call(
            owner._load_knowledge_overlay_config, owner._KNOWLEDGE_OVERLAY_OWNER,
            source.machine, {}, "launch", "windows",
        )
    return api.config.AcquisitionReport("knowledge-preferences", value, tuple(observations))


@pytest.mark.parametrize("mode", ["unbound", "stateful", "unregistered", "invalid-binding"])
def test_knowledge_preference_owner_gate_decisions(api, monkeypatch, mode):
    source = _knowledge_owner(api, monkeypatch)
    if mode == "unbound":
        source.machine["knowledge_repo"] = ""
    elif mode == "invalid-binding":
        source.machine["knowledge_repo"] = object()
    elif mode == "stateful":
        source.documents[source.launch] = "stateless: false\n"
    else:
        source.state.registered = False
    report = _knowledge_report(api, source)
    assert report.value == {} and report.reusable is False
    reads = [path for event, path in source.calls if event == "read"]
    assert reads == ([] if mode in ("unbound", "invalid-binding") else [str(source.launch)])
    owned = [o for o in report.observations if o.stage == "knowledge-preferences"]
    assert len(owned) == 1
    assert owned[0].authority is (
        ScanAuthority.INDETERMINATE if mode == "invalid-binding" else ScanAuthority.COMPLETE
    )
    if mode == "invalid-binding":
        assert [(f.reason, f.detail) for f in owned[0].findings] == [
            ("knowledge-preferences-failed", "AttributeError"),
        ]
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["complete", "malformed", "missing", "denied"])
def test_knowledge_preference_child_reads_preserve_values_and_uncertainty(api, monkeypatch, mode):
    source = _knowledge_owner(api, monkeypatch)
    if mode == "malformed":
        source.documents[source.knowledge] = "copilot_profiles: [\n"
    elif mode == "missing":
        source.documents[source.knowledge] = None
    elif mode == "denied":
        source.state.errors[source.knowledge] = PermissionError("private preference read body")
    report = _knowledge_report(api, source)
    expected = [("read", str(source.launch)), ("parse", str(source.launch))]
    if mode != "missing":
        expected.append(("read", str(source.knowledge)))
    if mode not in ("missing", "denied"):
        expected.append(("parse", str(source.knowledge)))
    assert [(event, path) for event, path in source.calls if event in ("read", "parse")] == expected
    assert sum(event == "git" for event, _ in source.calls) == 2
    assert sum(event == "registry" for event, _ in source.calls) == 1
    assert report.reusable is False and report.authority is ScanAuthority.INDETERMINATE
    owned = [o for o in report.observations if o.stage == "knowledge-preferences"]
    assert len(owned) == 1 and owned[0].authority is ScanAuthority.COMPLETE
    if mode == "complete":
        assert report.value["headless"] is True and "srcroot" not in report.value
        assert report.value["copilot_profiles"] is source.state.parsed[source.knowledge]["copilot_profiles"]
        assert any(o.source == str(source.knowledge) and o.stage == "config-yaml"
                   and o.authority is ScanAuthority.COMPLETE for o in report.observations)
    else:
        assert report.value == {}
        child = [o for o in report.observations if o.source.startswith("synthetic-knowledge")]
        assert any(o.authority is ScanAuthority.INDETERMINATE for o in child)
        assert not any(o.authority is ScanAuthority.ABSENT and o.stage != "committed-head" for o in child)
    assert "private preference read body" not in repr(report.observations)
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", [
    "late-selection", "same-globals-reader", "self-restoring-reader", "same-globals-resolver",
    "self-restoring-resolver", "same-globals-preferences", "self-restoring-preferences",
])
def test_knowledge_preference_callbacks_capture_actual_selection(api, monkeypatch, mode):
    from types import FunctionType

    source = _knowledge_owner(api, monkeypatch)
    owner = source.owner
    if mode == "late-selection":
        def resolver(name, platform):
            source.calls.append(("replacement-resolver", name))
            return "synthetic-knowledge"

        def on_read(path):
            if path == source.launch:
                monkeypatch.setattr(owner, "_resolve_anchor_from_registry", resolver)

        source.state.on_read = on_read
    else:
        callback_name = {
            "reader": "_load_inrepo_config", "resolver": "_resolve_anchor_from_registry",
            "preferences": "_load_knowledge_overlay_config",
        }[mode.rsplit("-", 1)[1]]
        original = getattr(owner, callback_name)

        def wrapper(*args, **kwargs):
            if mode.startswith("self-restoring"):
                monkeypatch.setattr(owner, callback_name, original)
            return original(*args, **kwargs)

        replacement = FunctionType(wrapper.__code__, original.__globals__, closure=wrapper.__closure__)
        assert replacement.__globals__ is original.__globals__
        monkeypatch.setattr(owner, callback_name, replacement)
    report = _knowledge_report(api, source)
    assert report.value["copilot_profiles"] is source.state.parsed[source.knowledge]["copilot_profiles"]
    assert report.reusable is False and report.authority is ScanAuthority.INDETERMINATE
    owned = [o for o in report.observations if o.stage == "knowledge-preferences"]
    assert owned and owned[-1].authority is ScanAuthority.INDETERMINATE
    assert any(f.reason == "owner-coverage-unavailable" for o in owned for f in o.findings)
    reads = [path for event, path in source.calls if event == "read"]
    assert reads == [str(source.launch), str(source.knowledge)]
    if mode == "late-selection":
        assert [event for event, _ in source.calls if event in ("read", "parse", "replacement-resolver")] == [
            "read", "parse", "replacement-resolver", "read", "parse",
        ]
    elif mode.startswith("self-restoring"):
        assert getattr(owner, callback_name) is original
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["unselected", "selected", "helper-error", "resolver-error", "bound-method"])
def test_installation_context_original_decision_keeps_delegation_opaque(api, monkeypatch, mode):
    import os

    registry = api.load("registry_paths")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", " " if mode == "unselected" else "synthetic-context")
    result, root, calls = {"marketplaceId": "example"}, Path("synthetic-payload"), []
    error = ValueError("private helper bytes") if mode == "helper-error" else PermissionError("private context bytes")

    def resolve(**kwargs):
        calls.append("resolve")
        assert kwargs["payload_root"] is root and kwargs["environment"] is os.environ
        if mode == "resolver-error":
            raise error
        return result

    def rebound(**kwargs):
        pytest.fail("resolver method was selected after payload-root argument evaluation")

    helper = SimpleNamespace(resolve_registry_context=resolve)

    def select_helper():
        calls.append("helper")
        if mode == "helper-error":
            raise error
        return helper

    def payload():
        calls.append("payload")
        if mode == "bound-method":
            helper.resolve_registry_context = rebound
        return root

    monkeypatch.setattr(registry, "_helper", select_helper)
    monkeypatch.setattr(registry, "_payload_root", payload)
    with api.config.capture_config_observations() as observations:
        if mode.endswith("-error"):
            with pytest.raises(type(error)) as caught:
                registry.installation_context_call(registry.installation_context)
            assert caught.value is error
            value = None
        else:
            value = registry.installation_context_call(registry.installation_context)
            if mode == "selected":
                assert registry.installation_context_call(registry.installation_context) is result
    expected = [] if mode == "unselected" else ["helper"]
    if mode not in ("unselected", "helper-error"):
        expected += ["payload", "resolve"]
    assert calls == expected * (2 if mode == "selected" else 1)
    if not mode.endswith("-error"):
        assert value is (None if mode == "unselected" else result)
    report = api.config.AcquisitionReport("installation-context", value, tuple(observations))
    owned = [o for o in observations if o.stage == "installation-context"]
    assert len(owned) == (2 if mode == "selected" else 1)
    authority = ScanAuthority.INDETERMINATE if mode.endswith("-error") else ScanAuthority.COMPLETE
    assert all(o.authority is authority for o in owned)
    children = [o for o in observations if o.source == "installation-context-helper"]
    assert len(children) == (0 if mode == "unselected" else len(owned))
    assert all(o.authority is ScanAuthority.INDETERMINATE for o in children)
    if mode.endswith("-error"):
        assert any(f.detail == type(error).__name__ for o in owned for f in o.findings)
    assert "private" not in repr(observations)
    assert report.authority is (ScanAuthority.COMPLETE if mode == "unselected" else ScanAuthority.INDETERMINATE)
    assert report.reusable is False and api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["late-selection", "same-globals", "self-restoring", "nested-original"])
def test_installation_context_config_call_qualifies_actual_selected_owner(api, monkeypatch, mode):
    from types import FunctionType

    source = _knowledge_owner(api, monkeypatch)
    owner = source.owner
    registry = sys.modules[owner.__package__ + ".registry_paths"]
    original = registry._INSTALLATION_CONTEXT_OWNER
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "")
    calls, base = [], {"headless": True}

    def wrapper():
        calls.append("replacement")
        if mode == "self-restoring":
            monkeypatch.setattr(registry, "installation_context", original)
        assert original() is None
        if mode == "nested-original":
            assert original() is None
        return None

    selected = original if mode == "late-selection" else FunctionType(
        wrapper.__code__, original.__globals__, closure=wrapper.__closure__,
    )
    assert selected.__globals__ is original.__globals__

    def base_read(*args, **kwargs):
        calls.append("base")
        monkeypatch.setattr(registry, "installation_context", selected)
        return base

    monkeypatch.setattr(registry, "installation_context", lambda: pytest.fail("callback selected before base"))
    monkeypatch.setattr(owner.inrepo_config_source, "load_config_base", base_read)
    with api.config.capture_config_observations() as observations:
        value = owner._load_inrepo_config("synthetic-launch")
    assert value is base
    assert calls == (["base"] if mode == "late-selection" else ["base", "replacement"])
    report = api.config.AcquisitionReport("installation-context", value, tuple(observations))
    owned = [o for o in observations if o.stage == "installation-context"]
    completed = [o for o in owned if o.authority is ScanAuthority.COMPLETE]
    assert len(completed) == (2 if mode == "nested-original" else 1)
    if mode == "late-selection":
        assert len(owned) == 1 and report.authority is ScanAuthority.COMPLETE
    else:
        assert len(owned) == len(completed) + 1 and report.authority is ScanAuthority.INDETERMINATE
        assert owned[-1].authority is ScanAuthority.INDETERMINATE
        assert any(f.reason == "owner-coverage-unavailable" for f in owned[-1].findings)
    if mode == "self-restoring":
        assert registry.installation_context is original
    assert report.reusable is False and api.config.current_observations() is None


def _control_plane_owner(api, monkeypatch):
    calls, project, pr = [], object(), {"enabled": True}
    state = SimpleNamespace(anchor="control", plugins=["plugin"], error=None, rebind=False)

    def discover():
        calls.append("discover")
        return state.anchor

    def plugins():
        calls.append("plugins")
        return state.plugins

    def local_path():
        calls.append("local")
        return "control"

    def registry():
        calls.append("registry")
        return [SimpleNamespace(name="control", local_path=local_path)]

    def rebound(*args, **kwargs):
        pytest.fail("source callback was rebound after nested project argument evaluation")

    def load_project(name, **kwargs):
        calls.append(("project", name, kwargs))
        if state.rebind:
            monkeypatch.setattr(sources_module, "config_source_anchors", rebound)
        return project

    def sources(config, *, base_anchor):
        if config is not project:
            calls.append("execution-sources")
            return []
        assert base_anchor == "control"
        calls.append("sources")
        if state.error == "sources":
            raise PermissionError("private source body")
        return [SimpleNamespace(anchor="control", origin="harness"),
                SimpleNamespace(anchor="knowledge", origin="knowledge")]

    def graft(anchors):
        calls.append(("graft", tuple(anchors)))
        if state.error == "graft":
            raise ValueError("private graft body")
        return SimpleNamespace(related={
            "target": SimpleNamespace(pr=pr), "empty": SimpleNamespace(pr={}), "missing": SimpleNamespace(),
        })

    api.stub("machine_identity", configured_machine_binding=lambda *a: "fixture")
    api.stub("related", find_control_plane_anchor=discover, installed_plugin_related_anchors=plugins,
             _anchor_key=lambda anchor: calls.append(("key", anchor)) or anchor,
             config_contribution_anchor=lambda anchor, origin: f"{origin}:{anchor}", read_related_grafted=graft)
    sources_module = api.stub("state_root", config_source_anchors=sources)
    owner = _knowledge_owner(api, monkeypatch).owner
    monkeypatch.setattr(sys.modules[owner.__package__ + ".repos"], "list_repos", registry, raising=False)
    monkeypatch.setattr(owner, "load_project_config", load_project)
    return SimpleNamespace(owner=owner, state=state, calls=calls, pr=pr, sources=sources, module=sources_module)


@pytest.mark.parametrize("mode", ["unselected", "selected-bound-callee"])
def test_control_plane_original_decision_keeps_children_opaque(api, monkeypatch, mode):
    source = _control_plane_owner(api, monkeypatch)
    if mode == "unselected":
        source.state.anchor, source.state.plugins = None, []
    else:
        source.state.rebind = True
    with api.config.capture_config_observations() as observations:
        value = source.owner._control_plane_related_pr_map()
    if mode == "unselected":
        assert value == {} and source.calls == ["discover", "plugins"]
    else:
        assert value == {"target": source.pr} and value["target"] is source.pr
        assert source.calls == [
            "discover", "plugins", ("key", "control"), "registry", "local", "local", ("key", "control"),
            ("project", "control", {"include_control_plane_related_pr": False}), "sources",
            ("graft", ("plugin", "harness:control", "knowledge:knowledge")),
        ]
        assert source.module.config_source_anchors is not source.sources
    report = api.config.AcquisitionReport("control-plane-related-pr", value, tuple(observations))
    owned = [o for o in observations if o.stage == "control-plane-related-pr"]
    assert len(owned) == 1 and owned[0].authority is ScanAuthority.COMPLETE
    assert any(o.stage == "config-owner" and o.source == "control-plane-related-pr"
               and o.authority is ScanAuthority.INDETERMINATE for o in observations)
    assert report.authority is ScanAuthority.INDETERMINATE and report.reusable is False
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["sources", "graft"], ids=["inner-fallback", "outer-caught"])
def test_control_plane_caught_errors_preserve_fallback_and_sanitize(api, monkeypatch, mode):
    source = _control_plane_owner(api, monkeypatch)
    source.state.error = mode
    with api.config.capture_config_observations() as observations:
        value = source.owner._control_plane_related_pr_map()
    assert source.calls[:9] == [
        "discover", "plugins", ("key", "control"), "registry", "local", "local", ("key", "control"),
        ("project", "control", {"include_control_plane_related_pr": False}), "sources",
    ]
    if mode == "sources":
        assert value == {"target": source.pr} and value["target"] is source.pr
        assert source.calls[9:] == [("graft", ("plugin", "harness:control"))]
    else:
        assert value == {}
        assert source.calls[9:] == [("graft", ("plugin", "harness:control", "knowledge:knowledge"))]
    report = api.config.AcquisitionReport("control-plane-related-pr", value, tuple(observations))
    owned = [o for o in observations if o.stage == "control-plane-related-pr"]
    assert owned and owned[-1].authority is ScanAuthority.INDETERMINATE
    expected_type = "PermissionError" if mode == "sources" else "ValueError"
    assert any(f.detail == expected_type for o in owned for f in o.findings)
    assert "private source body" not in repr(observations) and "private graft body" not in repr(observations)
    assert report.authority is ScanAuthority.INDETERMINATE and report.reusable is False
    assert api.config.current_observations() is None


@pytest.mark.parametrize("mode", ["same-globals", "self-restoring", "nested-original"])
def test_control_plane_caller_qualifies_actual_selected_callback(api, monkeypatch, mode):
    from types import FunctionType

    source = _control_plane_owner(api, monkeypatch)
    owner, calls, built = source.owner, [], []
    original = owner._control_plane_related_pr_map
    replacement_pr = {"enabled": False}
    machine, global_path = Path("synthetic-machine.yaml"), Path("synthetic-global.yaml")

    def wrapper():
        calls.append("replacement")
        if mode == "self-restoring":
            monkeypatch.setattr(owner, "_control_plane_related_pr_map", original)
        assert original()["target"] is source.pr
        if mode == "nested-original":
            assert original()["target"] is source.pr
        return {"target": replacement_pr}

    replacement = FunctionType(wrapper.__code__, original.__globals__, closure=wrapper.__closure__)
    assert replacement.__globals__ is original.__globals__

    def read(path):
        calls.append(("yaml", path))
        if path == global_path:
            return {}
        assert path == machine
        monkeypatch.setattr(owner, "_control_plane_related_pr_map", replacement)
        return {"repo_name": "target", "platform": "windows", "machine": "fixture",
                "repos": {"target": {"anchor": "target"}}}

    def build(raw, anchor, worktree_root):
        built.append(raw)
        return SimpleNamespace(anchor=anchor)

    def premature():
        pytest.fail("control-plane callback was selected before config acquisition")

    monkeypatch.setattr(owner, "_control_plane_related_pr_map", premature)
    monkeypatch.setattr(owner, "global_config_path", lambda: global_path)
    monkeypatch.setattr(owner, "_load_yaml_safe", read)
    monkeypatch.setattr(owner, "_load_config_d", lambda *a, **k: {})
    monkeypatch.setattr(owner, "_load_inrepo_config", lambda *a, **k: {})
    monkeypatch.setattr(owner, "_resolve_adoption_defaults_from_registry", lambda *a: {})
    monkeypatch.setattr(owner, "derive_worktree_root", lambda anchor: "worktrees")
    monkeypatch.setattr(owner, "_build_repo_config", build)
    monkeypatch.setattr(owner, "_parse_profile_assignment", lambda *a: None)
    monkeypatch.setattr(owner, "detect_machine", lambda value=None: value or "fixture")
    report = api.config.load_config_report(machine, include_knowledge_preferences=False)
    assert calls == [("yaml", global_path), ("yaml", machine), "replacement"]
    assert report.value.repos["target"].anchor == "target"
    assert len(built) == 1 and built[0]["pr"] == replacement_pr
    original_calls = 2 if mode == "nested-original" else 1
    assert source.calls.count("discover") == original_calls
    assert source.calls.count("plugins") == original_calls
    assert source.calls.count("registry") == original_calls
    assert source.calls.count("local") == original_calls * 2
    assert source.calls.count("sources") == original_calls
    assert source.calls.count(("graft", ("plugin", "harness:control", "knowledge:knowledge"))) == original_calls
    assert source.calls[-1] == "execution-sources"
    if mode == "self-restoring":
        assert owner._control_plane_related_pr_map is original
    owned = [o for o in report.observations if o.stage == "control-plane-related-pr"]
    assert sum(o.authority is ScanAuthority.COMPLETE for o in owned) == original_calls
    assert owned[-1].authority is ScanAuthority.INDETERMINATE
    assert any(f.reason == "owner-coverage-unavailable" for f in owned[-1].findings)
    assert report.authority is ScanAuthority.INDETERMINATE and report.reusable is False
    assert api.config.current_observations() is None
