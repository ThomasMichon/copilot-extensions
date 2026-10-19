"""Real filesystem and independent-consumer contracts for venue preparation."""

import json
import hashlib
import base64
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from plugin_resolve.preparation import prepare
from plugin_resolve.reflection import build_plan
from plugin_resolve.registration import atomic_json
from plugin_resolve.resolve import resolve_repo_plugins
from plugin_resolve.conventions import SETTINGS_RELS
from plugin_resolve.reflection import selection_spec, selection_cli_args
from plugin_resolve.settings import read_repo_settings


def init_checkout(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def git_status(path):
    return subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"], text=True, capture_output=True,
        check=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout


def local_plan(root, name="sample", marketplace="local"):
    payload = root / ".ai" / name
    payload.mkdir(parents=True, exist_ok=True)
    atomic_json(payload / "plugin.json", {
        "name": name, "version": "1.2.3", "author": {"name": "Synthetic Publisher"},
    })
    atomic_json(root / ".ai" / "marketplace.json", {
        "name": marketplace, "owner": {"name": "Synthetic Publisher"},
        "description": "Synthetic native marketplace",
        "plugins": [{"name": name, "source": name, "version": "1.2.3"}],
    })
    settings = root / ".github" / "copilot"
    settings.mkdir(parents=True, exist_ok=True)
    atomic_json(settings / "settings.json", {
        "enabledPlugins": {f"{name}@{marketplace}": True},
        "extraKnownMarketplaces": {
            marketplace: {"source": {"source": "directory", "path": "./.ai"}},
        },
    })
    return build_plan([{"source": f"{name}@{marketplace}"}], [root])


@pytest.mark.parametrize("value, expected", [
    ("sample@local", {"source": "sample@local", "enable": True}),
    ("/p/one with spaces=false", {"source": "/p/one with spaces=false", "enable": True}),
    ({"source": "C:\\plugins\\one with spaces=false", "enable": False},
     {"source": "C:\\plugins\\one with spaces=false", "enable": False}),
    ({"source": "sample@local"}, {"source": "sample@local", "enable": True}),
    ({"source": "/p/one with spaces", "enable": False},
     {"source": "/p/one with spaces", "enable": False}),
])
def test_selection_specs_and_unambiguous_cli_args(value, expected):
    assert selection_spec(value) == expected
    assert selection_cli_args(value) == [
        "--stage-plugin" if expected["enable"] else "--stage-plugin-disabled", expected["source"],
    ]


@pytest.mark.parametrize("value", [
    None, [], "", "  ", {}, {"source": ""}, {"source": 1},
    {"source": "sample@local", "enable": "false"},
    {"source": "sample@local", "enable": 0},
    {"source": "sample@local", "enable": None},
])
def test_selection_specs_reject_non_boolean_flags_and_empty_sources(value):
    with pytest.raises(ValueError):
        selection_spec(value)
    with pytest.raises(ValueError):
        selection_cli_args(value)


def _expect_installer_settings(monkeypatch, target):
    local = target / ".github" / "copilot" / "settings.local.json"
    if local.exists():
        monkeypatch.setenv("VENUE_EXPECTED_SETTINGS_SHA256", hashlib.sha256(local.read_bytes()).hexdigest())
    else:
        monkeypatch.delenv("VENUE_EXPECTED_SETTINGS_SHA256", raising=False)


@pytest.mark.parametrize("layer", SETTINGS_RELS)
def test_every_native_remote_layer_overrides_overlapping_host_origin(tmp_path, monkeypatch, layer):
    target = tmp_path / "target"
    init_checkout(target)
    path = target.joinpath(*layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    origin = {"source": {"source": "github", "repo": "example/native-marketplace"}}
    atomic_json(path, {
        "enabledPlugins": {"native@remote": True}, "extraKnownMarketplaces": {"remote": origin},
    })
    host = local_plan(tmp_path / "host", name="native", marketplace="remote")
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    _expect_installer_settings(monkeypatch, target)
    first = prepare({"repo": str(target), **host}, copilot_command=command)
    payload = Path(first["native@remote"])
    assert "installation" in payload.parts
    assert json.loads((payload / "plugin.json").read_text())["version"] == "2.3.4"
    assert list((target / ".git").rglob(".installation-complete.json"))
    assert prepare({"repo": str(target), **host}, copilot_command=command) == first
    receipt = json.loads((target / ".git" / "copilot-venue-plugins" / "ownership.json").read_text())
    assert ("remote" in receipt["marketplaceOrigins"]) == (layer == SETTINGS_RELS[-1])

    # A lower, still-authored file changes in place; a generated projection is
    # not authority to retain the previous origin.
    changed = json.loads(path.read_text())
    replacement = {"source": {"source": "github", "repo": "example/replacement-marketplace"}}
    changed["extraKnownMarketplaces"]["remote"] = replacement
    atomic_json(path, changed)
    _expect_installer_settings(monkeypatch, target)
    again = prepare({"repo": str(target), **host}, copilot_command=command)
    assert again["native@remote"] != first["native@remote"]
    assert len(list((target / ".git").rglob(".installation-complete.json"))) == 2


@pytest.mark.parametrize("layer", SETTINGS_RELS)
def test_native_local_origin_reloads_payload_and_new_catalog_entries(tmp_path, layer):
    target = tmp_path / "target"
    local_plan(target)
    base = target / ".github" / "copilot" / "settings.json"
    original = json.loads(base.read_text())
    path = target.joinpath(*layer)
    if path != base:
        base.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, original)
    init_checkout(target)
    first = prepare({"repo": str(target), "plugins": []})
    manifest = target / ".ai" / "sample" / "plugin.json"
    value = json.loads(manifest.read_text())
    value["version"] = "1.2.4"
    atomic_json(manifest, value)
    again = prepare({"repo": str(target), "plugins": []})
    assert json.loads((Path(again["sample@local"]) / "plugin.json").read_text())["version"] == "1.2.4"
    assert Path(first["sample@local"]).is_dir()
    extra = target / ".ai" / "extra"
    extra.mkdir()
    atomic_json(extra / "plugin.json", {"name": "extra", "version": "2.0.0"})
    catalog = target / ".ai" / "marketplace.json"
    value = json.loads(catalog.read_text())
    value["plugins"].append({"name": "extra", "source": "extra", "version": "2.0.0"})
    atomic_json(catalog, value)
    value = json.loads(path.read_text())
    value["enabledPlugins"]["extra@local"] = True
    atomic_json(path, value)
    result = prepare({"repo": str(target), "plugins": []})
    assert set(result) == {"sample@local", "extra@local"}
    assert json.loads((Path(result["extra@local"]) / "plugin.json").read_text())["version"] == "2.0.0"


@pytest.mark.parametrize("layer", SETTINGS_RELS)
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("repeated", [False, True])
def test_all_operator_enable_layers_win_both_directions_first_and_repeated(
    tmp_path, layer, native, repeated,
):
    target = tmp_path / "target"
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    plan["plugins"][0]["enable"] = not native
    if repeated:
        prepare({"repo": str(target), **plan})
    path = target.joinpath(*layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = json.loads(path.read_text()) if path.exists() else {}
    value.setdefault("enabledPlugins", {})["sample@local"] = native
    atomic_json(path, value)
    for _ in range(2):
        result = prepare({"repo": str(target), **plan})
        assert read_repo_settings(target).enabled["sample@local"] is native
        assert ("sample@local" in result) is native


def test_native_layer_order_wins_behind_owned_generated_values(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    prepare({"repo": str(target), **plan})
    for layer, enabled in zip(SETTINGS_RELS[:-1], (False, True, False)):
        path = target.joinpath(*layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, {"enabledPlugins": {"sample@local": enabled}})
    assert prepare({"repo": str(target), **plan}) == {}
    assert read_repo_settings(target).enabled["sample@local"] is False
    path = target.joinpath(*SETTINGS_RELS[-1])
    data = json.loads(path.read_text())
    data["enabledPlugins"]["sample@local"] = True
    atomic_json(path, data)
    plan["plugins"][0]["enable"] = False
    assert "sample@local" in prepare({"repo": str(target), **plan})


def test_underlying_operator_disable_applies_even_without_another_host_selection(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    prepare({"repo": str(target), **local_plan(tmp_path / "host")})
    atomic_json(target / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"sample@local": False},
    })
    assert prepare({"repo": str(target), "plugins": []}) == {}
    assert read_repo_settings(target).enabled["sample@local"] is False


def test_operator_enable_edit_relinquishes_ownership_even_when_returning_to_old_value(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    plan["plugins"][0]["enable"] = False
    prepare({"repo": str(target), **plan})
    path = target / ".github" / "copilot" / "settings.local.json"
    data = json.loads(path.read_text())
    data["enabledPlugins"]["sample@local"] = True
    atomic_json(path, data)
    assert "sample@local" in prepare({"repo": str(target), **plan})
    data = json.loads(path.read_text())
    data["enabledPlugins"]["sample@local"] = False
    atomic_json(path, data)
    plan["plugins"][0]["enable"] = True
    assert prepare({"repo": str(target), **plan}) == {}
    receipt = json.loads((target / ".git" / "copilot-venue-plugins" / "ownership.json").read_text())
    assert "sample@local" not in receipt["enabledPlugins"]


def test_explicit_former_projection_path_does_not_reacquire_operation_ownership(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    first = prepare({"repo": str(target), **plan})
    path = target / ".github" / "copilot" / "settings.local.json"
    original = json.loads(path.read_text())["extraKnownMarketplaces"]["local"]
    other = tmp_path / "other"
    local_plan(other)
    data = json.loads(path.read_text())
    data["extraKnownMarketplaces"]["local"] = {
        "source": {"source": "directory", "path": str(other / ".ai")},
    }
    atomic_json(path, data)
    prepare({"repo": str(target), **plan})
    data = json.loads(path.read_text())
    data["extraKnownMarketplaces"]["local"] = original
    atomic_json(path, data)
    result = prepare({"repo": str(target), **plan})
    assert Path(result["sample@local"]).is_relative_to(Path(original["source"]["path"]))
    assert Path(first["sample@local"]).is_dir()
    assert json.loads(path.read_text())["extraKnownMarketplaces"]["local"] == original
    receipt = json.loads((target / ".git" / "copilot-venue-plugins" / "ownership.json").read_text())
    assert "local" not in receipt["extraKnownMarketplaces"]


def test_saved_authored_local_remote_origin_outweighs_new_lower_base_origin(tmp_path, monkeypatch):
    target = tmp_path / "target"
    init_checkout(target)
    local = target / ".github" / "copilot" / "settings.local.json"
    local.parent.mkdir(parents=True)
    origin = {"source": {"source": "github", "repo": "example/local-authority"}}
    atomic_json(local, {"enabledPlugins": {"native@remote": True},
                        "extraKnownMarketplaces": {"remote": origin}})
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    _expect_installer_settings(monkeypatch, target)
    first = prepare({"repo": str(target), "plugins": []}, copilot_command=command)
    atomic_json(local.with_name("settings.json"), {
        "extraKnownMarketplaces": {"remote": {"source": {"source": "github", "repo": "example/lower"}}},
    })
    assert prepare({"repo": str(target), "plugins": []}, copilot_command=command) == first


def test_real_prepare_and_independent_consumer(tmp_path):
    host, target = tmp_path / "host", tmp_path / "target"
    init_checkout(target)
    plan = local_plan(host)
    result = prepare({"repo": str(target), **plan})
    assert set(result) == {"sample@local"}
    assert (Path(result["sample@local"]) / "plugin.json").is_file()
    assert prepare({"repo": str(target), **plan}) == result
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    consumer = subprocess.run(
        [sys.executable, "-c",
         "import sys,json;from plugin_resolve import resolve_repo_plugins;"
         "print(json.dumps(sorted(resolve_repo_plugins(sys.argv[1]).resolved)))", str(target)],
        env=env, capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert json.loads(consumer.stdout) == ["sample@local"]


def test_consecutive_complete_selections_prune_only_stale_owned_enablement(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    first = prepare({"repo": str(target), **local_plan(tmp_path / "first", name="sample")})
    second = prepare({"repo": str(target), **local_plan(tmp_path / "second", name="second")})
    assert set(second) == {"second@local"}
    assert set(resolve_repo_plugins(target).resolved) == {"second@local"}
    assert "sample@local" not in read_repo_settings(target).enabled
    assert prepare({"repo": str(target), "plugins": []}) == {}
    assert resolve_repo_plugins(target).resolved == {}
    assert all(Path(path).is_dir() for path in [*first.values(), *second.values()])


@pytest.mark.parametrize("layer", SETTINGS_RELS)
@pytest.mark.parametrize("decision", [False, True])
def test_authored_decisions_survive_empty_selection_and_owned_projection_pruning(
    tmp_path, layer, decision,
):
    target = tmp_path / "target"
    init_checkout(target)
    first = prepare({"repo": str(target), **local_plan(tmp_path / "host")})
    path = target.joinpath(*layer)
    value = json.loads(path.read_text()) if path.exists() else {}
    if layer == SETTINGS_RELS[-1] and decision:
        # Make the operator edit observable instead of forging receipt ownership.
        value["enabledPlugins"]["sample@local"] = False
        atomic_json(path, value)
        prepare({"repo": str(target), "plugins": []})
        value = json.loads(path.read_text())
    value.setdefault("enabledPlugins", {})["sample@local"] = decision
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)
    for _ in range(2):
        result = prepare({"repo": str(target), "plugins": []})
        assert read_repo_settings(target).enabled["sample@local"] is decision
        assert ("sample@local" in result) is decision
        assert set(resolve_repo_plugins(target).resolved) == set(result)
    assert Path(first["sample@local"]).is_dir()


@pytest.mark.parametrize("initial", [False, True])
def test_pruning_preserves_owned_value_changed_after_initial_snapshot(tmp_path, monkeypatch, initial):
    from plugin_resolve import preparation as module

    target = tmp_path / "target"
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    plan["plugins"][0]["enable"] = initial
    prepare({"repo": str(target), **plan})
    register = module.register_prepared
    interleaved = []

    def publish_after_snapshot(*args, **kwargs):
        if not interleaved:
            interleaved.append(True)
            changed = build_plan([{"source": "sample@local", "enable": not initial}], [tmp_path / "host"])
            prepare({"repo": str(target), **changed})
        return register(*args, **kwargs)

    monkeypatch.setattr(module, "register_prepared", publish_after_snapshot)
    result = prepare({"repo": str(target), "plugins": []})
    assert read_repo_settings(target).enabled["sample@local"] is (not initial)
    assert ("sample@local" in result) is (not initial)
    assert set(resolve_repo_plugins(target).resolved) == set(result)
    assert prepare({"repo": str(target), "plugins": []}) == {}
    assert "sample@local" not in read_repo_settings(target).enabled


def test_sibling_assets_and_explicit_disable_survive(tmp_path):
    host, target = tmp_path / "host", tmp_path / "target"
    init_checkout(target)
    local_plan(host)
    sibling = host / ".ai" / "support"
    sibling.mkdir()
    (sibling / "guide.md").write_text("support")
    skill = host / ".ai" / "sample" / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("[guide](../../../support/guide.md)")
    plan = build_plan([{"source": "sample@local"}], [host])
    result = prepare({"repo": str(target), **plan})
    assert (Path(result["sample@local"]).parent / "support" / "guide.md").read_text() == "support"
    path = target / ".github" / "copilot" / "settings.local.json"
    data = json.loads(path.read_text())
    data["enabledPlugins"]["sample@local"] = False
    data["unrelated"] = {"retained": True}
    atomic_json(path, data)
    assert prepare({"repo": str(target), **plan}) == {}
    assert json.loads(path.read_text())["unrelated"] == {"retained": True}


def test_failed_copy_never_publishes_settings(tmp_path):
    plan = local_plan(tmp_path / "host")
    plan["plugins"][0]["archive"] = "broken"
    target = tmp_path / "target"
    init_checkout(target)
    with pytest.raises(Exception):
        prepare({"repo": str(target), **plan})
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


def test_concurrent_subprocess_registration_retains_both(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    plans = [local_plan(tmp_path / f"host-{name}", name) for name in ("first", "second")]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    ready = tmp_path / "ready"
    ready.mkdir()
    child_script = """
import contextlib,json,sys,time
from pathlib import Path
from plugin_resolve import preparation as module
original=module.checkout_lock
@contextlib.contextmanager
def overlap(directory):
    if directory.parent.name == 'marketplaces':
        (Path(sys.argv[2])/sys.argv[3]).write_text('initial snapshot read')
        deadline=time.monotonic()+15
        while len(list(Path(sys.argv[2]).iterdir())) != 2:
            assert time.monotonic()<deadline, 'other initial snapshot did not arrive'
            time.sleep(0.01)
    with original(directory):
        yield
module.checkout_lock=overlap
module.prepare(json.loads(sys.argv[1]))
"""
    # Concurrent means both complete preparations have read their initial
    # snapshot before either publishes; a later complete preparation may prune.
    children = [
        subprocess.Popen(
            [sys.executable, "-c", child_script,
             json.dumps({"repo": str(target), **plan}), str(ready), str(index)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for index, plan in enumerate(plans)
    ]
    for child in children:
        _out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
    assert set(resolve_repo_plugins(target).resolved) == {"first@local", "second@local"}
    assert set(prepare({"repo": str(target), **plans[1]})) == {"second@local"}
    assert prepare({"repo": str(target), "plugins": []}) == {}


@pytest.mark.parametrize("layer", SETTINGS_RELS)
@pytest.mark.parametrize("changed", [False, True])
def test_local_origin_change_during_install_refuses_before_publication(tmp_path, monkeypatch, layer, changed):
    from plugin_resolve import preparation as module

    target = tmp_path / "target"
    local_plan(target)
    base = target / ".github" / "copilot" / "settings.json"
    operator_path = target.joinpath(*layer)
    if operator_path != base:
        data = json.loads(base.read_text())
        base.unlink()
        operator_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(operator_path, data)
    init_checkout(target)
    first = prepare({"repo": str(target), "plugins": []})
    replacement = tmp_path / "replacement"
    local_plan(replacement)
    manifest = replacement / ".ai" / "sample" / "plugin.json"
    data = json.loads(manifest.read_text())
    data["version"] = "9.8.7"
    atomic_json(manifest, data)
    state = target / ".git" / "copilot-venue-plugins"
    settings = target / ".github" / "copilot" / "settings.local.json"
    catalog = state / "marketplaces" / "local" / "marketplace.json"
    receipt = state / "ownership.json"
    before = settings.read_bytes(), catalog.read_bytes(), receipt.read_bytes()
    _expect_installer_settings(monkeypatch, target)
    install = module._install
    edits = []

    def interleave(*args, **kwargs):
        installed = install(*args, **kwargs)
        data = json.loads(operator_path.read_text())
        if changed:
            data["extraKnownMarketplaces"]["local"] = {
                "source": {"source": "directory", "path": str(replacement / ".ai")},
            }
        atomic_json(operator_path, data)
        edits.append(operator_path.read_bytes())
        return installed

    monkeypatch.setattr(module, "_install", interleave)
    request = _remote_request(target)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    if changed:
        with pytest.raises(ValueError, match="local marketplace origin changed"):
            prepare(request, copilot_command=command)
        assert operator_path.read_bytes() == edits[0]
        assert settings.read_bytes() == (edits[0] if operator_path == settings else before[0])
        assert catalog.read_bytes() == before[1]
        assert receipt.read_bytes() == before[2]
        assert not (state / "marketplaces" / "synthetic" / "marketplace.json").exists()
    else:
        result = prepare(request, copilot_command=command)
        assert set(result) == {"sample@local", "remote@synthetic"}
        assert json.loads((Path(result["sample@local"]) / "plugin.json").read_text())["version"] == "1.2.3"
    assert Path(first["sample@local"]).is_dir()


def test_real_remote_install_and_copy_both_precede_registration(tmp_path):
    plan = local_plan(tmp_path / "host")
    plan["plugins"].append({
        "source": "remote@synthetic", "enable": True,
        "marketplace": {"source": {"source": "github", "repo": "example/marketplace"}},
    })
    target = tmp_path / "target"
    init_checkout(target)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    result = prepare({"repo": str(target), **plan}, copilot_command=command)
    assert set(result) == {"sample@local", "remote@synthetic"}
    assert all((Path(path) / "plugin.json").exists() for path in result.values())


@pytest.mark.parametrize("layer", SETTINGS_RELS)
@pytest.mark.parametrize("late", ["missing-local", "remote", "resolved-local", "disabled"])
def test_operator_enable_during_install_is_validated_before_any_publication(
    tmp_path, monkeypatch, layer, late,
):
    from plugin_resolve import preparation as module

    host, target = tmp_path / "host", tmp_path / "target"
    local_plan(target, name="late", marketplace="operator")
    base = target / ".github" / "copilot" / "settings.json"
    data = json.loads(base.read_text())
    data["enabledPlugins"] = {}
    data["extraKnownMarketplaces"]["new-remote"] = {
        "source": {"source": "github", "repo": "example/late-marketplace"},
    }
    atomic_json(base, data)
    init_checkout(target)
    local_plan(host)
    manifest_path = host / ".ai" / "sample" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["dependencies"] = ["unselected-companion"]
    atomic_json(manifest_path, manifest)
    plan = build_plan([{"source": "sample@local"}], [host])
    baseline_diagnostics = []
    baseline = prepare({"repo": str(target), **plan}, diagnostics=baseline_diagnostics)
    assert set(baseline) == {"sample@local"}
    assert any(item["status"] == "outside-selection" for item in baseline_diagnostics)
    state = target / ".git" / "copilot-venue-plugins"
    local = target / ".github" / "copilot" / "settings.local.json"
    published_catalog = state / "marketplaces" / "local" / "marketplace.json"
    receipt = state / "ownership.json"
    previous = local.read_bytes(), published_catalog.read_bytes(), receipt.read_bytes()
    _expect_installer_settings(monkeypatch, target)
    source = {
        "remote": "late@new-remote", "resolved-local": "late@operator",
    }.get(late, "late@local")
    enabled = late != "disabled"
    operator_path = target.joinpath(*layer)
    written = []
    install = module._install

    def interleave(*args, **kwargs):
        result = install(*args, **kwargs)
        value = json.loads(operator_path.read_text()) if operator_path.exists() else {}
        value.setdefault("enabledPlugins", {})[source] = enabled
        operator_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(operator_path, value)
        written.append(operator_path.read_bytes())
        return result

    monkeypatch.setattr(module, "_install", interleave)
    plan["plugins"].append({
        "source": "remote@synthetic", "enable": True,
        "marketplace": {"source": {"source": "github", "repo": "example/installer-marketplace"}},
    })
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    diagnostics = []
    request = {"repo": str(target), **plan}
    if late in {"missing-local", "remote"}:
        with pytest.raises(ValueError, match=source):
            prepare(request, copilot_command=command, diagnostics=diagnostics)
        assert written
        assert local.read_bytes() == (written[0] if operator_path == local else previous[0])
        assert published_catalog.read_bytes() == previous[1]
        assert receipt.read_bytes() == previous[2]
        assert operator_path.read_bytes() == written[0]
        assert not (state / "marketplaces" / "synthetic" / "marketplace.json").exists()
        assert source in resolve_repo_plugins(target).unresolved
        assert "remote@synthetic" not in read_repo_settings(target).enabled
    else:
        result = prepare(request, copilot_command=command, diagnostics=diagnostics)
        assert written
        assert read_repo_settings(target).enabled[source] is enabled
        assert (source in result) is enabled
        assert set(resolve_repo_plugins(target).resolved) == set(result)
        assert resolve_repo_plugins(target).unresolved == []
        assert any(
            item["dependency"] == "unselected-companion@local" and item["status"] == "outside-selection"
            for item in diagnostics
        )


@pytest.mark.parametrize("layer", SETTINGS_RELS[:-1])
@pytest.mark.parametrize("mode", [
    "changed-remote", "changed-local", "inactive-remote", "cached-remote", "no-origin", "direct-local",
])
def test_late_enabled_owned_projection_requires_current_origin_proof(tmp_path, monkeypatch, layer, mode):
    from plugin_resolve import preparation as module

    target = tmp_path / "target"
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    plan["plugins"][0]["enable"] = False
    operator_path = target.joinpath(*layer)
    original_remote = {"source": {"source": "github", "repo": "example/original-catalog"}}
    if mode in {"changed-remote", "inactive-remote", "cached-remote"}:
        operator_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(operator_path, {
            "enabledPlugins": {"sample@local": False},
            "extraKnownMarketplaces": {"local": original_remote},
        })
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    assert prepare({"repo": str(target), **plan}, copilot_command=command) == {}
    state = target / ".git" / "copilot-venue-plugins"
    local = target / ".github" / "copilot" / "settings.local.json"
    catalog = state / "marketplaces" / "local" / "marketplace.json"
    receipt = state / "ownership.json"
    before = local.read_bytes(), catalog.read_bytes(), receipt.read_bytes()
    assert read_repo_settings(target).enabled["sample@local"] is False
    replacement = tmp_path / "replacement"
    local_plan(replacement)
    path = replacement / ".ai" / "sample" / "plugin.json"
    value = json.loads(path.read_text())
    value["version"] = "9.8.7"
    atomic_json(path, value)
    install = module._install
    edits = []
    _expect_installer_settings(monkeypatch, target)

    def interleave(*args, **kwargs):
        installed = install(*args, **kwargs)
        chosen = local if mode == "direct-local" else operator_path
        value = json.loads(chosen.read_text()) if chosen.exists() else {}
        value.setdefault("enabledPlugins", {})["sample@local"] = mode != "inactive-remote"
        if mode in {"changed-remote", "inactive-remote"}:
            value.setdefault("extraKnownMarketplaces", {})["local"] = {
                "source": {"source": "github", "repo": "example/new-authority"},
            }
        elif mode in {"changed-local", "direct-local"}:
            value.setdefault("extraKnownMarketplaces", {})["local"] = {
                "source": {"source": "directory", "path": str(replacement / ".ai")},
            }
        chosen.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(chosen, value)
        edits.append((chosen, chosen.read_bytes()))
        return installed

    monkeypatch.setattr(module, "_install", interleave)
    if mode in {"changed-remote", "changed-local"}:
        with pytest.raises(ValueError, match="native.*origin.*not prepared"):
            prepare(_remote_request(target), copilot_command=command)
        assert edits
        assert edits[0][0].read_bytes() == edits[0][1]
        assert (local.read_bytes(), catalog.read_bytes(), receipt.read_bytes()) == before
        assert not (state / "marketplaces" / "synthetic" / "marketplace.json").exists()
    else:
        result = prepare(_remote_request(target), copilot_command=command)
        assert edits
        assert ("sample@local" in result) is (mode != "inactive-remote")
        assert set(resolve_repo_plugins(target).resolved) == set(result)
        if mode == "cached-remote":
            assert "installation" in Path(result["sample@local"]).parts
            assert json.loads((Path(result["sample@local"]) / "plugin.json").read_text())["version"] == "2.3.4"
        if mode == "direct-local":
            assert Path(result["sample@local"]) == (replacement / ".ai" / "sample").resolve()


@pytest.mark.parametrize("origin", [
    {"source": "git", "url": "https://example.invalid/catalog.git"},
    {"source": "github", "repo": "example/catalog"},
])
@pytest.mark.parametrize("omit_author", [False, True])
def test_remote_installed_catalog_and_entry_survive_without_plugin_authorship(
    tmp_path, monkeypatch, origin, omit_author,
):
    target = tmp_path / "target"
    init_checkout(target)
    catalog = {
        "name": "synthetic", "owner": {"name": "Catalog Steward", "email": "catalog@example.invalid"},
        "description": "Catalog description", "metadata": {"pluginRoot": "./plugins", "tag": "retained"},
        "plugins": [{
            "name": "remote", "source": "./plugins/remote", "version": "2.3.4",
            "author": {"name": "Catalog Entry Author"}, "description": "Entry description",
            "category": "testing", "keywords": ["retained"], "strict": False,
        }],
    }
    path = tmp_path / "remote-catalog.json"
    atomic_json(path, catalog)
    monkeypatch.setenv("VENUE_MARKETPLACE_CATALOG", str(path))
    if omit_author:
        monkeypatch.setenv("VENUE_OMIT_PLUGIN_AUTHOR", "1")
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    request = {"repo": str(target), "plugins": [{
        "source": "remote@synthetic", "enable": True, "marketplace": {"source": origin},
    }]}
    original_request = json.dumps(request)
    result = prepare(request, copilot_command=command)
    root = target / ".git" / "copilot-venue-plugins" / "marketplaces" / "synthetic"
    published = json.loads((root / "marketplace.json").read_text())
    assert published["owner"] == catalog["owner"]
    assert published["description"] == catalog["description"]
    assert published["metadata"] == {"tag": "retained"}
    entry = next(item for item in published["plugins"] if item["name"] == "remote")
    assert {k: v for k, v in entry.items() if k != "source"} == {
        k: v for k, v in catalog["plugins"][0].items() if k != "source"
    }
    home = Path(result["remote@synthetic"]).parents[2]
    assert home.name != "installed-plugins"
    assert (home / "cache" / "marketplaces").is_dir()
    import shutil
    shutil.rmtree(home / "cache")
    monkeypatch.setenv("VENUE_FAIL_INSTALL", "1")
    assert prepare(json.loads(original_request), copilot_command=command) == result
    assert json.loads((root / "marketplace.json").read_text()) == published


def _remote_request(target):
    return {"repo": str(target), "plugins": [{
        "source": "remote@synthetic", "enable": True,
        "marketplace": {"source": {"source": "github", "repo": "example/catalog"}},
    }]}


def test_remote_cache_uses_native_catalog_over_distinct_legacy_and_nested_lookalikes(tmp_path, monkeypatch):
    target = tmp_path / "target"
    init_checkout(target)
    catalogs = []
    for label in ("native", "legacy", "nested"):
        value = {
            "name": "synthetic", "owner": {"name": label + " publisher"},
            "description": label + " catalog",
            "plugins": [{
                "name": "remote", "source": "./remote", "version": "2.3.4",
                "description": label + " entry", "keywords": [label], "strict": label != "native",
            }, {"name": label + "-only", "source": "./other", "version": "1.0.0"}],
        }
        path = tmp_path / (label + "-catalog.json")
        atomic_json(path, value)
        catalogs.append((path, value))
    monkeypatch.setenv("VENUE_MARKETPLACE_CATALOG", str(catalogs[0][0]))
    monkeypatch.setenv("VENUE_LEGACY_CATALOG", str(catalogs[1][0]))
    monkeypatch.setenv("VENUE_NESTED_CATALOG", str(catalogs[2][0]))
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    first = prepare(_remote_request(target), copilot_command=command)
    home = Path(first["remote@synthetic"]).parents[2]
    clone = home / "cache" / "marketplaces" / "opaque-clone"
    assert json.loads((clone / ".github" / "plugin" / "marketplace.json").read_text()) != (
        json.loads((clone / ".claude-plugin" / "marketplace.json").read_text())
    )
    published_path = home.parents[1] / "marketplace.json"
    published = json.loads(published_path.read_text())
    assert published["owner"] == catalogs[0][1]["owner"]
    assert published["description"] == "native catalog"
    entry = next(item for item in published["plugins"] if item["name"] == "remote")
    assert entry["description"] == "native entry"
    assert entry["keywords"] == ["native"] and entry["strict"] is False
    assert prepare(_remote_request(target), copilot_command=command) == first
    assert json.loads(published_path.read_text()) == published


def test_installed_metadata_ignores_nested_only_clones_and_cache_files(tmp_path):
    from plugin_resolve.preparation import _installed_metadata

    home = tmp_path / "installation"
    cache = home / "cache" / "marketplaces"
    document = {
        "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
        "plugins": [{"name": "remote", "source": "./remote", "version": "1.0.0"}],
    }
    nested = cache / "not-a-marketplace" / "tests" / ".github" / "plugin"
    nested.mkdir(parents=True)
    atomic_json(nested / "marketplace.json", document)
    atomic_json(cache / "freshness.json", document)
    (cache / "catalog.lock").write_text("locked", encoding="utf-8")
    assert _installed_metadata(home, "remote@synthetic", required=False) is None
    with pytest.raises(ValueError, match="missing or ambiguous"):
        _installed_metadata(home, "remote@synthetic")


def test_installed_metadata_still_rejects_competing_clone_roots(tmp_path):
    from plugin_resolve.preparation import _installed_metadata

    home = tmp_path / "installation"
    cache = home / "cache" / "marketplaces"
    for label in ("one", "two"):
        root = cache / label
        root.mkdir(parents=True)
        atomic_json(root / "marketplace.json", {
            "name": "synthetic", "owner": {"name": label + " publisher"},
            "plugins": [{"name": "remote", "source": "./remote", "version": "1.0.0"}],
        })
    with pytest.raises(ValueError, match="missing or ambiguous"):
        _installed_metadata(home, "remote@synthetic", required=False)


@pytest.mark.parametrize("linked", ["clone", "manifest"])
def test_installed_metadata_refuses_linked_clone_or_selected_manifest(tmp_path, linked):
    from plugin_resolve.preparation import _installed_metadata

    home = tmp_path / "installation"
    cache = home / "cache" / "marketplaces"
    cache.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    atomic_json(outside / "marketplace.json", {
        "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
        "plugins": [{"name": "remote", "source": "./remote", "version": "1.0.0"}],
    })
    try:
        if linked == "clone":
            (cache / "clone").symlink_to(outside, target_is_directory=True)
        else:
            root = cache / "clone"
            root.mkdir()
            (root / "marketplace.json").symlink_to(outside / "marketplace.json")
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    with pytest.raises(ValueError, match="Linked"):
        _installed_metadata(home, "remote@synthetic")


def _recorded_native_calls(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("available_catalog", [True, False])
def test_source_only_receipt_hydrates_without_duplicate_registration_or_install(
    tmp_path, monkeypatch, available_catalog,
):
    target = tmp_path / "target"
    init_checkout(target)
    log = tmp_path / "native-calls.jsonl"
    monkeypatch.setenv("VENUE_CALL_LOG", str(log))
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    first = prepare(_remote_request(target), copilot_command=command)
    payload = Path(first["remote@synthetic"])
    home = payload.parents[2]
    # Reproduce the actual old operation receipt; native registration, payload
    # and catalog were produced by the installation subprocess boundary.
    receipt = home / ".installation-complete.json"
    atomic_json(receipt, {"source": "remote@synthetic"})
    native_settings = (home / "settings.json").read_bytes()
    payload_manifest = (payload / "plugin.json").read_bytes()
    if not available_catalog:
        (home / "cache").rename(home / "prior-native-cache")
    log.write_text("", encoding="utf-8")
    _expect_installer_settings(monkeypatch, target)
    assert prepare(_remote_request(target), copilot_command=command) == first
    calls = _recorded_native_calls(log)
    assert [call["argv"] for call in calls] == (
        [] if available_catalog else [["plugin", "marketplace", "update", "synthetic"]]
    )
    assert (home / "settings.json").read_bytes() == native_settings
    assert (payload / "plugin.json").read_bytes() == payload_manifest
    upgraded = json.loads(receipt.read_text())
    assert upgraded["catalog"]["owner"]["name"] == "Synthetic Marketplace Publisher"
    assert upgraded["entry"]["name"] == "remote"
    for call in calls:
        assert not Path(call["cwd"]).is_relative_to(target)
        assert not Path(call["cwd"]).exists()
    if not available_catalog:
        assert (home / "prior-native-cache").is_dir()


def test_partial_install_retry_retains_registration_and_retries_only_install(tmp_path, monkeypatch):
    target = tmp_path / "target"
    init_checkout(target)
    log = tmp_path / "native-calls.jsonl"
    monkeypatch.setenv("VENUE_CALL_LOG", str(log))
    monkeypatch.setenv("VENUE_FAIL_INSTALL", "1")
    monkeypatch.setenv("VENUE_PARTIAL_PAYLOAD", "1")
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    with pytest.raises(RuntimeError, match="synthetic package extraction failed"):
        prepare(_remote_request(target), copilot_command=command)
    first_calls = _recorded_native_calls(log)
    assert [call["argv"][:3] for call in first_calls] == [
        ["plugin", "marketplace", "add"], ["plugin", "install", "remote@synthetic"],
    ]
    home = next((target / ".git" / "copilot-venue-plugins" / "marketplaces" / "synthetic"
                 / "installation").iterdir())
    assert json.loads((home / "settings.json").read_text())["extraKnownMarketplaces"]["synthetic"] == (
        _remote_request(target)["plugins"][0]["marketplace"]
    )
    assert not (home / ".installation-complete.json").exists()
    assert (home / "cache" / "marketplaces").is_dir()
    assert (home / "installed-plugins" / "synthetic" / "remote" / "partial-marker").read_text() == "retained"
    monkeypatch.delenv("VENUE_FAIL_INSTALL")
    log.write_text("", encoding="utf-8")
    assert "remote@synthetic" in prepare(_remote_request(target), copilot_command=command)
    calls = _recorded_native_calls(log)
    assert [call["argv"] for call in calls] == [["plugin", "install", "remote@synthetic"]]
    assert all(not Path(call["cwd"]).is_relative_to(target) for call in first_calls + calls)
    assert all(not Path(call["cwd"]).exists() for call in first_calls + calls)
    assert (home / "installed-plugins" / "synthetic" / "remote" / "partial-marker").read_text() == "retained"


def test_native_fixture_refuses_duplicate_add_without_repointing_registration(tmp_path, monkeypatch):
    target = tmp_path / "target"
    init_checkout(target)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    result = prepare(_remote_request(target), copilot_command=command)
    home = Path(result["remote@synthetic"]).parents[2]
    before = (home / "settings.json").read_bytes()
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    _expect_installer_settings(monkeypatch, target)
    env = {**os.environ, "COPILOT_HOME": str(home), "COPILOT_CACHE_HOME": str(home / "cache")}
    duplicate = subprocess.run(
        [*command, "plugin", "marketplace", "add", "example/catalog"], cwd=neutral,
        env=env, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert duplicate.returncode == 1
    assert "already registered" in duplicate.stderr
    assert (home / "settings.json").read_bytes() == before


@pytest.mark.parametrize("mismatch", ["receipt", "origin", "origin-null", "payload"])
def test_installation_identity_mismatch_refuses_before_native_operations(tmp_path, monkeypatch, mismatch):
    target = tmp_path / "target"
    init_checkout(target)
    log = tmp_path / "native-calls.jsonl"
    monkeypatch.setenv("VENUE_CALL_LOG", str(log))
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    result = prepare(_remote_request(target), copilot_command=command)
    payload = Path(result["remote@synthetic"])
    home = payload.parents[2]
    if mismatch == "receipt":
        atomic_json(home / ".installation-complete.json", {"source": "different@synthetic"})
    elif mismatch in {"origin", "origin-null"}:
        settings = json.loads((home / "settings.json").read_text())
        settings["extraKnownMarketplaces"]["synthetic"] = {
            "source": {"source": "github", "repo": "example/another-origin"},
        } if mismatch == "origin" else None
        atomic_json(home / "settings.json", settings)
    else:
        atomic_json(payload / "plugin.json", {"name": "different", "version": "1.0.0"})
    log.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        prepare(_remote_request(target), copilot_command=command)
    assert _recorded_native_calls(log) == []


def test_legacy_catalog_refresh_failure_is_explicit_and_keeps_existing_state(tmp_path, monkeypatch):
    target = tmp_path / "target"
    init_checkout(target)
    log = tmp_path / "native-calls.jsonl"
    monkeypatch.setenv("VENUE_CALL_LOG", str(log))
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    result = prepare(_remote_request(target), copilot_command=command)
    payload = Path(result["remote@synthetic"])
    home = payload.parents[2]
    receipt = home / ".installation-complete.json"
    atomic_json(receipt, {"source": "remote@synthetic"})
    (home / "cache").rename(home / "prior-native-cache")
    settings = target / ".github" / "copilot" / "settings.local.json"
    before = settings.read_bytes(), receipt.read_bytes(), (home / "settings.json").read_bytes()
    log.write_text("", encoding="utf-8")
    _expect_installer_settings(monkeypatch, target)
    monkeypatch.setenv("VENUE_FAIL_REFRESH", "1")
    with pytest.raises(RuntimeError, match="synthetic package extraction failed"):
        prepare(_remote_request(target), copilot_command=command)
    assert (settings.read_bytes(), receipt.read_bytes(), (home / "settings.json").read_bytes()) == before
    assert (home / "prior-native-cache").is_dir()
    assert [call["argv"] for call in _recorded_native_calls(log)] == [
        ["plugin", "marketplace", "update", "synthetic"],
    ]
    monkeypatch.delenv("VENUE_FAIL_REFRESH")
    assert prepare(_remote_request(target), copilot_command=command) == result


def test_failed_real_install_never_publishes_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VENUE_FAIL_INSTALL", "1")
    target = tmp_path / "target"
    init_checkout(target)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    request = {"repo": str(target), "plugins": [{
        "source": "remote@synthetic", "enable": True,
        "marketplace": {"source": {"source": "github", "repo": "example/marketplace"}},
    }]}
    with pytest.raises(RuntimeError, match="installation failed") as error:
        prepare(request, copilot_command=command)
    assert "exit 1" in str(error.value)
    assert "synthetic repository unavailable" in str(error.value)
    assert "SYNTHETIC_SECRET_VALUE" not in str(error.value)
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


def test_same_named_plugins_remain_marketplace_qualified(tmp_path):
    first = local_plan(tmp_path / "first", marketplace="first")
    second = local_plan(tmp_path / "second", marketplace="second")
    target = tmp_path / "target"
    init_checkout(target)
    result = prepare({"repo": str(target), "plugins": first["plugins"] + second["plugins"]})
    assert set(result) == {"sample@first", "sample@second"}
    assert len(set(result.values())) == 2


def test_native_plugins_in_the_same_marketplace_remain_resolvable(tmp_path):
    target = tmp_path / "target"
    local_plan(target, name="native")
    init_checkout(target)
    plan = local_plan(tmp_path / "host")
    result = prepare({"repo": str(target), **plan})
    assert set(result) == {"sample@local", "native@local"}


def test_documentation_links_do_not_expand_payload_closure(tmp_path):
    host = tmp_path / "host"
    local_plan(host)
    (host / "CONTRIBUTING.md").write_text("not a runtime asset")
    (host / ".ai" / "sample" / "README.md").write_text("[contribute](../../CONTRIBUTING.md)")
    target = tmp_path / "target"
    init_checkout(target)
    result = prepare({"repo": str(target), **build_plan([{"source": "sample@local"}], [host])})
    snapshot = Path(result["sample@local"]).parent.parent
    assert not (snapshot / "CONTRIBUTING.md").exists()


def test_malformed_local_settings_are_not_overwritten(tmp_path):
    plan = local_plan(tmp_path / "host")
    directory = tmp_path / "target" / ".github" / "copilot"
    directory.mkdir(parents=True)
    init_checkout(tmp_path / "target")
    path = directory / "settings.local.json"
    path.write_text("{broken")
    with pytest.raises(ValueError):
        prepare({"repo": str(tmp_path / "target"), **plan})
    assert path.read_text() == "{broken"


def test_interrupted_publication_leaves_previous_settings_valid(tmp_path, monkeypatch):
    plan = local_plan(tmp_path / "host")
    target = tmp_path / "target"
    init_checkout(target)
    prepare({"repo": str(target), **plan})
    path = target / ".github" / "copilot" / "settings.local.json"
    previous = path.read_bytes()
    import plugin_resolve.registration as registration
    replace = registration.os.replace

    def interrupted(source, destination):
        if Path(destination) == path:
            raise OSError("interrupted settings publication")
        return replace(source, destination)

    monkeypatch.setattr(registration.os, "replace", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        prepare({"repo": str(target), **plan})
    assert path.read_bytes() == previous
    assert json.loads(previous)["enabledPlugins"] == {"sample@local": True}


def test_declared_sibling_dependency_is_prepared_and_resolved(tmp_path):
    host = tmp_path / "host"
    local_plan(host)
    sibling = host / ".ai" / "support"
    sibling.mkdir()
    atomic_json(sibling / "plugin.json", {"name": "support", "version": "1.2.3"})
    atomic_json(host / ".ai" / "sample" / "plugin.json", {
        "name": "sample", "version": "1.2.3",
        "dependencies": [{"name": "support", "marketplace": "local"}],
    })
    atomic_json(host / ".ai" / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic Publisher"}, "plugins": [
            {"name": "sample", "source": "sample"}, {"name": "support", "source": "support"},
        ],
    })
    plan = build_plan([{"source": "sample@local"}, {"source": "support@local"}], [host])
    target = tmp_path / "target"
    init_checkout(target)
    assert set(prepare({"repo": str(target), **plan})) == {"sample@local", "support@local"}


def test_object_dependencies_and_native_publication_schema(tmp_path):
    host = tmp_path / "host"
    local_plan(host)
    sibling = host / ".ai" / "support"
    sibling.mkdir()
    atomic_json(sibling / "plugin.json", {"name": "support", "version": "1.2.3"})
    atomic_json(host / ".ai" / "sample" / "plugin.json", {
        "name": "sample", "version": "1.2.3", "description": "Synthetic skill provider",
        "dependencies": [
            {"name": "support", "marketplace": "local"},
            {"name": "external", "marketplace": "remote"},
        ],
    })
    claude = host / ".ai" / "sample" / ".claude-plugin"
    claude.mkdir()
    (host / ".ai" / "sample" / "plugin.json").rename(claude / "plugin.json")
    atomic_json(host / ".ai" / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic Publisher"},
        "description": "Preserved marketplace metadata",
        "plugins": [{"name": name, "source": name, "version": "1.2.3",
                     "keywords": ["synthetic"]} for name in ("sample", "support")],
    })
    path = host / ".github" / "copilot" / "settings.json"
    settings = json.loads(path.read_text())
    settings["extraKnownMarketplaces"]["remote"] = {
        "source": {"source": "github", "repo": "example/marketplace"},
    }
    atomic_json(path, settings)
    plan = build_plan([
        {"source": "sample@local"}, {"source": "support@local"}, {"source": "external@remote"},
    ], [host])
    target = tmp_path / "target"
    init_checkout(target)
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    result = prepare({"repo": str(target), **plan}, copilot_command=command)
    assert set(result) == {"sample@local", "support@local", "external@remote"}
    local = json.loads((target / ".github" / "copilot" / "settings.local.json").read_text())
    for name, definition in local["extraKnownMarketplaces"].items():
        catalog = json.loads((Path(definition["source"]["path"]) / "marketplace.json").read_text())
        # These fields are required by the native consumer even though the
        # shared path resolver deliberately accepts a smaller reading surface.
        assert catalog["name"] == name
        assert catalog["owner"]["name"]
        assert all(entry["version"] and entry["source"].startswith("./")
                   for entry in catalog["plugins"])
        if name == "local":
            assert catalog["description"] == "Preserved marketplace metadata"
            assert all(entry["keywords"] == ["synthetic"] for entry in catalog["plugins"])
    assert git_status(target) == ""


def test_target_native_remote_is_installed_alongside_host_local(tmp_path):
    plan = local_plan(tmp_path / "host")
    target = tmp_path / "target"
    init_checkout(target)
    settings = target / ".github" / "copilot"
    settings.mkdir(parents=True)
    atomic_json(settings / "settings.json", {
        "enabledPlugins": {"native@remote": True},
        "extraKnownMarketplaces": {
            "remote": {"source": {"source": "github", "repo": "example/marketplace"}},
        },
    })
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    result = prepare({"repo": str(target), **plan}, copilot_command=command)
    assert set(result) == {"sample@local", "native@remote"}
    assert "installation" in Path(result["native@remote"]).parts
    assert (Path(result["native@remote"]) / "plugin.json").is_file()


def test_disabled_companion_preserves_the_override_and_reports_external_ownership(tmp_path):
    host = tmp_path / "host"
    local_plan(host)
    sibling = host / ".ai" / "support"
    sibling.mkdir()
    atomic_json(sibling / "plugin.json", {"name": "support", "version": "1.2.3"})
    atomic_json(host / ".ai" / "sample" / "plugin.json", {
        "name": "sample", "version": "1.2.3",
        "dependencies": [{"name": "support", "marketplace": "local"}],
    })
    atomic_json(host / ".ai" / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic Publisher"},
        "plugins": [{"name": name, "source": name, "version": "1.2.3"}
                    for name in ("sample", "support")],
    })
    plan = build_plan([{"source": "sample@local"}, {"source": "support@local", "enable": False}], [host])
    target = tmp_path / "target"
    init_checkout(target)
    diagnostics = []
    result = prepare({"repo": str(target), **plan}, diagnostics=diagnostics)
    assert set(result) == {"sample@local"}
    assert diagnostics == [{
        "source": "sample@local", "dependency": "support@local", "status": "disabled",
    }]
    settings = json.loads((target / ".github" / "copilot" / "settings.local.json").read_text())
    assert settings["enabledPlugins"]["support@local"] is False


def test_host_disable_and_explicit_target_enable_have_distinct_authority(tmp_path):
    plan = local_plan(tmp_path / "host")
    plan["plugins"][0]["enable"] = False
    target = tmp_path / "target"
    init_checkout(target)
    assert prepare({"repo": str(target), **plan}) == {}
    path = target / ".github" / "copilot" / "settings.local.json"
    value = json.loads(path.read_text())
    assert value["enabledPlugins"]["sample@local"] is False
    value["enabledPlugins"]["sample@local"] = True
    atomic_json(path, value)
    assert "sample@local" in prepare({"repo": str(target), **plan})


def test_target_native_external_marketplace_retains_its_actual_copy_base(tmp_path):
    native = tmp_path / "native"
    local_plan(native, name="native")
    target = tmp_path / "target"
    init_checkout(target)
    directory = target / ".github" / "copilot"
    directory.mkdir(parents=True)
    atomic_json(directory / "settings.json", {
        "enabledPlugins": {"native@local": True},
        "extraKnownMarketplaces": {
            "local": {"source": {"source": "directory", "path": str(native / ".ai")}},
        },
    })
    result = prepare({"repo": str(target), "plugins": []})
    assert set(result) == {"native@local"}
    assert Path(result["native@local"]).is_relative_to(target / ".git")


def test_git_file_checkout_keeps_all_generated_state_in_its_git_metadata(tmp_path):
    target = tmp_path / "target"
    init_checkout(target)
    metadata = tmp_path / "metadata"
    (target / ".git").rename(metadata)
    (target / ".git").write_text(f"gitdir: {metadata.as_posix()}\n")
    result = prepare({"repo": str(target), **local_plan(tmp_path / "host")})
    assert all(Path(path).is_relative_to(metadata) for path in result.values())
    assert not (target / ".github" / "copilot" / ".venue-plugin-ownership.json").exists()
    assert git_status(target) == ""


def test_inherited_git_selectors_cannot_redirect_the_supplied_checkout(tmp_path, monkeypatch):
    target, other = tmp_path / "target", tmp_path / "other"
    init_checkout(target)
    init_checkout(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    result = prepare({"repo": str(target), **local_plan(tmp_path / "host")})
    assert all(Path(path).is_relative_to(target / ".git") for path in result.values())
    assert not (other / ".github").exists()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("filename, permitted", [
    (".env", False), (".env.production", False),
    (".ENV", False), (".ENV.production", False), (".eNv.local", False),
    (".EnV.DeVeLoPmEnT", False), (".env.EXAMPLE.backup", False),
    (".ENV.SAMPLE.old", False), (".env.TEMPLATE.secret", False),
    (".env.example", True), (".ENV.EXAMPLE", True), (".env.EXAMPLE", True),
    (".env.SaMpLe", True), (".env.TeMpLaTe", True),
    (".eNv.production.SaMpLe", True), (".ENV.local.TeMpLaTe", True),
    (".Env.SAMPLE", True), (".eNv.TeMpLaTe", True),
    (".environment", True), (".ENVIRONMENT", True), ("settings.env", True),
])
def test_reflection_environment_basename_policy_matches_native_staging(
    tmp_path, filename, permitted, nested,
):
    host = tmp_path / "control"
    local_plan(host)
    payload = host / ".ai" / "sample"
    directory = payload / "references" if nested else payload
    directory.mkdir(exist_ok=True)
    marker = b"SYNTHETIC_ENV_CASE_MARKER\n"
    (directory / filename).write_bytes(marker)
    guide = host / ".ai" / "support" / "guide.md"
    guide.parent.mkdir()
    guide.write_text("Synthetic required sibling guide", encoding="utf-8")
    skill = payload / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("[guide](../../../support/guide.md)", encoding="utf-8")
    plan = build_plan([{"source": "sample@local"}], [host])
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(plan["plugins"][0]["archive"]))) as archive:
        contents = {item.name: archive.extractfile(item).read() for item in archive.getmembers()}
    member = (directory / filename).relative_to(host).as_posix()
    assert (member in contents) is permitted
    if permitted:
        assert contents[member] == marker
    else:
        assert not any(marker in content for content in contents.values())
    assert contents[".ai/support/guide.md"] == b"Synthetic required sibling guide"
    assert ".ai/sample/skills/example/SKILL.md" in contents
    assert ".ai/sample/plugin.json" in contents


def test_script_root_expressions_do_not_reflect_ancestor_directories(tmp_path):
    host = tmp_path / "control"
    local_plan(host)
    payload = host / ".ai" / "sample"
    scripts = payload / "scripts"
    scripts.mkdir()
    (scripts / "relative-root.py").write_text("ROOT='../../..'\nPARENT='../../../..'\nPLUGIN='..'\n")
    (host / "unrelated-control-state.json").write_text('{"must_not_copy":true}')
    (host / ".ai" / "unrelated-marketplace-state.json").write_text("{}")
    guide = host / ".ai" / "support" / "guide.md"
    guide.parent.mkdir()
    guide.write_text("required sibling guide")
    skill = payload / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("[guide](../../../support/guide.md)")
    plan = build_plan([{"source": "sample@local"}], [host])
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(plan["plugins"][0]["archive"]))) as archive:
        members = set(archive.getnames())
    assert ".ai/support/guide.md" in members
    assert ".ai/sample/scripts/relative-root.py" in members
    assert len(members) == 4
    assert ".github/copilot/settings.json" not in members
    assert "unrelated-control-state.json" not in members
    assert ".ai/unrelated-marketplace-state.json" not in members


def test_unselected_companions_are_diagnostics_not_propagation_authority(tmp_path, caplog):
    host = tmp_path / "control"
    local_plan(host)
    companion = host / ".ai" / "host-companion"
    companion.mkdir()
    atomic_json(companion / "plugin.json", {"name": "host-companion", "version": "1.2.3"})
    (companion / "host-state-marker").write_text("must not project")
    atomic_json(host / ".ai" / "sample" / "plugin.json", {
        "name": "sample", "version": "1.2.3",
        "dependencies": [
            {"name": "host-companion", "marketplace": "local"},
            {"name": "control-harness", "marketplace": "external"},
        ],
    })
    catalog = host / ".ai" / "marketplace.json"
    value = json.loads(catalog.read_text())
    value["plugins"].append({"name": "host-companion", "source": "host-companion", "version": "1.2.3"})
    atomic_json(catalog, value)
    plan = build_plan([{"source": "sample@local"}], [host])
    assert [item["source"] for item in plan["plugins"]] == ["sample@local"]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(plan["plugins"][0]["archive"]))) as archive:
        assert not any("host-companion" in name for name in archive.getnames())
    target = tmp_path / "target"
    init_checkout(target)
    assert set(prepare({"repo": str(target), **plan})) == {"sample@local"}
    settings = json.loads((target / ".github" / "copilot" / "settings.local.json").read_text())
    assert settings["enabledPlugins"] == {"sample@local": True}
    root = Path(settings["extraKnownMarketplaces"]["local"]["source"]["path"])
    assert [entry["name"] for entry in json.loads((root / "marketplace.json").read_text())["plugins"]] == ["sample"]
    assert "host-companion@local" in caplog.text
    assert "control-harness@external" in caplog.text
    assert "caller must supply its execution owner" in caplog.text


@pytest.mark.parametrize("host_selects_same_identity", [False, True])
def test_native_remote_origin_in_local_settings_is_preserved_and_localized(
    tmp_path, monkeypatch, host_selects_same_identity,
):
    target = tmp_path / "target"
    init_checkout(target)
    directory = target / ".github" / "copilot"
    directory.mkdir(parents=True)
    path = directory / "settings.local.json"
    origin = {"source": {"source": "github", "repo": "example/native-marketplace"}}
    atomic_json(path, {
        "enabledPlugins": {"native@remote": True},
        "extraKnownMarketplaces": {"remote": origin}, "unrelated": {"retained": True},
    })
    monkeypatch.setenv("VENUE_EXPECTED_SETTINGS_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    plan = local_plan(tmp_path / "host", name="native", marketplace="remote") if host_selects_same_identity else {"plugins": []}
    command = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    diagnostics = []
    result = prepare({"repo": str(target), **plan}, copilot_command=command, diagnostics=diagnostics)
    assert set(result) == {"native@remote"}
    assert "installation" in Path(result["native@remote"]).parts
    assert json.loads((Path(result["native@remote"]) / "plugin.json").read_text())["version"] == "2.3.4"
    value = json.loads(path.read_text())
    assert value["unrelated"] == {"retained": True}
    assert value["extraKnownMarketplaces"]["remote"]["source"]["source"] == "directory"
    receipt = json.loads((target / ".git" / "copilot-venue-plugins" / "ownership.json").read_text())
    assert receipt["marketplaceOrigins"]["remote"] == origin
    assert any(item["status"] == "native-origin-preserved" for item in diagnostics)
    assert prepare({"repo": str(target), **plan}, copilot_command=command) == result


def test_explicit_target_local_payload_path_is_not_replaced(tmp_path):
    plan = local_plan(tmp_path / "host")
    other = tmp_path / "operator"
    local_plan(other)
    target = tmp_path / "target"
    init_checkout(target)
    directory = target / ".github" / "copilot"
    directory.mkdir(parents=True)
    origin = {"source": {"source": "directory", "path": str(other / ".ai")}}
    atomic_json(directory / "settings.local.json", {
        "enabledPlugins": {"sample@local": True}, "extraKnownMarketplaces": {"local": origin},
    })
    result = prepare({"repo": str(target), **plan})
    assert Path(result["sample@local"]) == (other / ".ai" / "sample").resolve()
    value = json.loads((directory / "settings.local.json").read_text())
    assert value["extraKnownMarketplaces"]["local"] == origin


def test_explicit_target_only_nondefault_plugin_is_deferred_then_prepared(tmp_path):
    plan = build_plan([{"source": "nondefault@target"}], [])
    assert plan["plugins"] == [{
        "source": "nondefault@target", "enable": True, "resolve_on_target": True,
    }]
    assert plan["marketplaces"] == {}
    target = tmp_path / "target"
    local_plan(target, name="default", marketplace="target")
    extra = target / ".ai" / "nondefault"
    extra.mkdir()
    atomic_json(extra / "plugin.json", {"name": "nondefault", "version": "1.2.3"})
    catalog = target / ".ai" / "marketplace.json"
    value = json.loads(catalog.read_text())
    value["plugins"].append({"name": "nondefault", "source": "nondefault", "version": "1.2.3"})
    atomic_json(catalog, value)
    init_checkout(target)
    result = prepare({"repo": str(target), **plan})
    assert set(result) == {"default@target", "nondefault@target"}
    assert (Path(result["nondefault@target"]) / "plugin.json").is_file()
    settings = json.loads((target / ".github" / "copilot" / "settings.local.json").read_text())
    assert settings["enabledPlugins"]["nondefault@target"] is True


@pytest.mark.parametrize("missing", ["marketplace", "payload"])
def test_deferred_target_selection_missing_authority_or_payload_blocks_publication(tmp_path, missing):
    plan = build_plan([{"source": "nondefault@target"}], [])
    target = tmp_path / "target"
    if missing == "payload":
        local_plan(target, name="default", marketplace="target")
    init_checkout(target)
    with pytest.raises(ValueError, match="Required"):
        prepare({"repo": str(target), **plan})
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


def test_two_plugins_localize_one_operator_origin_without_deactivating_it(tmp_path, monkeypatch):
    target = tmp_path / "target"
    init_checkout(target)
    directory = target / ".github" / "copilot"
    directory.mkdir(parents=True)
    path = directory / "settings.local.json"
    origin = {"source": {"source": "github", "repo": "example/operator-marketplace"}}
    atomic_json(path, {
        "enabledPlugins": {"a@remote": True, "b@remote": True},
        "extraKnownMarketplaces": {"remote": origin},
    })
    monkeypatch.setenv("VENUE_EXPECTED_SETTINGS_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    installer = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    diagnostics = []
    first = prepare({"repo": str(target), "plugins": []}, copilot_command=installer, diagnostics=diagnostics)
    receipt_path = target / ".git" / "copilot-venue-plugins" / "ownership.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["marketplaceOrigins"]["remote"] == origin
    assert receipt["marketplaceOriginActive"]["remote"] is True
    assert sum(item["status"] == "native-origin-preserved" for item in diagnostics) == 1
    host = local_plan(tmp_path / "host", name="a", marketplace="remote")
    again = prepare({"repo": str(target), **host}, copilot_command=installer)
    assert again == first
    assert all("installation" in Path(payload).parts for payload in again.values())
    assert all(json.loads((Path(payload) / "plugin.json").read_text())["version"] == "2.3.4"
               for payload in again.values())


@pytest.mark.parametrize("origin_file", ["settings.json", "settings.local.json"])
def test_new_native_selection_uses_active_origin_and_respects_changed_local_override(
    tmp_path, monkeypatch, origin_file,
):
    target = tmp_path / "target"
    init_checkout(target)
    directory = target / ".github" / "copilot"
    directory.mkdir(parents=True)
    path = directory / origin_file
    origin = {"source": {"source": "github", "repo": "example/operator-marketplace"}}
    atomic_json(path, {
        "enabledPlugins": {"a@remote": True}, "extraKnownMarketplaces": {"remote": origin},
    })
    installer = (sys.executable, str(Path(__file__).parent / "fixtures" / "venue_installer.py"))
    if origin_file == "settings.local.json":
        monkeypatch.setenv("VENUE_EXPECTED_SETTINGS_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    else:
        monkeypatch.delenv("VENUE_EXPECTED_SETTINGS_SHA256", raising=False)
    prepare({"repo": str(target), "plugins": []}, copilot_command=installer)
    path = directory / "settings.local.json"
    value = json.loads(path.read_text())
    value["enabledPlugins"]["b@remote"] = True
    atomic_json(path, value)
    monkeypatch.setenv("VENUE_EXPECTED_SETTINGS_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    result = prepare({"repo": str(target), "plugins": []}, copilot_command=installer)
    assert set(result) == {"a@remote", "b@remote"}
    assert all("installation" in Path(payload).parts for payload in result.values())
    assert all(json.loads((Path(payload) / "plugin.json").read_text())["version"] == "2.3.4"
               for payload in result.values())

    operator = tmp_path / "operator"
    local_plan(operator, name="a", marketplace="remote")
    (operator / ".ai" / "b").mkdir()
    for name in ("a", "b"):
        atomic_json(operator / ".ai" / name / "plugin.json", {"name": name, "version": "9.8.7"})
    atomic_json(operator / ".ai" / "marketplace.json", {
        "name": "remote", "owner": {"name": "Synthetic Operator"},
        "plugins": [{"name": name, "source": name, "version": "9.8.7"} for name in ("a", "b")],
    })
    value = json.loads(path.read_text())
    override = {"source": {"source": "directory", "path": str(operator / ".ai")}}
    value["extraKnownMarketplaces"]["remote"] = override
    atomic_json(path, value)
    result = prepare({"repo": str(target), "plugins": []}, copilot_command=installer)
    assert all(Path(payload).is_relative_to(operator / ".ai") for payload in result.values())
    assert json.loads(path.read_text())["extraKnownMarketplaces"]["remote"] == override
    receipt = json.loads((target / ".git" / "copilot-venue-plugins" / "ownership.json").read_text())
    if origin_file == "settings.local.json":
        assert receipt["marketplaceOrigins"]["remote"] == origin
        assert receipt["marketplaceOriginActive"]["remote"] is False
    else:
        assert json.loads((directory / origin_file).read_text())["extraKnownMarketplaces"]["remote"] == origin
