"""Real declaration-driven copy and launch boundary regressions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import context_cli, knowledge_plugins, local_cache_refresh as lcr
from agent_worktrees import resolve_cli, static_instructions as static

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


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
@pytest.mark.parametrize("index_state", ["staged-add", "committed", "staged-delete", "staged-rename"])
def test_index_protects_staged_outputs_before_any_copy(copy_workspace, stale, index_state):
    root, home = copy_workspace
    _, output = provider(root, home)
    _, second = provider(root, home, "second")
    assert lcr.refresh_local_cache(root).status == "ready"
    output.write_bytes(b"tracked edit")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(output)], check=True, capture_output=True)
    if index_state != "staged-add":
        subprocess.run([
            "git", "-C", str(root), "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "--quiet", "-m", "Fixture",
        ], check=True, capture_output=True)
    if index_state == "staged-delete":
        subprocess.run(["git", "-C", str(root), "rm", "-f", str(output)], check=True, capture_output=True)
    if index_state == "staged-rename":
        subprocess.run([
            "git", "-C", str(root), "mv", "-f", str(output), str(root / "kept.md"),
        ], check=True, capture_output=True)
    second.unlink()
    if stale:
        write_json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"second@market": True}})
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "tracked instruction output" in result.detail
    if index_state in ("staged-delete", "staged-rename"):
        assert not output.exists()
    else:
        assert output.read_bytes() == b"tracked edit"
    if index_state == "staged-rename":
        assert (root / "kept.md").read_bytes() == b"tracked edit"
    assert not second.exists()


def test_index_case_alias_is_protected_despite_inherited_pathspec_flags(copy_workspace, monkeypatch):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    blob = subprocess.check_output(
        ["git", "-C", str(root), "hash-object", "-w", str(output)], text=True,
    ).strip()
    relative = output.relative_to(root).as_posix()
    alias = relative.replace(".github/instructions/agent-worktrees-generated", ".GITHUB/INSTRUCTIONS/AGENT-WORKTREES-GENERATED")
    subprocess.run(
        ["git", "-C", str(root), "update-index", "--add", "--cacheinfo", f"100644,{blob},{alias}"],
        check=True, capture_output=True,
    )
    template.write_bytes(b"must not overwrite a staged case alias")
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.read_bytes() == previous


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


@pytest.mark.parametrize("version", [True, 1.0])
def test_invalid_declaration_version_preserves_previous_output(copy_workspace, version):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data.update(version=version, projections=[])
    write_json(declaration, data)
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "invalid instruction declarations" in result.detail
    assert output.read_bytes() == previous


@pytest.mark.parametrize("kind", ["settings", "declaration", "template", "exclude", "output"])
def test_nonregular_input_is_rejected_before_open(copy_workspace, monkeypatch, kind):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    paths = {
        "settings": root / ".github" / "copilot" / "settings.json",
        "declaration": template.parents[1] / "instruction-projections.json",
        "template": template,
        "exclude": root / ".git" / "info" / "exclude",
        "output": output,
    }
    target = paths[kind]
    original_stat, original_read = Path.stat, Path.read_bytes

    def file_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == target:
            return os.stat_result((stat.S_IFIFO | 0o600, *value[1:]))
        return value

    def read_bytes(path):
        assert path != target, "opened a nonregular input"
        return original_read(path)

    monkeypatch.setattr(Path, "stat", file_stat)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "not a regular file" in result.detail
    assert original_read(output) == original_read(template)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="native POSIX FIFO unavailable")
@pytest.mark.parametrize("kind", ["settings", "declaration", "template", "exclude"])
def test_fifo_input_fails_without_waiting_for_a_writer(copy_workspace, kind):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    target = {
        "settings": root / ".github" / "copilot" / "settings.json",
        "declaration": template.parents[1] / "instruction-projections.json",
        "template": template,
        "exclude": root / ".git" / "info" / "exclude",
    }[kind]
    previous = output.read_bytes()
    target.unlink()
    os.mkfifo(target)
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "not a regular file" in result.detail
    assert output.read_bytes() == previous


@pytest.mark.parametrize(
    ("platform", "flag", "bounded", "expected"),
    [
        ("posix", "1", False, True),
        ("posix", "1", True, False),
        ("posix", None, False, False),
        ("nt", "1", False, False),
    ],
)
def test_git_retains_only_foreground_manager_process_group(
    tmp_path, monkeypatch, platform, flag, bounded, expected,
):
    from agent_worktrees import push_timeout

    calls = []
    fake_os = SimpleNamespace(name=platform, environ=dict(os.environ))
    if flag is None:
        fake_os.environ.pop("WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP", None)
    else:
        fake_os.environ["WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP"] = flag
    monkeypatch.setattr(static, "os", fake_os)

    def run(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b"result", b"")

    monkeypatch.setattr(push_timeout, "run_bounded", run)
    deadline = time.monotonic() + 30 if bounded else None
    assert static._git(tmp_path, "status", deadline=deadline) == b"result"
    assert calls[0]["inherit_process_group"] is expected
    assert 0 < calls[0]["timeout"] <= 15


@pytest.mark.parametrize("owned", [False, True])
def test_git_timeout_cannot_continue_inside_manager_group(tmp_path, monkeypatch, owned):
    from agent_worktrees import push_timeout

    monkeypatch.setattr(static, "os", SimpleNamespace(
        name="posix",
        environ={"WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP": "1"} if owned else {},
    ))

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 15)

    monkeypatch.setattr(push_timeout, "run_bounded", timeout)
    expected = SystemExit if owned else subprocess.TimeoutExpired
    with pytest.raises(expected):
        static._git(tmp_path, "status")


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

    def snapshot(*args, **kwargs):
        value = original(*args, **kwargs)
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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("tracked", [False, True])
def test_generated_file_directory_migrations_preflight_before_cleanup(copy_workspace, reverse, tracked):
    root, home = copy_workspace
    template, _ = provider(root, home)
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    destinations = [
        ".github/instructions/demo/topic.instructions.md",
        ".github/instructions/demo/topic.local.instructions.md/body.instructions.md",
    ]
    if reverse:
        destinations.reverse()

    def select(destination):
        data["projections"][0]["destination"] = destination
        write_json(declaration, data)
        relative = destination.removeprefix(".github/instructions/").removesuffix(".instructions.md")
        return root / static.NAMESPACE / "market" / (relative + ".local.instructions.md")

    before = select(destinations[0])
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = before.read_bytes()
    if tracked:
        subprocess.run(["git", "-C", str(root), "add", "-f", str(before)], check=True, capture_output=True)
    template.write_bytes(b"new declaration")
    after = select(destinations[1])
    result = lcr.refresh_local_cache(root)
    if tracked:
        assert result.status == "failed"
        assert before.read_bytes() == previous
        assert not after.is_file()
    else:
        assert result.status == "ready", result.diagnostic
        assert (result.changed, result.removed) == (1, 1)
        assert after.read_bytes() == template.read_bytes()
        assert not before.is_file()


@pytest.mark.guard
def test_publication_lock_coalesces_case_insensitive_checkout_aliases(tmp_path, monkeypatch):
    from agent_worktrees import tracking

    paths = []
    monkeypatch.setattr(static.os.path, "normcase", lambda value: value)
    monkeypatch.setattr(tracking, "_RecordLock", lambda path, **kwargs: paths.append(path))
    static.publication_lock(tmp_path / "Checkout")
    static.publication_lock(tmp_path / "checkout")
    assert len(paths) == 2 and paths[0] == paths[1]
    composed, decomposed = tmp_path / "caf\u00e9", tmp_path / "cafe\u0301"
    static.publication_lock(composed)
    static.publication_lock(decomposed)
    assert paths[2] == paths[3]
    assert knowledge_plugins._overlay_lock_target(composed) == knowledge_plugins._overlay_lock_target(decomposed)


def test_unicode_equivalent_destinations_collide_before_cleanup(copy_workspace):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data["projections"] = [
        {**data["projections"][0], "id": str(index),
         "destination": f".github/instructions/demo/{name}.instructions.md"}
        for index, name in enumerate(("caf\u00e9", "cafe\u0301"))
    ]
    write_json(declaration, data)
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "colliding" in result.detail
    assert output.read_bytes() == previous


@pytest.mark.parametrize("error", [OSError, ValueError, TimeoutError])
def test_pending_marker_clear_errors_preserve_plugin_error_boundary(copy_workspace, monkeypatch, error):
    root, home = copy_workspace
    lcr.mark_pair_pending(root)

    def fail(*args, **kwargs):
        raise error("marker cleanup refused")

    monkeypatch.setattr(static, "publication_lock", fail)
    with pytest.raises(knowledge_plugins.KnowledgePluginError, match="marker cleanup refused"):
        lcr.complete_pair_composition(root, {"paired": True, "action": "composed"})
    assert static.pair_pending_path(root).exists()


def test_declared_source_cannot_be_deleted_as_disposable_output(copy_workspace):
    root, home = copy_workspace
    template, _ = provider(root, home)
    import shutil

    catalog = root / static.NAMESPACE / "embedded"
    shutil.copytree(template.parents[1], catalog / "demo")
    write_json(catalog / "marketplace.json", {
        "name": "embedded", "plugins": [{"name": "demo", "source": "./demo"}],
    })
    write_json(root / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"demo@embedded": True},
        "extraKnownMarketplaces": {"embedded": {"source": {"source": "directory", "path": str(catalog)}}},
    })
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "overlaps" in result.detail
    assert (catalog / "demo" / "instructions" / "fallback.md").read_bytes() == template.read_bytes()
    assert (catalog / "demo" / "plugin.json").exists()


def test_declared_template_cannot_be_deleted_as_disposable_output(copy_workspace):
    root, home = copy_workspace
    source = root / static.NAMESPACE / "source.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source must remain untouched")
    write_json(root / "plugin.json", {"name": "demo"})
    write_json(root / "marketplace.json", {
        "name": "embedded", "plugins": [{"name": "demo", "source": "."}],
    })
    write_json(root / "instruction-projections.json", {
        "schema": "copilot-extensions.instruction-projections", "version": 1,
        "projections": [{
            "id": "body", "template": f"{static.NAMESPACE}/source.md",
            "destination": ".github/instructions/demo/body.instructions.md",
        }],
    })
    write_json(root / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"demo@embedded": True},
        "extraKnownMarketplaces": {"embedded": {"source": {"source": "directory", "path": "."}}},
    })
    assert lcr.refresh_local_cache(root).status == "failed"
    assert source.read_bytes() == b"source must remain untouched"


def test_recursive_json_errors_remain_failed_diagnostics(copy_workspace, monkeypatch):
    root, home = copy_workspace
    provider(root, home)

    def recursive_json(*args, **kwargs):
        raise RecursionError("recursive JSON input")

    monkeypatch.setattr(static.json, "loads", recursive_json)
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "recursive JSON input" in result.detail


def test_pending_pair_blocks_final_noop_and_backup_without_cleanup(copy_workspace, monkeypatch, capsys):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    lcr.mark_pair_pending(root)
    template.write_bytes(b"new source not yet publishable")
    monkeypatch.setattr(context_cli, "_core_helper", lambda *a: lambda path: None)
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kw: {"action": "no-op", "paired": False})
    assert context_cli.cmd_knowledge_dispatch([
        "compose-plugins", "--cwd", str(root), "--prepare-projections", "--json",
    ]) == 3
    assert json.loads(capsys.readouterr().out)["projections"]["status"] == "failed"
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.read_bytes() != template.read_bytes()
    assert static.pair_pending_path(root).exists()


def test_final_preparation_targets_selected_knowledge_checkout(copy_workspace, tmp_path, monkeypatch, capsys):
    from dataclasses import replace
    from test_knowledge_plugins import _pair_resolution

    knowledge, home = copy_workspace
    template, output = provider(knowledge, home)
    harness = tmp_path / "harness"
    harness.mkdir()
    subprocess.run(["git", "init", "--quiet", str(harness)], check=True, capture_output=True)
    pair = _pair_resolution(harness, knowledge)
    pair = replace(pair, current=pair.sibling, sibling=pair.current)
    config = SimpleNamespace(knowledge_repo="private")
    monkeypatch.setattr(knowledge_plugins, "_resolve_pair_state", lambda **kw: (pair, config, None))
    monkeypatch.setattr(context_cli, "_core_helper", lambda *a: lambda path: None)
    assert context_cli.cmd_knowledge_dispatch([
        "compose-plugins", "--cwd", str(knowledge), "--prepare-projections", "--json",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert Path(summary["harness_path"]) == harness
    assert summary["projections"]["status"] == "ready"
    assert output.read_bytes() == template.read_bytes()
    assert not (harness / static.NAMESPACE).exists()


@pytest.mark.parametrize("entrypoint", ["direct", "final"])
@pytest.mark.parametrize("fault", ["settings", "config"])
def test_post_success_pair_failure_blocks_backup_until_repaired(
    copy_workspace, tmp_path, monkeypatch, capsys, entrypoint, fault,
):
    from test_knowledge_plugins import _pair_resolution

    root, home = copy_workspace
    template, output = provider(root, home)
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    settings = knowledge / ".github" / "copilot" / "settings.json"
    write_json(settings, {})
    resolution = _pair_resolution(root, knowledge)
    config = SimpleNamespace(knowledge_repo="private")
    monkeypatch.setattr(knowledge_plugins, "_resolve_pair_state", lambda **kw: (resolution, config, None))
    knowledge_plugins.compose_from_pair(cwd=root, config=config)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    template.write_bytes(b"new source awaiting repaired composition")
    if fault == "settings":
        settings.write_text("{", encoding="utf-8")
    else:
        monkeypatch.setattr(
            knowledge_plugins, "_resolve_pair_state",
            lambda **kw: (resolution, config, "configuration unreadable"),
        )
    if entrypoint == "direct":
        assert lcr.prepare_for_launch(root, compose_pair=True, config=config).status == "failed"
    else:
        monkeypatch.setattr(context_cli, "_core_helper", lambda *a: lambda path: None)
        assert context_cli.cmd_knowledge_dispatch([
            "compose-plugins", "--cwd", str(root), "--prepare-projections", "--json",
        ]) == 3
        assert json.loads(capsys.readouterr().out)["action"] == "error"
    assert static.pair_pending_path(root).exists()
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.read_bytes() == previous
    write_json(settings, {})
    monkeypatch.setattr(knowledge_plugins, "_resolve_pair_state", lambda **kw: (resolution, config, None))
    knowledge_plugins.compose_from_pair(cwd=root, config=config)
    assert not static.pair_pending_path(root).exists()
    assert lcr.refresh_local_cache(root).status == "ready"
    assert output.read_bytes() == template.read_bytes()


def test_portable_unicode_and_internal_space_destinations_are_not_filtered(copy_workspace):
    root, home = copy_workspace
    template, _ = provider(root, home)
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data["projections"][0]["id"] = "con"
    data["projections"][0]["destination"] = ".github/instructions/demo/caf\u00e9 notes/body name.instructions.md"
    write_json(declaration, data)
    result = lcr.refresh_local_cache(root)
    assert result.status == "ready", result.diagnostic
    output = root / static.NAMESPACE / "market" / "demo" / "caf\u00e9 notes" / "body name.local.instructions.md"
    assert output.read_bytes() == template.read_bytes()


def test_user_directory_marketplace_is_relative_to_copilot_settings(copy_workspace):
    root, home = copy_workspace
    template, _ = provider(root, home)
    import shutil

    catalog = home / ".copilot" / "marketplace"
    shutil.copytree(template.parents[1], catalog / "demo")
    write_json(catalog / "marketplace.json", {
        "name": "catalog-name", "plugins": [{"name": "demo", "source": "./demo"}],
    })
    write_json(root / ".github" / "copilot" / "settings.json", {})
    write_json(home / ".copilot" / "settings.json", {
        "enabledPlugins": {"demo@alias": True},
        "extraKnownMarketplaces": {"alias": {"source": {
            "source": "directory", "path": "./marketplace", "manifestName": "catalog-name",
        }}},
    })
    result = lcr.refresh_local_cache(root)
    assert result.status == "ready", result.diagnostic
    output = root / static.NAMESPACE / "alias" / "demo" / "fallback.local.instructions.md"
    assert output.read_bytes() == template.read_bytes()


@pytest.mark.parametrize("value", [True, {"enabled": True}, None, {"Case": "first", "case": "second"}])
@pytest.mark.parametrize("header", [False, True])
def test_builtin_plugin_settings_are_outside_marketplace_projection(copy_workspace, value, header):
    root, home = copy_workspace
    template, output = provider(root, home)
    write_json(root / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"builtin": value, "demo@market": True},
    })
    if header:
        settings = root / ".github" / "copilot" / "settings.json"
        settings.write_text("// canonical settings header\n" + settings.read_text(encoding="utf-8"), encoding="utf-8")
    assert lcr.refresh_local_cache(root).status == "ready"
    assert output.read_bytes() == template.read_bytes()


@pytest.mark.parametrize("identity", ["demo@@market", "demo@", "@market", "demo@market/path", "demo@ ", " @market"])
def test_malformed_marketplace_identity_blocks_publication(copy_workspace, identity):
    root, home = copy_workspace
    _, output = provider(root, home)
    write_json(root / ".github" / "copilot" / "settings.json", {"enabledPlugins": {identity: True}})
    assert lcr.refresh_local_cache(root).status == "failed"
    assert not output.exists()


def test_directory_catalog_identity_requires_explicit_mapping(copy_workspace):
    root, home = copy_workspace
    template, _ = provider(root, home)
    import shutil

    catalog = root / ".ai"
    shutil.copytree(template.parents[1], catalog / "demo")
    write_json(catalog / "marketplace.json", {
        "name": "different", "plugins": [{"name": "demo", "source": "./demo"}],
    })
    write_json(root / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"demo@alias": True},
        "extraKnownMarketplaces": {"alias": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    assert lcr.refresh_local_cache(root).status == "failed"
    assert not (root / static.NAMESPACE).exists()


def test_empty_registered_marketplace_cannot_fall_back_to_installed_payload(copy_workspace):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    template.write_bytes(b"must not be selected by malformed registration")
    write_json(root / ".github" / "copilot" / "settings.json", {
        "enabledPlugins": {"demo@market": True}, "extraKnownMarketplaces": {"market": {}},
    })
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.read_bytes() == previous


@pytest.mark.parametrize("kind", [123, "agents", None, {}])
def test_invalid_customization_kind_never_authorizes_stale_cleanup(copy_workspace, kind):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    declaration = template.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data["projections"][0]["customizationKind"] = kind
    write_json(declaration, data)
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.read_bytes() == previous


@pytest.mark.parametrize("node", ["root", "manifest"])
def test_installed_reparse_objects_rejected_by_shared_root_resolver(copy_workspace, monkeypatch, node):
    import stat

    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    previous = output.read_bytes()
    target = template.parents[1] if node == "root" else template.parents[1] / "plugin.json"
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR if node == "root" else stat.S_IFREG,
                st_file_attributes=0x400, st_reparse_tag=1,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert lcr.refresh_local_cache(root).status == "failed"
    assert output.read_bytes() == previous


@pytest.mark.guard
def test_git_preflights_reuse_case_insensitive_repository_environment_isolation(tmp_path, monkeypatch):
    from agent_worktrees import push_timeout

    captured = []
    monkeypatch.setattr(static.os, "environ", {
        "git_dir": "foreign", "Git_Index_File": "foreign-index",
        "git_literal_pathspecs": "1", "GIT_GLOB_PATHSPECS": "1", "PATH": "preserved",
    })
    monkeypatch.setattr(
        push_timeout, "run_bounded",
        lambda argv, **kwargs: captured.append(kwargs["env"]) or subprocess.CompletedProcess(argv, 0, b"", b""),
    )
    static._git(tmp_path, "ls-files")
    assert not any(
        key.upper() in {"GIT_DIR", "GIT_INDEX_FILE", *static._PATHSPEC_ENV}
        for key in captured[0]
    )
    assert captured[0]["PATH"] == "preserved"
    assert captured[0]["GIT_TERMINAL_PROMPT"] == "0"


def test_each_git_operation_uses_only_remaining_explicit_budget(tmp_path, monkeypatch):
    from agent_worktrees import push_timeout

    clock = [100.0]
    timeouts = []
    monkeypatch.setattr(static, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def run(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += 2
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(push_timeout, "run_bounded", run)
    static._git(tmp_path, "ls-files", deadline=105)
    static._git(tmp_path, "diff", deadline=105)
    assert timeouts == [5, 3]
    clock[0] = 105
    with pytest.raises(TimeoutError):
        static._git(tmp_path, "rev-parse", deadline=105)
    assert len(timeouts) == 2


@pytest.mark.parametrize("phase", ["rev-parse", "check-ignore"])
def test_expired_late_preflight_never_starts_copy(copy_workspace, monkeypatch, phase):
    root, home = copy_workspace
    _, output = provider(root, home)
    clock = [time.monotonic()]
    monkeypatch.setattr(static, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = static._git

    def git(root, *args, **kwargs):
        result = original(root, *args, **kwargs)
        if args[0] == phase:
            clock[0] = kwargs["deadline"] + 1
        return result

    monkeypatch.setattr(static, "_git", git)
    assert lcr.refresh_local_cache(root, timeout=10).status == "timeout"
    assert not output.exists()


@pytest.mark.parametrize("pending_pair", [False, True])
def test_real_knowledge_only_provider_copied_after_composition(copy_workspace, tmp_path, capsys, pending_pair):
    harness, home = copy_workspace
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    if pending_pair:
        lcr.mark_pair_pending(harness)
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
    if pending_pair:
        knowledge_plugins.compose(harness, knowledge, pair_id="repaired-pair", pair_kind="worktree")
    assert context_cli.cmd_knowledge_dispatch([
        "compose-plugins", "--harness-path", str(harness),
        "--knowledge-path", str(knowledge), "--prepare-projections", "--json",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["projections"]["changed"] == 1
    output = harness / static.NAMESPACE / "private" / "demo" / "fallback.local.instructions.md"
    assert output.read_bytes() == template.read_bytes()
    assert not static.pair_pending_path(harness).exists()


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


@pytest.mark.guard
def test_launcher_deferral_and_final_batch_order():
    root = Path(__file__).resolve().parents[3]
    for name, defer, final, mux in (
        ("launch-session.ps1", "$env:AGENT_WORKTREES_DEFER_PROJECTIONS = '1'", "'--prepare-projections'", "# Apply environment"),
        ("launch-session.sh", 'AGENT_WORKTREES_DEFER_PROJECTIONS=1 "$PYTHON"', "--prepare-projections --json", "# ── tmux"),
    ):
        text = (root / "worktree-manager" / "bin" / name).read_text(encoding="utf-8-sig")
        assert text.index(defer) < text.index(final) < text.index(mux)
        assert text.count(final) == 1
