"""Tests for machines.yaml location resolution (.agent-worktrees/ vs repo root)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg

_MIN = "machines:\n  m1:\n    display_name: M1\n"


def _write(p: Path, text: str = _MIN) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_prefers_canonical_over_root(tmp_path: Path):
    _write(tmp_path / ".agent-worktrees" / "machines.yaml")
    _write(tmp_path / "machines.yaml")
    assert cfg.machines_yaml_path(tmp_path) == tmp_path / ".agent-worktrees" / "machines.yaml"


def test_falls_back_to_root(tmp_path: Path):
    _write(tmp_path / "machines.yaml")
    assert cfg.machines_yaml_path(tmp_path) == tmp_path / "machines.yaml"


def test_missing_reports_canonical(tmp_path: Path):
    # Neither exists -> the canonical path is what errors should name.
    assert cfg.machines_yaml_path(tmp_path) == tmp_path / ".agent-worktrees" / "machines.yaml"


def test_load_reads_canonical(tmp_path: Path):
    _write(tmp_path / ".agent-worktrees" / "machines.yaml")
    entries = cfg.load_machines_yaml(tmp_path)
    assert "m1" in entries


def test_load_reads_legacy_root(tmp_path: Path):
    _write(tmp_path / "machines.yaml")
    entries = cfg.load_machines_yaml(tmp_path)
    assert "m1" in entries


def test_load_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        cfg.load_machines_yaml(tmp_path)


def test_load_merges_canonical_and_legacy_disjoint_keys(tmp_path: Path):
    """Regression (ThomasMichon/copilot-extensions#7914): adding the in-repo
    canonical file for container-fleet entries must never make every real
    facility machine in the legacy root file disappear -- the canonical
    file's own header documents this as strictly additive. Before the fix,
    ``load_machines_yaml`` read only whichever file ``machines_yaml_path``
    happened to resolve first, so the legacy roster vanished the instant a
    canonical file existed."""
    _write(
        tmp_path / ".agent-worktrees" / "machines.yaml",
        "machines:\n  container-fleet:\n    display_name: Container Fleet\n",
    )
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  worker-alpha:\n    display_name: Worker Alpha\n"
        "  worker-beta:\n    display_name: Worker Beta\n",
    )
    entries = cfg.load_machines_yaml(tmp_path)
    assert set(entries) == {"container-fleet", "worker-alpha", "worker-beta"}


def test_load_canonical_wins_on_key_collision(tmp_path: Path):
    _write(
        tmp_path / ".agent-worktrees" / "machines.yaml",
        "machines:\n  m1:\n    display_name: Canonical\n",
    )
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    display_name: Legacy\n",
    )
    entries = cfg.load_machines_yaml(tmp_path)
    assert entries["m1"].display_name == "Canonical"


def test_load_normalizes_machine_metadata(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        """machines:
  m1:
    display_name: M1
    role: worker
    description: "  General-purpose worker.  "
    capabilities: [" builds ", "", builds, tests, " tests "]
""",
    )
    entry = cfg.load_machines_yaml(tmp_path)["m1"]
    assert entry.description == "General-purpose worker."
    assert entry.capabilities == ["builds", "tests"]


def test_load_machine_metadata_defaults_empty(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    description:\n    capabilities:\n",
    )
    entry = cfg.load_machines_yaml(tmp_path)["m1"]
    assert entry.description == ""
    assert entry.capabilities == []


@pytest.mark.parametrize("value", ["builds", "{name: builds}", "123"])
def test_load_rejects_non_list_capabilities(tmp_path: Path, value: str):
    _write(
        tmp_path / "machines.yaml",
        f"machines:\n  m1:\n    capabilities: {value}\n",
    )
    with pytest.raises(ValueError, match="capabilities must be a list"):
        cfg.load_machines_yaml(tmp_path)


def test_load_rejects_non_string_description(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    description: [not, a, string]\n",
    )
    with pytest.raises(ValueError, match="description must be a string"):
        cfg.load_machines_yaml(tmp_path)


def test_load_rejects_non_mapping_ssh(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    ssh: not-a-mapping\n",
    )
    with pytest.raises(ValueError, match="ssh must be a mapping"):
        cfg.load_machines_yaml(tmp_path)


@pytest.mark.parametrize("layout", ["canonical", "legacy", "both", "empty"])
def test_machine_registry_acquisition_original_reads_and_merge(tmp_path, monkeypatch, layout):
    import builtins

    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition

    canonical = tmp_path / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
    legacy = tmp_path / "machines.yaml"
    if layout in ("canonical", "both", "empty"):
        _write(canonical, "machines: {}\n" if layout == "empty" else
               "machines:\n  common: {display_name: Canonical}\n")
    if layout in ("legacy", "both"):
        _write(legacy, "machines:\n  common: {display_name: Legacy}\n  extra: {}\n")
    native_open, native_file = builtins.open, Path.is_file
    calls = []

    def open_source(path, *args, **kwargs):
        if Path(path) in (canonical, legacy):
            calls.append(("open", Path(path)))
        return native_open(path, *args, **kwargs)

    def is_file(path):
        if path in (canonical, legacy):
            calls.append(("file", path))
        return native_file(path)

    monkeypatch.setattr(builtins, "open", open_source)
    monkeypatch.setattr(Path, "is_file", is_file)
    value = cfg.load_machines_yaml(tmp_path)
    original_calls = tuple(calls)
    calls.clear()
    with acquisition.capture_config_observations() as observations:
        actual = cfg.load_machines_yaml(tmp_path)
    assert actual == value
    expected = [("file", canonical), ("file", legacy)]
    if layout in ("legacy", "both"):
        expected.append(("open", legacy))
    if layout in ("canonical", "both", "empty"):
        expected.append(("open", canonical))
    assert calls == list(original_calls) == expected
    if layout == "empty":
        assert actual == {}
    else:
        assert actual["common"].display_name == ("Legacy" if layout == "legacy" else "Canonical")
    owned = [o for o in observations if o.stage == "machine-registry"]
    assert owned and owned[-1].authority is ScanAuthority.COMPLETE
    assert [o.source for o in observations if o.stage == "machine-registry-parser"] == [
        str(path) for operation, path in expected if operation == "open"
    ]
    assert any(o.stage == "config-owner" and o.authority is ScanAuthority.INDETERMINATE
               for o in observations), "parser child acquisition must not become a stable frontier"


@pytest.mark.parametrize("failure", ["malformed", "denied", "normalize"])
def test_machine_registry_acquisition_original_fatal_error_is_not_replayed(tmp_path, monkeypatch, failure):
    import builtins

    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition

    path = tmp_path / "machines.yaml"
    _write(path, "machines: [\n" if failure == "malformed" else _MIN)
    native_open = builtins.open
    error = PermissionError("synthetic private registry bytes")
    calls = []

    def open_source(candidate, *args, **kwargs):
        if Path(candidate) == path:
            calls.append("open")
            if failure == "denied":
                raise error
        return native_open(candidate, *args, **kwargs)

    def normalize(*args, **kwargs):
        calls.append("normalize")
        raise error

    monkeypatch.setattr(builtins, "open", open_source)
    if failure == "normalize":
        monkeypatch.setattr(cfg, "_mt_normalize_machine_entries", normalize)
    with acquisition.capture_config_observations() as observations:
        with pytest.raises(Exception) as caught:
            cfg.load_machines_yaml(tmp_path)
    if failure != "malformed":
        assert caught.value is error
    assert calls == (["open", "normalize"] if failure == "normalize" else ["open"])
    owned = [o for o in observations if o.stage == "machine-registry"]
    assert owned and owned[-1].authority is ScanAuthority.INDETERMINATE
    assert any(f.detail == type(caught.value).__name__ for o in owned for f in o.findings)
    assert "synthetic private" not in repr(observations)
    assert "machines: [" not in repr(observations)


def test_machine_registry_acquisition_overlay_failure_retains_original_fallback(tmp_path, monkeypatch):
    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition

    calls = []

    def load(**kwargs):
        calls.append(kwargs)
        raise ValueError("synthetic private config failure")

    monkeypatch.setattr(cfg, "load_config", load)
    with acquisition.capture_config_observations() as observations:
        with pytest.raises(FileNotFoundError) as caught:
            cfg.load_machines_yaml(tmp_path)
    assert calls == [{"include_control_plane_related_pr": False}]
    assert str(tmp_path / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml") in str(caught.value)
    failures = [o for o in observations if o.stage == "machine-registry-overlay"]
    assert any(o.authority is ScanAuthority.INDETERMINATE for o in failures)
    assert any(f.detail == "ValueError" for o in failures for f in o.findings)
    assert "synthetic private" not in repr(observations)


@pytest.mark.parametrize("callback", ["_parse_machines_yaml_file", "_mt_merge_machines_yaml",
                                    "_mt_normalize_machine_entries", "load_machines_yaml"])
def test_machine_registry_acquisition_selected_wrapper_cannot_borrow_completion(
    tmp_path, monkeypatch, callback,
):
    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition, machine_identity

    _write(tmp_path / "machines.yaml")
    original = getattr(cfg, callback)
    calls = []

    def selected(*args, **kwargs):
        calls.append(callback)
        monkeypatch.setattr(cfg, callback, original)
        return original(*args, **kwargs)

    monkeypatch.setattr(cfg, callback, selected)
    with acquisition.capture_config_observations() as observations:
        value = machine_identity.load_execution_registry(tmp_path)
    assert list(value) == ["m1"]
    assert calls == [callback]
    assert getattr(cfg, callback) is original
    assert any(
        o.stage.startswith("machine-registry") and o.authority is ScanAuthority.INDETERMINATE
        and any(f.reason == "owner-coverage-unavailable" for f in o.findings)
        for o in observations
    ), "the callable selected before its self-restoration is still a replacement"


# ---------------------------------------------------------------------------
# State-root config-overlay (E1e): a stateless harness with no machines.yaml of
# its own redirects to the bound knowledge repo's machines.yaml.
# ---------------------------------------------------------------------------

def _stateless_config(harness_anchor, *, knowledge_repo="knowledge", stateless=True):
    return cfg.Config(
        srcroot="/src", machine="test", platform="linux",
        repo_name="harness", knowledge_repo=knowledge_repo,
        repos={"harness": cfg.RepoConfig(
            anchor=str(harness_anchor),
            worktree_root=str(harness_anchor) + ".wt",
            default_branch="main", remote="origin", stateless=stateless)},
    )


def _bind(monkeypatch, harness, knowledge, *, stateless=True):
    from agent_worktrees import state_root as sr

    def load_without_pr_discovery(*, include_control_plane_related_pr):
        assert include_control_plane_related_pr is False
        return _stateless_config(harness, stateless=stateless)

    monkeypatch.setattr(
        cfg, "load_config",
        load_without_pr_discovery)
    monkeypatch.setattr(
        sr, "_checkout_path",
        lambda name: str(knowledge) if name == "knowledge" else None)


@pytest.mark.parametrize("layout", ["canonical", "legacy"])
def test_machine_registry_acquisition_report_uses_original_overlay_order(tmp_path, monkeypatch, layout):
    import builtins
    from types import SimpleNamespace

    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition, state_root

    harness, knowledge, unused = (tmp_path / name for name in ("harness", "knowledge", "unused"))
    selected = knowledge / (cfg.INREPO_CONFIG_DIRNAME if layout == "canonical" else "") / "machines.yaml"
    _write(selected, "machines: {}\n")
    config = _stateless_config(harness)
    calls = []
    native_exists, native_open = Path.exists, builtins.open

    def sources(value, **kwargs):
        assert value is config
        assert kwargs == {"base_anchor": str(harness), "family": "registry"}
        calls.append("sources")
        return [SimpleNamespace(anchor=str(root)) for root in (harness, knowledge, unused)]

    def exists(path):
        calls.append(("exists", path))
        return native_exists(path)

    def open_source(path, *args, **kwargs):
        calls.append(("open", Path(path)))
        return native_open(path, *args, **kwargs)

    monkeypatch.setattr(state_root, "config_source_anchors", sources)
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(builtins, "open", open_source)
    value = cfg.load_machines_yaml(harness, config=config)
    original_calls = tuple(calls)
    calls.clear()
    report = cfg.load_machines_yaml_report(harness, config=config)
    expected = [("exists", harness / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"),
                ("exists", harness / "machines.yaml"), "sources",
                ("exists", knowledge / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml")]
    if layout == "legacy":
        expected.append(("exists", selected))
    expected.extend([("exists", selected), ("open", selected)])
    assert calls == list(original_calls) == expected
    assert report.value == value == {}
    assert report.authority is ScanAuthority.INDETERMINATE and report.reusable is False
    assert not any(o.authority is ScanAuthority.ABSENT for o in report.observations)
    assert any(o.source == "machine-registry-config-sources" and o.findings for o in report.observations)
    assert acquisition.current_observations() is None


def test_machine_registry_acquisition_report_replacement_empty_value_stays_uncertain(tmp_path, monkeypatch):
    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition

    original = cfg.load_machines_yaml
    value = {}
    calls = []

    def replacement(*args, **kwargs):
        calls.append((args, kwargs))
        monkeypatch.setattr(cfg, "load_machines_yaml", original)
        return value

    monkeypatch.setattr(cfg, "load_machines_yaml", replacement)
    report = cfg.load_machines_yaml_report(tmp_path, require_alias=False)
    assert calls == [((tmp_path,), {"config": None, "require_alias": False})]
    assert report.value is value and report.reusable is False
    assert report.authority is ScanAuthority.INDETERMINATE
    assert any(f.reason == "owner-coverage-unavailable" for f in report.findings)
    assert acquisition.current_observations() is None


@pytest.mark.parametrize("replacement", [False, True], ids=["original-source-owner", "self-restoring-source-owner"])
def test_machine_registry_original_source_selection_is_borrowed_once(tmp_path, monkeypatch, replacement):
    import builtins
    from types import SimpleNamespace

    from dropin_registry import ScanAuthority

    from agent_worktrees import config_acquisition as acquisition, state_root

    harness, knowledge, local = (tmp_path / name for name in ("harness", "knowledge", "local"))
    harness.mkdir()
    (local / cfg.INREPO_CONFIG_DIRNAME).mkdir(parents=True)
    selected = knowledge / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
    _write(selected)
    config = _stateless_config(harness)
    calls = []
    native_open = builtins.open
    source_owner = state_root.config_source_anchors

    def resolve(value, **kwargs):
        assert value is config and kwargs == {"cwd": None}
        calls.append("state-root")
        return SimpleNamespace(requires_external=True, bound=True, path=str(knowledge), repo="knowledge")

    def open_source(path, *args, **kwargs):
        if Path(path) == selected:
            calls.append("document")
        return native_open(path, *args, **kwargs)

    def sources(*args, **kwargs):
        calls.append("replacement")
        monkeypatch.setattr(state_root, "config_source_anchors", source_owner)
        return source_owner(*args, **kwargs)

    monkeypatch.setattr(cfg, "project_dir", lambda project=None: local)
    monkeypatch.setattr(state_root, "resolve_state_root", resolve)
    monkeypatch.setattr(builtins, "open", open_source)
    if replacement:
        monkeypatch.setattr(state_root, "config_source_anchors", sources)
    report = cfg.load_machines_yaml_report(harness, config=config)
    assert list(report.value) == ["m1"]
    assert calls == (["replacement"] if replacement else []) + ["state-root", "document"]
    assert any(o.stage == "config-source-anchors" and o.authority is ScanAuthority.COMPLETE
               for o in report.observations)
    source_findings = [f for o in report.observations if o.source == "machine-registry-config-sources"
                       for f in o.findings]
    assert bool(source_findings) is replacement
    assert all(f.reason == "owner-coverage-unavailable" for f in source_findings)
    assert any(o.source == "machine-registry-document" for o in report.observations)
    assert any(o.source == "config-source-state-root" for o in report.observations)
    assert report.authority is ScanAuthority.INDETERMINATE and report.reusable is False
    assert acquisition.current_observations() is None


def test_overlay_redirects_to_knowledge(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(harness) == (
        knowledge / ".agent-worktrees" / "machines.yaml"
    )
    # ...and the loader follows the redirect.
    assert "m1" in cfg.load_machines_yaml(harness)


def test_overlay_redirects_to_knowledge_legacy_root(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / "machines.yaml")  # legacy root location in the overlay
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(harness) == knowledge / "machines.yaml"


def test_overlay_not_used_when_harness_has_own(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    _write(harness / ".agent-worktrees" / "machines.yaml")
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(harness) == (
        harness / ".agent-worktrees" / "machines.yaml"
    )


def test_overlay_only_for_launch_repo(tmp_path: Path, monkeypatch):
    # A non-default repo without machines.yaml must NOT be redirected.
    harness = tmp_path / "harness"
    harness.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(other) == (
        other / ".agent-worktrees" / "machines.yaml"
    )


def test_overlay_noop_when_not_stateless(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge, stateless=False)
    # not require-external -> report own canonical, no redirect
    assert cfg.machines_yaml_path(harness) == (
        harness / ".agent-worktrees" / "machines.yaml"
    )
