"""Real declaration-driven copy and launch boundary regressions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import context_cli, knowledge_plugins, local_cache_refresh as lcr
from agent_worktrees import resolve_cli, static_instructions as static
from conftest import _published_seed_environment, _seed_directory

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def copy_workspace(tmp_path, monkeypatch, _copy_workspace_seed):
    root, home = tmp_path / "repo", tmp_path / "home"
    shutil.copytree(_copy_workspace_seed, root, copy_function=shutil.copyfile)
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return root, home


@pytest.fixture(scope="session")
def _copy_workspace_seed(tmp_path_factory):
    """Reuse only empty Git setup, never payloads or publication state."""
    with _seed_directory(tmp_path_factory, "local-cache-empty-repo") as (seed, build):
        root = seed / "repo"
        if build:
            with _published_seed_environment(tmp_path_factory):
                subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
        return root


@pytest.mark.guard
@pytest.mark.parametrize("iteration", range(2))
def test_copy_workspace_stores_are_independent(copy_workspace, _copy_workspace_seed, tmp_path, iteration):
    root, home = copy_workspace
    sibling = tmp_path / "sibling"
    shutil.copytree(_copy_workspace_seed, sibling, copy_function=shutil.copyfile)

    def contents(path):
        return {
            file.relative_to(path): file.read_bytes()
            for file in path.rglob("*") if file.is_file()
        }

    before = contents(_copy_workspace_seed)
    assert contents(root) == contents(sibling) == before
    for relative in before:
        assert not (root / relative).samefile(_copy_workspace_seed / relative)
        assert not (root / relative).samefile(sibling / relative)
    (root / "tracked.txt").write_text(f"independent worktree {iteration}\n", encoding="ascii")
    with (root / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write("\n[fixture]\n\tisolated = true\n")
    with (root / ".git" / "info" / "exclude").open("a", encoding="utf-8") as handle:
        handle.write("\n/fixture-only/\n")
    subprocess.run(
        ["git", "-C", str(root), "add", "tracked.txt"], check=True, capture_output=True,
    )
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "-m", "Independent fixture",
    ], check=True, capture_output=True)
    assert (root / ".git" / "index").is_file()
    assert any((root / ".git" / "objects").rglob("??/*"))
    assert any((root / ".git" / "refs" / "heads").iterdir())
    assert not (root / ".git" / "objects" / "info" / "alternates").exists()
    assert contents(sibling) == before
    assert contents(_copy_workspace_seed) == before
    assert not list(home.iterdir())


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


def test_prepared_generation_reused_before_discovery_and_hook(copy_workspace, monkeypatch):
    root, home = copy_workspace
    _, output = provider(root, home)
    calls = []
    original = static._snapshot

    def snapshot(*args, **kwargs):
        calls.append("snapshot")
        return original(*args, **kwargs)

    monkeypatch.setattr(static, "_snapshot", snapshot)
    first = lcr.prepare_for_launch(root)
    assert first.status == "ready"
    assert first.generation
    assert not first.reused
    assert output.exists(), "publication precedes the consumer's first discovery"
    monkeypatch.setattr(static, "_git", lambda *a, **kw: pytest.fail("unchanged preparation spawned Git"))
    monkeypatch.setattr(lcr, "_queue_refresh", lambda *a: pytest.fail("current generation queued a worker"))
    second = lcr.prepare_for_launch(root)
    assert second.reused and second.generation == first.generation
    assert "ready" in lcr.sessionstart_diagnostic(str(root), deadline=time.time() + 3)
    assert calls == ["snapshot"]


def test_prepared_cache_without_worker_is_not_live_work(copy_workspace):
    from agent_worktrees import local_cache_worker

    root, home = copy_workspace
    provider(root, home)
    assert lcr.prepare_for_launch(root).status == "ready"
    assert not (lcr._worker_directory(root) / "request.json").exists()
    assert not local_cache_worker.is_active(root)


@pytest.mark.parametrize("change", ["payload", "settings", "missing-output", "extra-output", "receipt"])
def test_preparation_invalidates_actual_changes(copy_workspace, change):
    root, home = copy_workspace
    template, output = provider(root, home)
    first = lcr.prepare_for_launch(root)
    if change == "payload":
        template.write_bytes(b"next payload")
    elif change == "settings":
        provider(root, home, "second")
    elif change == "missing-output":
        output.unlink()
    elif change == "extra-output":
        (output.parent / "extra.local.instructions.md").write_text("stale")
    else:
        (lcr._worker_directory(root) / "prepared.json").write_text("{")
    second = lcr.prepare_for_launch(root)
    assert second.status == "ready" and not second.reused
    assert output.read_bytes() == template.read_bytes()
    if change in ("payload", "settings"):
        assert second.generation != first.generation


@pytest.mark.parametrize("failure", [
    "missing", "declaration", "template", "dangling-template", "dangling-declaration",
])
def test_missing_payload_does_not_withhold_independent_guidance(copy_workspace, failure):
    root, home = copy_workspace
    _, old = provider(root, home, "old")
    assert lcr.prepare_for_launch(root).status == "ready"
    _, safe = provider(root, home, "safe")
    if failure != "missing":
        broken, _ = provider(root, home, "missing")
        if failure == "template":
            broken.unlink()
        elif failure.startswith("dangling-"):
            path = broken if failure == "dangling-template" else broken.parents[1] / "instruction-projections.json"
            path.unlink()
            try:
                path.symlink_to("missing-target")
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    pytest.skip("native file-link privilege unavailable")
                raise
        else:
            (broken.parents[1] / "instruction-projections.json").write_text("{")
    settings = root / ".github" / "copilot" / "settings.json"
    write_json(settings, {"enabledPlugins": {"safe@market": True, "missing@market": True}})
    result = lcr.prepare_for_launch(root)
    assert result.status == "partial"
    assert result.blocking == 1 and "missing@market" in result.detail
    assert safe.is_file() and old.is_file()
    assert not result.reused


@pytest.mark.parametrize("initially_partial", [False, True])
def test_partial_source_cannot_overwrite_failed_provider_output(copy_workspace, initially_partial):
    root, home = copy_workspace
    broken, protected = provider(root, home, "broken", content=b"retain unresolved guidance")
    settings = root / ".github/copilot/settings.json"
    if initially_partial:
        data = json.loads(settings.read_text())
        data["enabledPlugins"]["missing@market"] = True
        write_json(settings, data)
    assert lcr.prepare_for_launch(root).status == ("partial" if initially_partial else "ready")
    broken.unlink()
    replacement, _ = provider(root, home, "replacement", content=b"must not replace another provider")
    declaration = replacement.parents[1] / "instruction-projections.json"
    data = json.loads(declaration.read_text())
    data["projections"][0]["destination"] = ".github/instructions/broken/fallback.instructions.md"
    write_json(declaration, data)
    _, independent = provider(root, home, "independent", content=b"safe independent guidance")
    result = lcr.prepare_for_launch(root)
    assert result.status == "partial", result.diagnostic
    assert protected.read_bytes() == b"retain unresolved guidance"
    assert independent.read_bytes() == b"safe independent guidance"
    assert "ownership" in result.detail


def test_partial_publication_preserves_legacy_unknown_slot_until_complete(copy_workspace):
    root, home = copy_workspace
    _, protected = provider(root, home, content=b"current canonical guidance")
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"unknown unresolved owner")
    _, independent = provider(root, home, "independent")
    settings = root / ".github/copilot/settings.json"
    data = json.loads(settings.read_text())
    data["enabledPlugins"]["missing@market"] = True
    write_json(settings, data)
    assert lcr.prepare_for_launch(root).status == "partial"
    assert protected.read_bytes() == b"unknown unresolved owner"
    assert independent.exists()
    data["enabledPlugins"]["missing@market"] = False
    write_json(settings, data)
    assert lcr.prepare_for_launch(root).status == "ready"
    assert protected.read_bytes() == b"current canonical guidance"


def test_partial_publication_can_update_its_proven_existing_source(copy_workspace):
    root, home = copy_workspace
    template, output = provider(root, home, content=b"first safe version")
    settings = root / ".github/copilot/settings.json"
    data = json.loads(settings.read_text())
    data["enabledPlugins"]["missing@market"] = True
    write_json(settings, data)
    assert lcr.prepare_for_launch(root).status == "partial"
    template.write_bytes(b"second safe version")
    result = lcr.prepare_for_launch(root)
    assert result.status == "partial" and result.changed == 1
    assert output.read_bytes() == b"second safe version"


@pytest.mark.parametrize("publisher", [
    "backup", "foreground", "foreground-partial", "during-probe", "post-copy-failure",
])
def test_late_worker_completion_reaches_actual_post_tool_hook(copy_workspace, monkeypatch, publisher):
    from agent_worktrees import local_cache_worker

    root, home = copy_workspace
    _, output = provider(root, home, content=b"fresh guidance")
    session = home / ".copilot" / "session-state" / "example-session"
    session.mkdir(parents=True)
    monkeypatch.setattr(lcr, "_queue_refresh", lambda *a: lcr.RefreshResult("queued"))
    if publisher == "during-probe":
        original = static.current
        began = time.time()
        monkeypatch.setattr(lcr, "time", SimpleNamespace(
            time=lambda: began, monotonic=time.monotonic, monotonic_ns=time.monotonic_ns,
        ))

        def finish_during_probe(*args, **kwargs):
            monkeypatch.setattr(static, "current", original)
            assert lcr.prepare_for_launch(root).changed == 1
            assert local_cache_worker.run(root, lcr._worker_directory(root)) == 0
            return None

        monkeypatch.setattr(static, "current", finish_during_probe)
    assert "queued" in lcr.sessionstart_diagnostic(
        str(root), deadline=time.time() + 5, session_id="example-session",
    )
    if publisher != "during-probe":
        assert not output.exists(), "backup has not delivered at discovery"
    if publisher == "foreground-partial":
        write_json(root / ".github/copilot/settings.json", {
            "enabledPlugins": {"demo@market": True, "missing@market": True},
        })
    if publisher.startswith("foreground"):
        assert lcr.prepare_for_launch(root).changed == 1
    directory = lcr._worker_directory(root)
    directory.mkdir(exist_ok=True)
    if publisher == "post-copy-failure":
        from agent_worktrees import static_preparation
        original_check = static_preparation.require_git_state

        def fail_after_copy(*args, **kwargs):
            if output.exists():
                raise ValueError("fixture post-copy verification failed")
            return original_check(*args, **kwargs)

        monkeypatch.setattr(static_preparation, "require_git_state", fail_after_copy)
    if publisher != "during-probe":
        assert local_cache_worker.run(root, directory) == (
            1 if publisher in ("foreground-partial", "post-copy-failure") else 0
        )
    script = Path(__file__).resolve().parents[1] / "scripts" / "nudge_status.py"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_WORKTREES_NUDGE="off")

    def post():
        result = subprocess.run(
            [sys.executable, str(script)], env=env,
            input=json.dumps({"sessionId": "example-session", "cwd": str(root), "toolName": "view"}),
            text=True, capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout) if result.stdout.strip() else {}

    notice = post()["additionalContext"]
    assert "Restart/new context is required" in notice
    assert "not proof of model admission" in notice
    if publisher == "foreground-partial":
        assert "incomplete" in notice and "Retain reviewed fallback" in notice
    assert not post()
    assert output.read_bytes() == b"fresh guidance"
    if publisher == "post-copy-failure":
        assert "failed" in notice and "repair" in notice
        monkeypatch.setattr(static_preparation, "require_git_state", original_check)
        repaired = lcr.prepare_for_launch(root)
        assert repaired.status == "ready"
        assert lcr.prepare_for_launch(root).reused


def test_probe_timeout_still_admits_bounded_backup(copy_workspace, monkeypatch):
    root, home = copy_workspace
    session = home / ".copilot" / "session-state" / "probe-session"
    session.mkdir(parents=True)
    queued = []

    def timeout(*args, **kwargs):
        raise TimeoutError("reuse probe budget exhausted")

    monkeypatch.setattr(static, "current", timeout)
    monkeypatch.setattr(lcr, "_queue_refresh", lambda cwd: queued.append(cwd) or lcr.RefreshResult("queued"))
    result = lcr.sessionstart_diagnostic(str(root), deadline=time.time() + 5, session_id="probe-session")
    assert "queued" in result
    assert queued == [str(root)]
    assert (session / "local-guidance-watch.json").is_file()


def test_notice_observes_publication_after_backup_result(copy_workspace):
    from agent_worktrees import local_cache_worker
    from test_nudge_status import nudge

    root, home = copy_workspace
    template, _ = provider(root, home)
    initial = lcr.prepare_for_launch(root)
    session = home / ".copilot/session-state/later-publication"
    session.mkdir(parents=True)
    local_cache_worker.watch_completion(root, "later-publication", initial_revision=initial.publication_revision)
    assert local_cache_worker.run(root, lcr._worker_directory(root)) == 0
    template.write_bytes(b"published after the backup returned its unchanged result")
    assert lcr.prepare_for_launch(root).changed == 1
    notice = nudge.decide(
        {"sessionId": "later-publication", "cwd": str(root)},
        home=home, env={"AGENT_WORKTREES_NUDGE": "off"},
    )
    assert "Restart/new context is required" in notice
    assert "reprepare" in notice


def test_probe_timeout_rechecks_remaining_hook_budget(copy_workspace, monkeypatch):
    root, _ = copy_workspace
    clock = [100.0]
    monkeypatch.setattr(lcr, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]))

    def timeout(*args, **kwargs):
        clock[0] = 101.0
        raise TimeoutError("reuse probe budget exhausted")

    monkeypatch.setattr(static, "current", timeout)
    monkeypatch.setattr(lcr, "_queue_refresh", lambda *a: pytest.fail("remaining budget too short"))
    assert "skipped" in lcr.sessionstart_diagnostic(str(root), deadline=102.0)


@pytest.mark.parametrize("kind", ["input", "output", "index"])
def test_deadline_expires_during_real_receipt_validation(copy_workspace, monkeypatch, kind):
    from agent_worktrees import static_preparation

    root, home = copy_workspace
    template, output = provider(root, home)
    if kind == "index":
        (root / "tracked.txt").write_text("fixture")
        subprocess.run(
            ["git", "-C", str(root), "add", "tracked.txt"], check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    assert lcr.prepare_for_launch(root).status == "ready"
    target = {"input": template, "output": output, "index": root / ".git/index"}[kind]
    clock = [100.0]
    monkeypatch.setattr(static, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = static_preparation._hash_file
    expired = []

    def expire_after_hash(path, deadline):
        value = original(path, deadline)
        if path == target and deadline is not None:
            clock[0] = deadline + 1
            expired.append(path)
        return value

    monkeypatch.setattr(static_preparation, "_hash_file", expire_after_hash)
    with pytest.raises(TimeoutError):
        static.current(root, home, deadline=101.0)
    assert expired == [target]


@pytest.mark.parametrize("kind", ["settings", "declaration"])
def test_symlinked_input_publishes_and_reuses(copy_workspace, kind):
    root, home = copy_workspace
    template, _ = provider(root, home)
    path = (
        root / ".github/copilot/settings.json" if kind == "settings"
        else template.parents[1] / "instruction-projections.json"
    )
    target = path.with_suffix(".target.json")
    path.rename(target)
    try:
        path.symlink_to(target.name)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("native file-link privilege unavailable")
        raise
    first = lcr.prepare_for_launch(root)
    assert first.status == "ready", first.diagnostic
    assert lcr.prepare_for_launch(root).reused


@pytest.mark.parametrize("cached", [False, True])
def test_cyclic_source_link_preserves_independent_publication(copy_workspace, cached):
    root, home = copy_workspace
    broken, old_output = provider(root, home, "broken", content=b"old guidance")
    healthy, healthy_output = provider(root, home, "healthy", content=b"old healthy guidance")
    if cached:
        assert lcr.prepare_for_launch(root).status == "ready"
    healthy.write_bytes(b"current healthy guidance")
    broken.unlink()
    try:
        broken.symlink_to(broken.name)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("native file-link privilege unavailable")
        raise
    result = lcr.prepare_for_launch(root)
    assert result.status == "partial", result.diagnostic
    assert result.blocking == 1 and not result.reused
    assert healthy_output.read_bytes() == b"current healthy guidance"
    if cached:
        assert old_output.read_bytes() == b"old guidance"


def test_new_ignore_negation_invalidates_cached_readiness(copy_workspace):
    root, home = copy_workspace
    _, output = provider(root, home)
    assert lcr.prepare_for_launch(root).status == "ready"
    (root / ".gitignore").write_text("!**/\n!*.local.instructions.md\n", encoding="utf-8")
    result = lcr.prepare_for_launch(root)
    assert result.status == "failed", result.diagnostic
    assert not result.reused
    assert output.exists()


def test_receipt_never_certifies_index_changed_after_preflight(copy_workspace, monkeypatch):
    from agent_worktrees import static_preparation

    root, home = copy_workspace
    _, output = provider(root, home)
    original = static_preparation.save

    def stage_before_save(*args, **kwargs):
        subprocess.run(["git", "-C", str(root), "add", "-f", str(output)], check=True, capture_output=True)
        return original(*args, **kwargs)

    monkeypatch.setattr(static_preparation, "save", stage_before_save)
    assert lcr.prepare_for_launch(root).status == "failed"
    assert lcr.prepare_for_launch(root).status == "failed"


def test_changed_index_refuses_before_output_mutation(copy_workspace, monkeypatch):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.prepare_for_launch(root).status == "ready"
    previous = output.read_bytes()
    template.write_bytes(b"new source must not overwrite newly staged output")
    original = static._git

    def stage_after_read(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if args[0] == "ls-files":
            subprocess.run(["git", "-C", str(root), "add", "-f", str(output)], check=True, capture_output=True)
        return value

    monkeypatch.setattr(static, "_git", stage_after_read)
    assert lcr.prepare_for_launch(root).status == "failed"
    assert output.read_bytes() == previous


@pytest.mark.guard
@pytest.mark.parametrize("legacy_progress", [False, True], ids=["timed", "legacy"])
def test_copy_opaque_bytes_without_packaged_engine(
    copy_workspace, monkeypatch, capsys, legacy_progress,
):
    root, home = copy_workspace
    from agent_worktrees import installer, launch_progress

    if legacy_progress:
        monkeypatch.setattr(launch_progress.output, "HumanOperation", None)
    else:
        assert launch_progress.output.HumanOperation is not None

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
        if legacy_progress:
            assert f"{stage}: starting" in captured.err
            assert f"{stage}: finished" in captured.err
        else:
            operation = f"launch/{stage.replace(' ', '-')}"
            assert f"{operation} starting [elapsed " in captured.err
            assert f"{operation} done [duration " in captured.err


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


@pytest.mark.guard
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
            fields = {
                name: getattr(value, name) for name in dir(value)
                if name.startswith("st_")
            }
            fields["st_mode"] = stat.S_IFIFO | 0o600
            return SimpleNamespace(**fields)
        return value

    def read_bytes(path):
        assert path != target, "opened a nonregular input"
        return original_read(path)

    original_git = static._git

    def git(path, *args, **kwargs):
        if kind == "exclude" and args[0] in ("ls-files", "diff", "check-ignore"):
            pytest.fail("Git consulted ignore rules before rejecting a nonregular exclude")
        return original_git(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", file_stat)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(static, "_git", git)
    native = original_stat(target, follow_symlinks=False)
    simulated = target.lstat()
    assert stat.S_ISFIFO(simulated.st_mode)
    for field in dir(native):
        if field.startswith("st_") and field != "st_mode":
            assert getattr(simulated, field) == getattr(native, field)
    result = lcr.refresh_local_cache(root)
    assert result.status == "failed"
    assert "not a regular file" in result.detail
    assert original_read(output) == original_read(template)


@pytest.mark.guard
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


@pytest.mark.guard
def test_exclude_is_reread_after_index_queries(copy_workspace, monkeypatch):
    root, home = copy_workspace
    template, output = provider(root, home)
    assert lcr.refresh_local_cache(root).status == "ready"
    template.write_bytes(b"new generation requires index preflight")
    exclude = root / ".git" / "info" / "exclude"
    original_git = static._git

    def git(path, *args, **kwargs):
        if args[0] == "ls-files":
            exclude.write_text("/independent-rule/\n", encoding="utf-8")
        return original_git(path, *args, **kwargs)

    monkeypatch.setattr(static, "_git", git)
    assert lcr.refresh_local_cache(root).status == "ready"
    assert exclude.read_text(encoding="utf-8").splitlines() == [
        "/independent-rule/", "", f"/{static.NAMESPACE}/",
    ]
    assert output.read_bytes() == template.read_bytes()


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
