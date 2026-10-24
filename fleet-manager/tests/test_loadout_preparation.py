"""Real Git publication/pull and durable exact-generation review, without deployment."""

from __future__ import annotations

import copy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from fleet_manager.loadout_preparation import PreparationStore
from fleet_manager.loadout_sources import GitSource
from fleet_manager.planning import PlanError, canonical, digest
from test_loadouts import inputs


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    root = Path(__file__).absolute().parents[2]
    monkeypatch.syspath_prepend(str(root / "libs" / "agent-procutil" / "src"))
    from agent_procutil.owned_process import run_caller_owned

    git = Path(shutil.which("git")).absolute()
    publisher, controller = tmp_path / "publisher", tmp_path / "controller"
    publisher.mkdir()
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def run(*arguments, cwd=publisher):
        result = run_caller_owned(
            [str(git), *arguments], cwd=cwd, env=environment, timeout=20,
            max_stdout_bytes=1024 * 1024, max_stderr_bytes=4096,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run("init", "--quiet", "-b", "published")
    run("config", "user.name", "Example")
    run("config", "user.email", "example@example.invalid")
    config, inventory, catalog = inputs(int(time.time()))
    inventory["expires_at"] += 500

    def publish(*, config=config, catalog=catalog):
        (publisher / "config.json").write_bytes(canonical(config))
        (publisher / "catalog.json").write_bytes(canonical(catalog))
        run("add", "config.json", "catalog.json")
        run("commit", "--quiet", "--allow-empty", "-m", "Publish selected loadout")
        return run("rev-parse", "HEAD")

    commit = publish()
    run("clone", "--quiet", "--", str(publisher), str(controller), cwd=tmp_path)

    def source(path="config.json"):
        return GitSource(
            source="adopted-" + path.split(".")[0], adoption_revision="adoption-1",
            checkout=controller, git=git, remote="origin", expected_remote=str(publisher),
            ref="refs/heads/published", path=path, run=run_caller_owned,
        )

    return {
        "source": source, "publish": publish, "run": run,
        "config": config, "inventory": inventory, "catalog": catalog, "commit": commit,
        "publisher": publisher, "controller": controller,
    }


def test_real_published_pull_ignores_unpublished_client_changes_and_retains_commit(repositories, monkeypatch):
    rig = repositories
    source = rig["source"]()
    (rig["controller"] / "config.json").write_text('{"unpublished":"not selected"}')
    monkeypatch.setenv("GIT_DIR", str(rig["publisher"]))
    monkeypatch.setenv("GIT_WORK_TREE", str(rig["publisher"]))
    snapshot = source.pull(deadline=time.monotonic() + 15)
    assert snapshot.commit == rig["commit"]
    assert snapshot.document() == rig["config"]
    changed = copy.deepcopy(rig["config"])
    changed["revision"] = "c2"
    revision = rig["publish"](config=changed)
    assert source.pull(deadline=time.monotonic() + 15).commit == revision
    assert source.pull(deadline=time.monotonic() + 15).document() == changed
    assert (rig["controller"] / "config.json").read_text() == '{"unpublished":"not selected"}'
    assert "publisher" not in str(snapshot.provenance())


@pytest.mark.parametrize("change", [
    {"ref": "HEAD"}, {"ref": "refs/heads/../other"},
    {"path": "../config.json"}, {"path": "linked/config.yaml"},
    {"remote": "--upload-pack=anything"}, {"checkout": Path("relative")},
])
def test_request_shaped_or_unsafe_source_selections_are_not_adopted(repositories, change):
    with pytest.raises(PlanError):
        replace(repositories["source"](), **change)


def test_changed_remote_and_missing_blob_refuse_without_echoing_source(repositories):
    source = repositories["source"]()
    with pytest.raises(PlanError, match="remote changed") as failure:
        replace(source, expected_remote="https://example.invalid/private").pull(
            deadline=time.monotonic() + 15)
    assert "example.invalid" not in str(failure.value)
    with pytest.raises(PlanError, match="ordinary JSON blob"):
        replace(source, path="missing.json").pull(deadline=time.monotonic() + 15)


def test_git_mode_symlink_is_never_followed_or_parsed(repositories):
    rig = repositories
    oid = rig["run"]("hash-object", "-w", "config.json")
    rig["run"]("update-index", "--cacheinfo", f"120000,{oid},config.json")
    rig["run"]("commit", "--quiet", "-m", "Publish a nonordinary selection")
    with pytest.raises(PlanError, match="ordinary JSON blob"):
        rig["source"]().pull(deadline=time.monotonic() + 15)


def test_repository_transaction_hooks_are_disabled_for_source_fetch(repositories, tmp_path):
    marker = tmp_path / "unexpected-hook"
    hook = repositories["controller"] / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f'#!/bin/sh\nprintf unexpected > "{marker.as_posix()}"\n', encoding="utf-8")
    hook.chmod(0o755)
    repositories["publish"]()
    repositories["source"]().pull(deadline=time.monotonic() + 15)
    assert not marker.exists()


def candidate(plan, *, revision="c1"):
    encoded = canonical(plan, output=True)
    import hashlib

    return {
        "schema": "fleet-manager.loadout-candidate", "version": 1,
        "fleet": "example", "installation": "controller-1",
        "status": "prepared", "reason": None, "sources": {"revision": revision},
        "plan_digest": hashlib.sha256(encoded).hexdigest(), "plan_bytes": len(encoded),
        "inventory_expires_at": plan["inventory_expires_at"], "execution_admission": "not-implemented",
    }


def test_durable_review_repeat_restart_cas_and_failed_refresh_preserve_last_valid(tmp_path):
    from fleet_manager.loadouts import design_loadout

    plan = design_loadout(*inputs(), now=150)
    selected = candidate(plan)
    path = tmp_path / "prepared.sqlite"
    store = PreparationStore(path, fleet="example", installation="controller-1")
    first = store.publish(selected, plan, expected=0, now=100)
    assert first["generation"] == 1 and first["sequence"] == 1
    assert store.publish(selected, plan, expected=1, now=101) == first
    reviewer = {"principal": "caller-a", "account": "account-a", "policy_revision": "policy-1"}
    approved = store.confirm(
        generation=1, selected_digest=digest(selected), reviewer=reviewer, now=102)
    assert approved["sequence"] == 2
    assert not approved["decision"]["activation_authorized"]
    store = PreparationStore(path, fleet="example", installation="controller-1")
    assert store.read() == approved
    assert store.confirm(
        generation=1, selected_digest=digest(selected), reviewer=reviewer, now=103) == approved
    blocked = {**selected, "status": "blocked", "reason": "published-source-unavailable",
               "plan_digest": None, "plan_bytes": 0, "inventory_expires_at": 0}
    changed = store.publish(blocked, None, expected=1, now=104)
    assert changed["generation"] == 2 and changed["decision"] is None and changed["plan"] is None
    with store._transaction() as connection:
        assert store._json(connection.execute("SELECT retained FROM proposal").fetchone()[0],
                           output=True) == plan
    with pytest.raises(PlanError, match="generation changed"):
        store.confirm(generation=1, selected_digest=digest(selected), reviewer=reviewer, now=105)
    with pytest.raises(PlanError, match="changed during"):
        store.publish(selected, plan, expected=1, now=106)
    with pytest.raises(PlanError, match="blocked or expired"):
        store.confirm(generation=2, selected_digest=changed["digest"], reviewer=reviewer, now=107)
    with pytest.raises(PlanError, match="identity mismatch"):
        PreparationStore(path, fleet="foreign", installation="controller-1")


def test_expired_plan_and_changed_reviewer_never_reuse_a_decision(tmp_path):
    from fleet_manager.loadouts import design_loadout

    plan = design_loadout(*inputs(), now=150)
    selected = candidate(plan)
    selected["inventory_expires_at"] = 200
    store = PreparationStore(tmp_path / "proposal.sqlite", fleet="example", installation="controller-1")
    current = store.publish(selected, plan, expected=0, now=150)
    reviewer = {"principal": "caller-a", "account": "account-a", "policy_revision": "policy-1"}
    with pytest.raises(PlanError, match="expired"):
        store.confirm(generation=1, selected_digest=current["digest"], reviewer=reviewer, now=200)
    store.confirm(generation=1, selected_digest=current["digest"], reviewer=reviewer, now=199)
    with pytest.raises(PlanError, match="another scope"):
        store.confirm(generation=1, selected_digest=current["digest"],
                      reviewer={**reviewer, "policy_revision": "policy-2"}, now=199)


def test_corrupt_retained_plan_cannot_be_presented_or_confirmed(tmp_path):
    from fleet_manager.loadouts import design_loadout

    plan = design_loadout(*inputs(), now=150)
    selected = candidate(plan)
    store = PreparationStore(tmp_path / "proposal.sqlite", fleet="example", installation="controller-1")
    store.publish(selected, plan, expected=0, now=150)
    corrupted = {**plan, "activation_authorized": True}
    with store._transaction() as connection:
        connection.execute("UPDATE proposal SET plan=?", (canonical(corrupted, output=True),))
    with pytest.raises(PlanError, match="differs"):
        store.read()


def test_exact_maximum_plan_pages_validate_full_content_once_and_refuse_stale_selection(tmp_path, monkeypatch):
    import hashlib

    from fleet_manager import loadout_preparation
    from fleet_manager.loadout_preparation import PLAN_CHUNK, InvalidPlanRange
    from fleet_manager.loadouts import design_loadout
    from fleet_manager.planning import MAX_PLAN_BYTES

    plan = design_loadout(*inputs(), now=150)
    plan["range_test_payload"] = ["x" * 65536] * 63
    plan["range_test_tail"] = ""
    plan["digest"] = digest({key: value for key, value in plan.items() if key != "digest"}, output=True)
    plan["range_test_tail"] = "y" * (MAX_PLAN_BYTES - len(canonical(plan, output=True)))
    plan["digest"] = digest({key: value for key, value in plan.items() if key != "digest"}, output=True)
    encoded = canonical(plan, output=True)
    assert len(encoded) == MAX_PLAN_BYTES
    selected = candidate(plan)
    path = tmp_path / "large-proposal.sqlite"
    store = PreparationStore(path, fleet="example", installation="controller-1")
    current = store.publish(selected, plan, expected=0, now=150)
    store = PreparationStore(path, fleet="example", installation="controller-1")
    original = loadout_preparation.canonical
    full_encodings = []

    def counted(value, **options):
        if isinstance(value, dict) and value.get("schema") == "fleet-manager.loadout-design":
            full_encodings.append(True)
        return original(value, **options)

    monkeypatch.setattr(loadout_preparation, "canonical", counted)
    assert "plan" not in store.read_metadata()
    assert full_encodings == []
    chunks = [
        store.read_plan_range(generation=1, selected_digest=current["digest"], offset=offset)
        for offset in range(0, len(encoded), PLAN_CHUNK)
    ]
    assert len(chunks) == 256 and all(len(chunk) == PLAN_CHUNK for chunk in chunks)
    assert b"".join(chunks) == encoded
    assert hashlib.sha256(b"".join(chunks)).hexdigest() == selected["plan_digest"]
    assert 1 <= len(full_encodings) <= 2
    encodings_after_download = len(full_encodings)
    assert store.read_metadata()["digest"] == current["digest"]
    assert len(full_encodings) == encodings_after_download
    with pytest.raises(InvalidPlanRange):
        store.read_plan_range(generation=1, selected_digest=current["digest"], offset=MAX_PLAN_BYTES)
    with pytest.raises(InvalidPlanRange):
        store.read_plan_range(generation=1, selected_digest=current["digest"], offset=1)
    changed = store.publish(
        {**selected, "sources": {"revision": "c2"}}, plan, expected=1, now=151,
    )
    with pytest.raises(PlanError, match="generation changed"):
        store.read_plan_range(generation=1, selected_digest=current["digest"], offset=PLAN_CHUNK)
    assert store.read_plan_range(
        generation=2, selected_digest=changed["digest"], offset=0) == encoded[:PLAN_CHUNK]


def test_built_installed_wheel_serves_real_source_to_gateway(tmp_path, repositories):
    from test_client_distribution import build_path, project_at

    run = repositories["source"]().run
    project, _ = project_at(tmp_path / "source")
    output = tmp_path / "artifacts"
    output.mkdir()
    built = run(
        [sys.executable, "-I", "-c",
         f"from setuptools.build_meta import build_wheel; build_wheel({build_path(output)!r})"],
        cwd=build_path(project), env=dict(os.environ), timeout=120,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=65536,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    artifact = next(output.glob("*.whl"))
    installed = tmp_path / "installed"
    uv = shutil.which("uv")
    assert uv, "installed consumer qualification requires the existing UV tool"
    install = run(
        [uv, "pip", "install", "--python", sys.executable, "--target", str(installed),
         "--no-index", "--no-deps", "--link-mode", "copy", str(artifact)],
        cwd=tmp_path, env=dict(os.environ), timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert install.returncode == 0, install.stderr
    test = Path(__file__).with_name("test_loadout_gateway.py")
    code = (
        "import pathlib,sys\n"
        "installed=pathlib.Path(sys.argv.pop(1))\n"
        "sys.path.insert(0,str(installed))\n"
        "import fleet_manager.loadout_gateway as gateway\n"
        "import fleet_manager.loadout_sources as sources\n"
        "import fleet_manager.loadout_preparation as preparation\n"
        "assert all(pathlib.Path(m.__file__).is_relative_to(installed) "
        "for m in (gateway,sources,preparation))\n"
        "import pytest\n"
        "raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',*sys.argv[1:]]))\n"
    )
    outcome = run(
        [sys.executable, "-I", "-B", "-c", code, str(installed),
         str(test) + "::test_real_source_pull_notification_plan_confirmation_repeat_and_restart",
         "--basetemp", str(tmp_path / "installed-tests")],
        cwd=tmp_path, env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, timeout=150,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=65536,
    )
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
