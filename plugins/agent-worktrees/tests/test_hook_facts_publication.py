"""The actual roster owner publishes bounded, generation-selected advisory files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import hook_facts_publication as facts
from agent_worktrees import config as cfg
from agent_worktrees.worktrees_runtime import WorktreesRuntime


@pytest.fixture
def owner(tmp_path):
    project = tmp_path / "project"
    (project / "worktrees").mkdir(parents=True)
    publisher = facts.HookFactsPublisher(
        tmp_path / "runtime", "a" * 32,
        project_root=lambda name: project if name == "project" else pytest.fail(name),
        context={"marketplaceId": "fixture-marketplace", "installReceipt": str(tmp_path / "receipt")},
    )
    runtime = WorktreesRuntime(tmp_path / "current.sqlite3", hook_facts=publisher)
    runtime.activate()
    try:
        yield runtime, publisher, project
    finally:
        runtime.shutdown()


def row(tmp_path, **changes):
    return {
        "id": "worktree", "creation_nonce": "first-incarnation",
        "path": str(tmp_path / "checkout"), "status": "active",
        "status_note_at": "2026-11-05T20:00:00Z",
        "last_session_id": "listing-session", "head_yielded": False,
        "live_session_ids": ["listing-session"],
        "title": "not needed by the hook", "summary": "not copied",
        "fact_provenance": {"record": {"quality": "current", "observed_at": 1}},
        **changes,
    }


def output_path(publisher, project):
    return project / "worktrees" / "worktree" / "hook-facts" / f"{publisher.generation}.json"


def test_accepted_row_is_detached_partial_and_shell_readable(owner, tmp_path):
    runtime, publisher, project = owner
    source = row(tmp_path)
    runtime.publish("project", [source], observed_at=1)
    path = output_path(publisher, project)
    value = json.loads(path.read_bytes())
    assert value["quality"] == "advisory" and value["completeness"] == "partial"
    assert value["scope"] == {
        "project": "project", "worktree_id": "worktree",
        "creation_nonce": "first-incarnation", "workspace": source["path"],
    }
    assert value["producer"]["projection_generation"] == runtime.store.generation
    assert value["producer"]["hook_facts_generation"] == publisher.generation
    assert value["producer"]["marketplaceId"] == "fixture-marketplace"
    assert value["facts"] == {
        "status": "active", "status_note_at": source["status_note_at"],
        "last_session_id": "listing-session", "head_yielded": False,
    }
    assert "title" not in value["facts"] and "summary" not in value["facts"]
    source["status"] = "changed outside the accepted store"
    assert json.loads(path.read_bytes())["facts"]["status"] == "active"
    assert runtime.health()["worktrees"]["hook_facts"]["generation"] == publisher.generation


def test_recorded_head_is_not_the_listing_or_execution_leg_resume_target(owner, tmp_path):
    from agent_worktrees import worktrees_projection as projection

    runtime, publisher, project = owner
    source = row(tmp_path, last_session_id="resumable")
    runtime.publish("project", [source], observed_at=1, record_heads={
        source["id"]: {
            "creation_nonce": source["creation_nonce"], "workspace": source["path"],
            "head_session": None,
        },
    })
    value = json.loads(output_path(publisher, project).read_bytes())
    assert value["version"] == 2
    assert value["facts"]["record_head_session"] is None
    assert value["facts"]["last_session_id"] == "resumable"
    # Freeze the old strict row allowlist after the new publisher has written.
    # Both wire contracts and durable fallback must remain readable on rollback.
    old_fields = projection.ROW_FIELDS - {"record_head_session"}
    for version in (1, 2):
        publication = runtime.store.read("project", contract_version=version)
        for item in publication["worktrees"]:
            legacy_row = {key: value for key, value in item.items() if key != "semantic_revision"}
            assert set(legacy_row) <= old_fields
            identity, encoded = projection._encode_row(legacy_row)
            assert projection._decode_row(identity, encoded) == legacy_row
        durable = projection.read_advisory(
            tmp_path / "current.sqlite3", "project", contract_version=version,
            wire_platform=cfg.detect_platform(),
        )
        assert durable["worktrees"] == publication["worktrees"]
    runtime.publish("project", [row(tmp_path)], observed_at=2)
    value = json.loads(output_path(publisher, project).read_bytes())
    assert value["version"] == 1
    assert "record_head_session" not in value["facts"]


@pytest.mark.parametrize("mismatch", ["missing-head", "incarnation", "workspace", "worktree"])
def test_head_observation_must_match_the_accepted_roster(owner, tmp_path, mismatch):
    runtime, publisher, project = owner
    source = row(tmp_path)
    head = {
        "creation_nonce": source["creation_nonce"], "workspace": source["path"],
        "head_session": "canonical-head",
    }
    if mismatch == "missing-head":
        head.pop("head_session")
    elif mismatch == "incarnation":
        head["creation_nonce"] = "previous-incarnation"
    elif mismatch == "workspace":
        head["workspace"] = str(tmp_path / "another-checkout")
    runtime.publish("project", [source], observed_at=1, record_heads={
        "another-worktree" if mismatch == "worktree" else source["id"]: head,
    })
    value = json.loads(output_path(publisher, project).read_bytes())
    assert value["version"] == 1
    assert "record_head_session" not in value["facts"]


@pytest.mark.parametrize("unrelated_change", [False, True])
def test_unrelated_or_observation_only_change_does_not_replace_file(
    owner, tmp_path, monkeypatch, unrelated_change,
):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    original = output_path(publisher, project).read_bytes()
    replacements = []
    monkeypatch.setattr(facts.os, "replace", lambda *args: replacements.append(args))
    source = row(tmp_path)
    source["fact_provenance"]["record"]["observed_at"] = 2
    if unrelated_change:
        source["title"] = "a new title does not change the sampled advisory fields"
    runtime.publish("project", [source], observed_at=2)
    assert replacements == []
    assert output_path(publisher, project).read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"creation_nonce": None},
        {"path": None},
        {"fact_provenance": {}},
        {"live_session_ids": []},
        {"fact_provenance": {"record": {"quality": "unavailable", "observed_at": None}}},
    ],
)
def test_unqualified_record_withdraws_view_without_fabricating_empty_facts(
    owner, tmp_path, changes,
):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    path = output_path(publisher, project)
    assert path.exists()
    runtime.publish("project", [row(tmp_path, **changes)], observed_at=2)
    assert not path.exists()
    assert runtime.handle({"contract_version": 1, "project": "project"})["ok"]


def test_removed_failed_and_closed_publications_withdraw_and_recover(owner, tmp_path):
    runtime, publisher, project = owner
    path = output_path(publisher, project)
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    runtime.publication_failed("project", OSError("fixture failure"))
    assert not path.exists()
    runtime.publish("project", [row(tmp_path)], observed_at=2)
    assert path.exists()
    runtime.publish("project", [], observed_at=3)
    assert not path.exists()
    runtime.publish("project", [row(tmp_path)], observed_at=4)
    runtime.close_admission()
    assert not path.exists()
    publisher.publish("project", runtime.store.read("project", contract_version=2))
    assert not path.exists()
    runtime.activate()
    runtime.publish("project", [row(tmp_path)], observed_at=5)
    assert path.exists()


@pytest.mark.parametrize("missing", ["file", "hook-facts", "worktree"])
def test_missing_derived_path_does_not_withdraw_surviving_worktree(owner, tmp_path, missing):
    runtime, publisher, project = owner
    survivor = row(tmp_path, id="survivor")
    runtime.publish("project", [row(tmp_path), survivor], observed_at=1)
    removed = output_path(publisher, project)
    retained = project / "worktrees" / "survivor" / "hook-facts" / removed.name
    original = retained.read_bytes()
    removed.unlink()
    if missing != "file":
        removed.parent.rmdir()
    if missing == "worktree":
        removed.parent.parent.rmdir()
    for observed_at in (2, 3):
        runtime.publish("project", [survivor], observed_at=observed_at)
        assert retained.read_bytes() == original
        assert publisher.error is None
    runtime.publish("project", [row(tmp_path), survivor], observed_at=4)
    assert removed.is_file() and retained.read_bytes() == original


def test_withdrawal_refuses_replaced_derived_directory(owner, tmp_path):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    path = output_path(publisher, project)
    path.unlink()
    path.parent.rmdir()
    path.parent.write_bytes(b"not an owned directory")
    publisher.withdraw("project")
    assert path.parent.read_bytes() == b"not an owned directory"
    assert publisher.error == "ValueError"


def test_same_build_replacement_has_distinct_files_and_old_close_is_local(owner, tmp_path):
    runtime, old, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    new = facts.HookFactsPublisher(
        tmp_path / "runtime", "a" * 32, project_root=lambda name: project,
    )
    new.activate()
    new.publish("project", runtime.store.read("project", contract_version=2))
    new_path = output_path(new, project)
    assert new.generation != old.generation and new_path.exists()
    old.close()
    assert not output_path(old, project).exists()
    assert json.loads(new_path.read_bytes())["producer"]["hook_facts_generation"] == new.generation
    new.close()


@pytest.mark.parametrize("finish", ["withdraw", "close"])
def test_project_root_resolves_once_per_publication_and_withdrawal(
    owner, tmp_path, monkeypatch, finish,
):
    runtime, publisher, project = owner
    calls = []
    monkeypatch.setattr(publisher, "_project_root", lambda name: calls.append(name) or project)
    rows = [row(tmp_path, id=name) for name in ("worktree", "second", "third")]
    runtime.publish("project", rows, observed_at=1)
    assert calls == ["project"]
    calls.clear()
    runtime.publish("project", rows, observed_at=2)
    assert calls == ["project"]
    calls.clear()
    if finish == "close":
        publisher.close()
    else:
        publisher.withdraw("project")
    assert calls == ["project"]
    assert not list((project / "worktrees").rglob("*.json"))


def test_monitor_factory_composes_the_original_runtime_and_file_owner(tmp_path, monkeypatch):
    from agent_worktrees import status_monitor_cutover, status_monitor_runtime
    from agent_worktrees.worktrees_runtime import cfg

    project = tmp_path / "project"
    (project / "worktrees").mkdir(parents=True)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path / "runtime")
    monkeypatch.setattr(status_monitor_cutover, "active_generation_for_pid", lambda pid: "elected")
    monkeypatch.setattr(cfg, "project_dir", lambda name: project)
    context = {"marketplaceId": "fixture-selected-source"}
    runtime = WorktreesRuntime.for_monitor(None, lambda: True, context)
    try:
        runtime.activate()
        runtime.publish("project", [row(tmp_path)], observed_at=1)
        value = json.loads(output_path(runtime.hook_facts, project).read_bytes())
        assert value["producer"]["marketplaceId"] == context["marketplaceId"]
        assert value["producer"]["runtime_root"] == str(tmp_path / "runtime")
    finally:
        runtime.shutdown()


def test_failed_atomic_replace_withdraws_old_view_but_preserves_roster(owner, tmp_path, monkeypatch):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)

    def fail_replace(*args):
        raise OSError("fixture sharing failure")

    monkeypatch.setattr(facts.os, "replace", fail_replace)
    runtime.publish("project", [row(tmp_path, status="completed")], observed_at=2)
    path = output_path(publisher, project)
    assert not path.exists()
    assert list(path.parent.iterdir()) == []
    assert publisher.error == "OSError"
    assert runtime.handle({"contract_version": 1, "project": "project"})["worktrees"][0]["status"] == "completed"


def test_oversized_utf8_fact_withdraws_optional_view(owner, tmp_path):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    publication = runtime.store.read("project", contract_version=2)
    publication["worktrees"][0]["status_note_at"] = "\u00e9" * (facts.MAX_STRING_BYTES // 2 + 1)
    publisher.publish("project", publication)
    assert not output_path(publisher, project).exists()
    assert publisher.error == "ValueError"


def test_output_hard_link_is_refused_without_touching_its_other_name(owner, tmp_path):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    path = output_path(publisher, project)
    other = tmp_path / "other.json"
    os.link(path, other)
    original = other.read_bytes()
    runtime.publish("project", [row(tmp_path, status="completed")], observed_at=2)
    assert other.read_bytes() == original and path.read_bytes() == original
    assert publisher.error == "ValueError"
    other.unlink()


def test_unsafe_derived_directory_never_receives_publication(owner, tmp_path):
    runtime, publisher, project = owner
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    target = project / "worktrees" / "worktree"
    try:
        target.symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable to this test identity")
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    assert list(foreign.iterdir()) == [] and publisher.error == "ValueError"


def test_project_overflow_withdraws_without_partial_views(owner, tmp_path, monkeypatch):
    runtime, publisher, project = owner
    runtime.publish("project", [row(tmp_path)], observed_at=1)
    monkeypatch.setattr(facts, "MAX_VIEWS", 1)
    runtime.publish("project", [row(tmp_path), row(tmp_path, id="second")], observed_at=2)
    assert not output_path(publisher, project).exists()
    assert not (project / "worktrees" / "second").exists()
    assert publisher.error == "ValueError"


@pytest.mark.parametrize("shell_name", ["pwsh", "powershell"])
@pytest.mark.parametrize(
    "case",
    [
        "current", "offset-note", "missing-support", "wrong-incarnation", "wrong-publisher",
        "wrong-build", "malformed", "oversized", "invalid-utf8", "unknown-field",
        "boolean-revision", "wrong-quality", "wrong-case", "typed-head",
        "boolean-quality", "array-quality", "boolean-completeness", "array-completeness",
    ],
)
def test_real_shell_reads_only_the_exact_bounded_advisory_view(
    owner, tmp_path, shell_name, case,
):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is unavailable")
    runtime, publisher, project = owner
    note = "2026-11-05T20:00:00.123+05:30" if case == "offset-note" else "2026-11-05T20:00:00Z"
    runtime.publish(
        "project", [row(tmp_path, status='active "quoted" \u00e9', status_note_at=note)],
        observed_at=1,
    )
    path = output_path(publisher, project)
    query = {
        "discovery": {
            "hook_facts_version": 1, "hook_facts_generation": publisher.generation,
            **{key: publisher.producer[key] for key in (
                "runtime_generation", "marketplaceId", "installReceipt", "pluginRoot",
            )},
        },
        "runtimeRoot": publisher.producer["runtime_root"], "projectRoot": str(project),
        "project": "project", "worktreeId": "worktree",
        "creationNonce": "first-incarnation", "workspace": str(tmp_path / "checkout"),
    }
    if case == "missing-support":
        query["discovery"]["hook_facts_version"] = None
    elif case == "wrong-incarnation":
        query["creationNonce"] = "another-incarnation"
    elif case == "wrong-publisher":
        query["discovery"]["hook_facts_generation"] = "b" * 32
    elif case == "wrong-build":
        query["discovery"]["runtime_generation"] = "b" * 32
    elif case == "malformed":
        path.write_bytes(b'{"version":')
    elif case == "oversized":
        path.write_bytes(b" " * facts.MAX_BYTES + path.read_bytes())
    elif case == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif case != "current":
        value = json.loads(path.read_bytes())
        if case == "unknown-field":
            value["permission"] = True
        elif case == "boolean-revision":
            value["revision"] = True
        elif case == "wrong-quality":
            value["quality"] = "current"
        elif case == "wrong-case":
            value["Scope"] = value.pop("scope")
        elif case == "typed-head":
            value["facts"]["head_yielded"] = "false"
        elif case in ("boolean-quality", "array-quality"):
            value["quality"] = True if case == "boolean-quality" else []
        elif case in ("boolean-completeness", "array-completeness"):
            value["completeness"] = True if case == "boolean-completeness" else []
        path.write_text(json.dumps(value), encoding="utf-8")
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    script = scripts / "hook-facts.ps1"
    command = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. 'POST'
. 'SCRIPT'
function python { throw 'unexpected Python execution' }
function Get-Command { throw 'unexpected command discovery' }
function Invoke-ResidentPostTool { throw 'unexpected resident round trip' }
$query = ConvertFrom-PostToolJson ([Console]::In.ReadToEnd())
$discovery = ConvertTo-PostToolFactMap $query.discovery @(
    'hook_facts_version', 'hook_facts_generation', 'runtime_generation',
    'marketplaceId', 'installReceipt', 'pluginRoot'
)
$value = Read-PostToolHookFacts -Discovery $discovery -RuntimeRoot $query.runtimeRoot `
    -ProjectRoot $query.projectRoot -Project $query.project -WorktreeId $query.worktreeId `
    -CreationNonce $query.creationNonce -Workspace $query.workspace
if ($null -eq $value) { 'null' } else { ConvertTo-PostToolJson $value }
""".replace("SCRIPT", str(script).replace("'", "''")).replace(
        "POST", str(scripts / "post-tool-hook.ps1").replace("'", "''"),
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(query), capture_output=True, text=True, encoding="utf-8",
        timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    value = json.loads(result.stdout)
    if case in ("current", "offset-note"):
        if value is None:
            source = script.read_text(encoding="utf-8")
            start = source.index("function Read-PostToolHookFacts")
            end = len(source)
            lines = source[start:end].splitlines()
            last = max(index for index, line in enumerate(lines) if "return $null" in line)
            for index, line in enumerate(lines):
                lines[index] = line.replace(
                    "return $null", "throw $_" if index == last else f"throw 'rejected at reader line {index + 1}'",
                )
            diagnostic = tmp_path / "diagnostic-post.ps1"
            diagnostic.write_text(source[:start] + "\n".join(lines) + "\n" + source[end:], encoding="utf-8")
            detail = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-Command",
                 command.replace(str(script).replace("'", "''"), str(diagnostic).replace("'", "''"))],
                input=json.dumps(query), capture_output=True, text=True, encoding="utf-8",
                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            pytest.fail(detail.stderr or detail.stdout)
        assert value["facts"]["status"] == 'active "quoted" \u00e9'
        assert value["facts"]["status_note_at"] == note
        assert value["producer"]["hook_facts_generation"] == publisher.generation
    else:
        assert value is None
