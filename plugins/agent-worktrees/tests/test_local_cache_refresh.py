"""Real declaration-driven copy and launch boundary regressions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import context_cli, knowledge_plugins, local_cache_refresh as lcr
from agent_worktrees import resolve_cli, static_instructions as static


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def copy_workspace(tmp_path, monkeypatch):
    root, home = tmp_path / "repo", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return root, home


def provider(root, home, name="demo", *, content=b'---\r\napplyTo: "**"\r\n---\r\nOpaque \xff\n'):
    plugin = home / ".copilot" / "installed-plugins" / "market" / name
    write_json(plugin / "plugin.json", {"name": name})
    write_json(plugin / "instruction-projections.json", {
        "schema": "copilot-extensions.instruction-projections", "version": 1,
        "projections": [{
            "id": "fallback", "template": "instructions/fallback.md",
            "customizationKind": "instructions",
            "destination": f".github/instructions/{name}/fallback.instructions.md",
        }],
    })
    template = plugin / "instructions" / "fallback.md"
    template.parent.mkdir()
    template.write_bytes(content)
    settings = root / ".github" / "copilot" / "settings.json"
    current = json.loads(settings.read_text()) if settings.exists() else {}
    current.setdefault("enabledPlugins", {})[f"{name}@market"] = True
    write_json(settings, current)
    return template, root / static.NAMESPACE / "market" / name / "fallback.local.instructions.md"


def test_copy_opaque_bytes_without_packaged_engine(copy_workspace, monkeypatch, capsys):
    root, home = copy_workspace
    from agent_worktrees import installer

    monkeypatch.setattr(
        installer, "_payload_root",
        lambda: pytest.fail("static delivery must not load a packaged engine"),
    )
    template, output = provider(root, home, content=b'---\r\napplyTo: "**"\r\n---\r\n' + b"X" * 100_000)
    nested = root / "nested"
    nested.mkdir()
    result = lcr.prepare_for_launch(nested)
    assert result.status == "ready", result.diagnostic
    assert result.changed == 1
    assert output.read_bytes() == template.read_bytes()
    assert not (nested / ".github").exists()
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", str(output)], capture_output=True,
    )
    assert ignored.returncode == 0
    captured = capsys.readouterr()
    assert not captured.out
    for stage in ("Preparing instruction projections", "Copying declared instructions"):
        assert f"{stage}: starting" in captured.err
        assert f"{stage}: finished" in captured.err


def test_overwrite_untracked_and_reconcile_disabled_sources(copy_workspace):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).changed == 1
    output.write_bytes(b"locally edited; no ownership marker")
    assert lcr.refresh_local_cache(root).changed == 1
    assert output.read_bytes() == template.read_bytes()
    assert lcr.refresh_local_cache(root).unchanged == 1
    outside = root / ".github" / "instructions" / "keep.local.instructions.md"
    outside.write_bytes(b"unrelated")
    write_json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"demo@market": False}})
    assert lcr.refresh_local_cache(root).removed == 1
    assert not output.exists()
    assert outside.read_bytes() == b"unrelated"


@pytest.mark.parametrize("stale", [False, True])
def test_index_protects_staged_outputs_before_any_copy(copy_workspace, stale):
    root, home = copy_workspace
    _, output = provider(root, home)
    _, second = provider(root, home, "second")
    assert lcr.refresh_local_cache(root).status == "ready"
    output.write_bytes(b"tracked edit")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(output)], check=True, capture_output=True)
    second.unlink()
    if stale:
        write_json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"second@market": True}})
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "tracked instruction output" in result.detail
    assert output.read_bytes() == b"tracked edit"
    assert not second.exists()


@pytest.mark.parametrize("fault", ["settings", "declaration", "missing-plugin", "missing-template", "collision"])
def test_incomplete_discovery_never_removes_existing_output(copy_workspace, fault):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    declaration = template.parents[1] / "instruction-projections.json"
    if fault == "settings":
        (root / ".github" / "copilot" / "settings.json").write_text("{")
    elif fault == "declaration":
        declaration.write_text("{")
    elif fault == "missing-plugin":
        write_json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"absent@market": True}})
    elif fault == "missing-template":
        template.unlink()
    else:
        data = json.loads(declaration.read_text())
        data["projections"] *= 2
        write_json(declaration, data)
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.exists()


def test_linked_output_is_refused(copy_workspace):
    root, home = copy_workspace
    _, output = provider(root, home)
    output.parent.mkdir(parents=True)
    outside = home / "outside"
    outside.write_bytes(b"protected")
    try:
        output.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert outside.read_bytes() == b"protected"


def test_publication_resolves_sources_after_acquiring_lock(copy_workspace, monkeypatch):
    root, home = copy_workspace
    template, output = provider(root, home)
    entered, release = threading.Event(), threading.Event()
    original = static._snapshot
    calls = []

    def snapshot(*args):
        value = original(*args)
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return value

    monkeypatch.setattr(static, "_snapshot", snapshot)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(lcr.refresh_local_cache, root)
        assert entered.wait(5)
        second = executor.submit(lcr.refresh_local_cache, root)
        template.write_bytes(b"new snapshot")
        release.set()
        assert first.result().status == "failed"
        assert second.result().status == "ready"
    assert output.read_bytes() == b"new snapshot"


def test_backup_budget_and_dry_run_do_not_publish(copy_workspace, monkeypatch):
    root, home = copy_workspace
    _, output = provider(root, home)
    assert lcr.refresh_local_cache(root, timeout=0).status == "timeout"
    assert lcr.prepare_for_launch(root, dry_run=True).status == "skipped"
    assert not output.exists()
    monkeypatch.setattr(lcr, "_queue_refresh", lambda _: lcr.RefreshResult("queued"))
    assert "queued" in lcr.sessionstart_diagnostic(str(root), deadline=time.time() + 2)
    assert "skipped" in lcr.sessionstart_diagnostic(str(root), deadline=time.time() - 1)


def test_composition_failure_reports_degradation_without_partial_copy(copy_workspace, monkeypatch, capsys):
    root, home = copy_workspace
    _, output = provider(root, home)

    def fail(**kwargs):
        raise knowledge_plugins.KnowledgePluginError("incomplete paired settings")

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", fail)
    result = lcr.prepare_for_launch(root, compose_pair=True)
    assert result.status == "failed"
    assert not output.exists()
    assert "incomplete paired settings" in capsys.readouterr().err


def test_settings_layers_and_unchanged_declared_destination(copy_workspace):
    root, home = copy_workspace
    template, _ = provider(root, home)
    write_json(home / ".copilot" / "settings.json", {"enabledPlugins": {"demo@market": True}})
    write_json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"demo@market": False}})
    assert lcr.refresh_local_cache(root).changed == 0
    write_json(root / ".github" / "copilot" / "settings.local.json", {"enabledPlugins": {"demo@market": True}})
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data["projections"][0].update(
        destination=".github/instructions/other/nested/renamed.instructions.md",
        skipLocalCache=True,
    )
    write_json(declaration, data)
    result = lcr.refresh_local_cache(root)
    assert result.status == "ready"
    output = root / static.NAMESPACE / "market" / "other" / "nested" / "renamed.local.instructions.md"
    assert output.read_bytes() == template.read_bytes()


def test_nested_file_directory_destination_collision_fails_before_copy(copy_workspace):
    root, home = copy_workspace
    template, _ = provider(root, home)
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data["projections"].append({
        **data["projections"][0], "id": "second",
        "destination": ".github/instructions/demo/fallback.local.instructions.md/child.instructions.md",
    })
    write_json(declaration, data)
    assert lcr.refresh_local_cache(root).status == "failed"
    assert not (root / static.NAMESPACE).exists()


def test_repository_ignore_negation_cannot_publish_unignored_files(copy_workspace):
    root, home = copy_workspace
    _, output = provider(root, home)
    (root / ".gitignore").write_text("!**/\n!*.local.instructions.md\n", encoding="utf-8")
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert not output.exists()


def test_real_knowledge_only_provider_copied_after_composition(copy_workspace, tmp_path, capsys):
    harness, home = copy_workspace
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    template, _ = provider(knowledge, home)
    # A real local marketplace carried only by the knowledge checkout.
    plugin = template.parents[1]
    catalog = knowledge / ".ai"
    write_json(catalog / "marketplace.json", {
        "name": "private", "plugins": [{"name": "demo", "source": "./demo"}],
    })
    import shutil

    shutil.copytree(plugin, catalog / "demo")
    write_json(knowledge / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"demo@private": True},
        "extraKnownMarketplaces": {"private": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    assert context_cli.cmd_knowledge_dispatch([
        "compose-plugins", "--harness-path", str(harness),
        "--knowledge-path", str(knowledge), "--prepare-projections", "--json",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["projections"]["changed"] == 1
    output = harness / static.NAMESPACE / "private" / "demo" / "fallback.local.instructions.md"
    assert output.read_bytes() == template.read_bytes()


@pytest.mark.parametrize("deferred", [False, True])
def test_json_base_defers_only_for_outer_launcher(tmp_path, monkeypatch, capfd, deferred):
    events = []
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
    state = SimpleNamespace(
        load_config=lambda: config, requested_machine=None, use_base=True, use_new=False,
        args=argparse.Namespace(dry_run=False, defer_projections=deferred),
    )
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda config: None)
    monkeypatch.setattr(resolve_cli, "_preflight_launch", lambda *a: SimpleNamespace(error=None))
    monkeypatch.setattr(lcr, "prepare_for_launch", lambda root, **kw: events.append("copy"))
    monkeypatch.setattr(resolve_cli, "_build_launch_cmd", lambda *a, **kw: events.append("command") or ["copilot"])
    monkeypatch.setattr(resolve_cli, "_repo_session_env", lambda *a: {})
    monkeypatch.setattr(resolve_cli, "_build_env", lambda *a, **kw: {})
    assert resolve_cli._resolve_json_mode(state) == 0
    assert events == (["command"] if deferred else ["copy", "command"])
    assert json.loads(capfd.readouterr().out)["action"] == "exec"


@pytest.mark.parametrize("deferred,dry_run", [(False, False), (True, False), (False, True)])
def test_interactive_base_prepares_before_emitting_direct_plan(tmp_path, monkeypatch, deferred, dry_run):
    from agent_worktrees import resolve_launch_cli as launch

    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
    args = argparse.Namespace(dry_run=dry_run, defer_projections=deferred)
    events = []
    monkeypatch.setattr(launch, "_dispatch_validate_profile_assignment_config", lambda config: None)
    monkeypatch.setattr(launch, "_preflight_launch", lambda *a: SimpleNamespace(error=None))
    monkeypatch.setattr(launch, "_build_launch_cmd", lambda *a, **kw: ["copilot"])
    monkeypatch.setattr(launch, "_build_env", lambda *a, **kw: {})
    monkeypatch.setattr(launch, "_repo_session_env", lambda *a: {})
    monkeypatch.setattr(launch, "_emit_plan", lambda plan: events.append("plan"))
    monkeypatch.setattr(lcr, "prepare_for_launch", lambda root: events.append("copy"))
    monkeypatch.setattr(launch.sys.stdin, "isatty", lambda: False)
    assert launch._resolve_base_repo(config, args) == 0
    assert events == (["plan"] if deferred or dry_run else ["copy", "plan"])


def test_launcher_deferral_and_final_batch_order():
    root = Path(__file__).resolve().parents[3]
    for name, defer, final, mux in (
        ("launch-session.ps1", "'resolve', '--defer-projections'", "'--prepare-projections'", "# Apply environment"),
        ("launch-session.sh", 'resolve --defer-projections "$@"', "--prepare-projections --json", "# ── tmux"),
    ):
        text = (root / "worktree-manager" / "bin" / name).read_text(encoding="utf-8-sig")
        assert text.index(defer) < text.index(final) < text.index(mux)
        assert text.count(final) == 1
