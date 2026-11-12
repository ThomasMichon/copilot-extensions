"""Real native owner CLI, redirected roots and exact source-safety boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import threading
import time

from agent_procutil import no_window_kwargs
import pytest
import yaml

from agent_worktrees import config as cfg, repos
from agent_worktrees.repos_configuration import apply, preview
from agent_worktrees.repos_configuration_sources import ConfigurationError

SECRET = "github_pat_SENTINEL_DO_NOT_RENDER"


@pytest.mark.parametrize("outcome", ["absent", "disappeared", "empty"])
def test_mapping_acquisition_owner_distinguishes_empty_without_replay(tmp_path, monkeypatch, outcome):
    """Strict acquisition distinguishes empty values without repeating native reads."""
    from agent_worktrees import repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    if outcome != "absent":
        path.write_text("", encoding="utf-8")
    original_read, original_parse, original_exists = Path.read_bytes, sources.parse_mapping, Path.exists
    reads, parses = [], []

    def read(candidate):
        if candidate == path:
            reads.append(candidate)
            if outcome == "disappeared":
                raise FileNotFoundError("synthetic disappearance after selection")
        return original_read(candidate)

    def parse(text):
        parses.append(text)
        return original_parse(text)

    def exists(candidate):
        if candidate == path:
            pytest.fail("strict owner must use its original read, not add a presence probe")
        return original_exists(candidate)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(sources, "parse_mapping", parse)
    assert sources.mapping(path) == {}
    assert reads == [path]
    assert parses == ([""] if outcome == "empty" else [])
    reads.clear()
    parses.clear()
    report = sources.mapping_report(path)
    assert report.value == {}
    assert reads == [path]
    assert parses == ([""] if outcome == "empty" else [])
    assert report.authority.value == ("complete" if outcome == "empty" else "absent")
    assert any(o.source == str(path) for o in report.observations)
    assert report.reusable is False
    assert "stable-owner-input-frontier" in report.missing_coverage


def test_mapping_acquisition_owner_preserves_invalid_composition(tmp_path, monkeypatch):
    """The strict report keeps the parser's fatal sanitized failure."""
    from agent_worktrees import config_acquisition, repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    path.write_text("private_setting: [\n", encoding="utf-8")
    original = Path.read_bytes
    reads = []

    def read(candidate):
        if candidate == path:
            reads.append(candidate)
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", read)
    for load in (sources.mapping, lambda candidate: sources.mapping_report(candidate)):
        reads.clear()
        with pytest.raises(ConfigurationError) as caught:
            load(path)
        assert caught.value.code == "invalid-composition"
        assert "private_setting" not in str(caught.value)
        assert reads == [path]
        assert config_acquisition.current_observations() is None


def test_mapping_acquisition_owner_preserves_nonabsence_exception_identity(tmp_path, monkeypatch):
    """A raw non-absence failure must not become an optional empty mapping."""
    import errno
    from agent_worktrees import config_acquisition, repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    failure = OSError(errno.ELOOP, "synthetic non-absence read failure")
    reads = []

    def read(candidate):
        assert candidate == path
        reads.append(candidate)
        raise failure

    monkeypatch.setattr(Path, "read_bytes", read)
    for load in (sources.mapping, lambda candidate: sources.mapping_report(candidate)):
        reads.clear()
        with pytest.raises(OSError) as caught:
            load(path)
        assert caught.value is failure
        assert reads == [path]
        assert config_acquisition.current_observations() is None


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    previous = cfg.active_project()
    cfg.set_active_project("sample")
    try:
        for name in ("repository", "knowledge", "plugin", "preset"):
            root = tmp_path / name
            root.mkdir()
            (root / "sentinel.yaml").write_text(
                yaml.safe_dump({"session_env": {"PRIVATE_SENTINEL": SECRET}}), encoding="utf-8",
            )
        registry = repos._repos_yaml_path()
        registry.parent.mkdir()
        registry.write_text(yaml.safe_dump({
            "unrecognized_owner_metadata": {"sentinel": SECRET},
            "repos": {
                "sample": {
                    "class": "worktree", repos._current_platform(): str(tmp_path / "repository"),
                    "remote": "https://github.com/example/sample.git",
                    "agent": False, "custom_owner_metadata": {"secret": SECRET},
                },
                "stale": {"class": "reference", repos._current_platform(): str(tmp_path / "gone")},
                "foreign": {"class": "reference"},
            },
        }), encoding="utf-8")
        cfg.global_config_path().write_text(
            yaml.safe_dump({"default_copilot_account": "inherited", "session_env": {"SECRET": SECRET}}),
            encoding="utf-8",
        )
        yield tmp_path
    finally:
        cfg.set_active_project(previous)


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "configuration", *args],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )


def request(command, *args, name="sample"):
    return cli(
        command, "--context-project", "sample", "--repository", name, *args,
    )


def tree(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def local_knowledge(native, *, bare=False):
    source = native / "repository" / ".agent-worktrees" / "config.yaml"
    source.parent.mkdir()
    source.write_text("stateless: true\nrequires_external_state_root: true\n", encoding="utf-8")
    root = native / "knowledge"
    child = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "--quiet", *(["--bare"] if bare is True else []), str(root)],
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert child.returncode == 0, child.stderr
    if bare == "converted":
        clone = native / "converted-knowledge"
        child = subprocess.run(
            ["git", "clone", "--local", "--quiet", str(root), str(clone)],
            capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        assert child.returncode == 0, child.stderr
        root = clone
        for arguments in (["remote", "remove", "origin"], ["config", "core.bare", "true"]):
            child = subprocess.run(
                ["git", "-C", str(root), *arguments],
                capture_output=True, text=True, timeout=5, **no_window_kwargs(),
            )
            assert child.returncode == 0, child.stderr
    return root


def prepared_knowledge_observer(native):
    from agent_worktrees import guarded_anchors

    root = local_knowledge(native)
    caller = native / "observer"
    for path in (caller, native / "repository"):
        result = subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", "--quiet", str(path)],
            capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    path = repos._repos_yaml_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["repos"]["observer"] = {
        "class": "worktree", repos._current_platform(): str(caller), "default_branch": "main",
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert guarded_anchors.prepare(caller) == ""
    return root, caller, guarded_anchors._reader()


@pytest.mark.parametrize(("bare", "remote", "included"), [
    (False, "", False), (True, "", False), ("converted", "", False),
    (False, "https://github.com/example/knowledge.git", False),
    (False, "https://github.com/example/knowledge.git", True),
], ids=["checkout", "bare", "converted-bare", "remote-metadata", "included-origin"])
def test_real_local_knowledge_registration_is_registry_only_and_does_not_bind(native, bare, remote, included):
    root = local_knowledge(native, bare=bare)
    if remote:
        arguments = ["remote", "add", "origin", remote]
        if included:
            include = native / "origin.gitconfig"
            include.write_text(f'[remote "origin"]\n    url = {remote}\n', encoding="utf-8")
            arguments = ["config", "include.path", str(include)]
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    before = tree(native)
    catalog = cli("catalog", "--context-project", "sample")
    assert catalog.returncode == 0, catalog.stderr
    rows = json.loads(catalog.stdout)["entries"]
    action = next(row for row in rows if row.get("knowledge_registration_available") == "true")
    assert action["id"] == "action:knowledge-registration" and action["knowledge_bindable"] == "false"
    fields = ("--checkout-path", str(root), "--default-branch", "main", "--account", "chosen-login")
    proposed = request("knowledge-register-preview", *fields, name="local-knowledge")
    assert proposed.returncode == 0, proposed.stderr
    previewed = json.loads(proposed.stdout)
    assert previewed["target"] == action["configuration"]["target"]
    assert tree(native) == before
    old_host = request("knowledge-register-apply", *fields, "--preview-token", "", name="local-knowledge")
    assert old_host.returncode and json.loads(old_host.stdout)["code"] == "missing-preview"
    assert tree(native) == before
    completed = request(
        "knowledge-register-apply", *fields, "--preview-token", previewed["token"], name="local-knowledge",
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "persisted" and receipt["binding_changed"] is False
    raw = yaml.safe_load(repos._repos_yaml_path().read_text(encoding="utf-8"))
    expected = {
        "class": "knowledge", "agent": False, repos._current_platform(): str(root),
        "default_branch": "main", "account": "chosen-login",
    }
    if remote:
        expected["remote"] = remote
        assert any(remote in effect for effect in previewed["effects"])
    assert raw["repos"]["local-knowledge"] == expected
    assert raw["unrecognized_owner_metadata"] == {"sentinel": SECRET}
    assert raw["repos"]["sample"]["custom_owner_metadata"] == {"secret": SECRET}
    after = tree(native)
    missing = object()
    changed = {path for path in before.keys() | after.keys() if before.get(path, missing) != after.get(path, missing)}
    assert changed <= {
        str(Path(".agent-worktrees") / "repos.yaml"), str(Path(".agent-worktrees") / "repos.lock"),
        str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
        str(Path(".agent-worktrees") / "guarded-anchors"),
        str(Path(".agent-worktrees") / "guarded-anchors" / "publication.lock"),
    }
    assert str(Path(".agent-worktrees") / "repos.yaml") in changed
    assert SECRET not in proposed.stdout + proposed.stderr + completed.stdout + completed.stderr
    retry = request("knowledge-register-apply", *fields, "--preview-token", previewed["token"], name="local-knowledge")
    assert retry.returncode and json.loads(retry.stdout)["code"] == "registration-conflict"
    assert tree(native) == after


@pytest.mark.parametrize("change", ["registry", "binding", "origin"])
def test_local_knowledge_registration_rechecks_the_proposal_without_git_or_binding_writes(native, change):
    from agent_worktrees import repos_knowledge_registration as registration

    root = local_knowledge(native)
    token = registration.preview("sample", "local-knowledge", str(root), "main")["token"]
    if change == "registry":
        path = repos._repos_yaml_path()
        path.write_bytes(path.read_bytes() + b"\n# concurrent registry edit\n")
    elif change == "binding":
        local = cfg.project_dir("sample")
        local.mkdir(parents=True, exist_ok=True)
        (local / "config.yaml").write_text("knowledge_repo: another-selection\n", encoding="utf-8")
    else:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", "https://git.example.test/other/knowledge.git"],
            capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    before = tree(native)
    with pytest.raises(ConfigurationError):
        registration.apply("sample", "local-knowledge", str(root), "main", "", token)
    after = tree(native)
    assert {key: value for key, value in after.items() if not key.endswith(".lock")} == before


@pytest.mark.parametrize("owner", ["unregistered", "registered", "harness", "registered-metadata"])
def test_local_knowledge_registration_refuses_converted_bare_metadata_aliases(native, owner):
    root = local_knowledge(native, bare="converted")
    registry = repos._repos_yaml_path()
    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    if owner == "harness":
        raw["repos"]["sample"][repos._current_platform()] = str(root)
        policy = root / ".agent-worktrees" / "config.yaml"
        policy.parent.mkdir()
        policy.write_text("stateless: true\nrequires_external_state_root: true\n", encoding="utf-8")
    elif owner != "unregistered":
        raw["repos"]["existing"] = {
            "class": "knowledge", "agent": False,
            repos._current_platform(): str(root / ".git" if owner == "registered-metadata" else root),
        }
    registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    selected = root if owner == "registered-metadata" else root / ".git"
    before = tree(native)
    result = request(
        "knowledge-register-preview", "--checkout-path", str(selected),
        "--default-branch", "main", name="local-knowledge",
    )
    expected = "registration-conflict" if owner == "registered-metadata" else "invalid-checkout"
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["code"] == expected
    assert tree(native) == before


@pytest.mark.parametrize("classification", ["reference", "singleton", "knowledge"])
def test_local_knowledge_registration_requires_the_binding_owners_worktree_class(native, classification):
    local_knowledge(native)
    path = repos._repos_yaml_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["repos"]["sample"]["class"] = classification
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = tree(native)
    result = cli("catalog", "--context-project", "sample")
    assert result.returncode == 0, result.stderr
    assert not any(row.get("knowledge_registration_available") == "true" for row in json.loads(result.stdout)["entries"])
    result = request(
        "knowledge-register-preview", "--checkout-path", str(native / "knowledge"),
        "--default-branch", "main", name="local-knowledge",
    )
    assert result.returncode == 1 and json.loads(result.stdout)["code"] == "context-not-worktree"
    assert tree(native) == before


def symlink_registry(native):
    registry = repos._repos_yaml_path()
    backing = native / "registry-source.yaml"
    registry.rename(backing)
    try:
        registry.symlink_to(backing)
    except OSError as error:
        pytest.skip(f"native file symlinks are unavailable: {error}")
    return backing


def test_local_knowledge_registration_rejects_nonregular_registry_before_fingerprinting(native, monkeypatch):
    from agent_worktrees import repos_knowledge_registration as registration

    root = local_knowledge(native)
    registry = repos._repos_yaml_path()
    before = tree(native)
    original_stat, original_read = Path.stat, Path.read_bytes

    def fifo_mode(path, *args, **kwargs):
        return SimpleNamespace(st_mode=stat.S_IFIFO) if path == registry else original_stat(path, *args, **kwargs)

    def read(path):
        if path == registry:
            pytest.fail("A nonregular registry must be rejected before attempting to open it.")
        return original_read(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", fifo_mode)
        patch.setattr(Path, "read_bytes", read)
        with pytest.raises(repos.RepositoryResolutionError, match="regular file"):
            registration.preview("sample", "local-knowledge", str(root), "main")
    assert tree(native) == before


@pytest.mark.skipif(not hasattr(repos.os, "mkfifo"), reason="native FIFO creation is unavailable")
@pytest.mark.parametrize("operation", ["preview", "apply"])
def test_local_knowledge_registration_refuses_real_fifo_registry_promptly(native, operation):
    root = local_knowledge(native)
    registry = repos._repos_yaml_path()
    registry.unlink()
    repos.os.mkfifo(registry)
    before = tree(root)
    started = time.monotonic()
    result = request(
        "knowledge-register-" + operation, "--checkout-path", str(root),
        "--default-branch", "main",
        *(("--preview-token", "0" * 64) if operation == "apply" else ()), name="local-knowledge",
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "owner-read-or-write-failed"
    assert time.monotonic() - started < 5
    assert stat.S_ISFIFO(registry.stat().st_mode) and tree(root) == before


def test_local_knowledge_registration_refuses_symlinked_registry_without_replacing_its_owner(native):
    root = local_knowledge(native)
    backing = symlink_registry(native)
    registry = repos._repos_yaml_path()
    before = tree(native)
    fields = ("--checkout-path", str(root), "--default-branch", "main")
    proposed = request("knowledge-register-preview", *fields, name="local-knowledge")
    if proposed.returncode == 0:
        completed = request(
            "knowledge-register-apply", *fields, "--preview-token",
            json.loads(proposed.stdout)["token"], name="local-knowledge",
        )
        assert completed.returncode, "Registration replaced an indirect registry and reported persistence."
    assert proposed.returncode == 1
    assert json.loads(proposed.stdout)["code"] == "indirect-source"
    assert registry.is_symlink() and backing.read_bytes() == before[str(backing.relative_to(native))]
    assert tree(native) == before


@pytest.mark.parametrize("phase", ["before-apply", "before-write"])
def test_local_knowledge_registration_rechecks_registry_indirection(native, monkeypatch, phase):
    from agent_worktrees import guarded_anchors, repos_knowledge_registration as registration

    root = local_knowledge(native)
    token = registration.preview("sample", "local-knowledge", str(root), "main")["token"]
    before = repos._repos_yaml_path().read_bytes()
    if phase == "before-apply":
        backing = symlink_registry(native)
    else:
        original = guarded_anchors.managed_write
        backing = native / "registry-source.yaml"

        def redirected(write, **kwargs):
            symlink_registry(native)
            return original(write, **kwargs)

        monkeypatch.setattr(guarded_anchors, "managed_write", redirected)
    with pytest.raises(ConfigurationError) as failure:
        registration.apply("sample", "local-knowledge", str(root), "main", "", token)
    assert failure.value.code == "indirect-source"
    assert repos._repos_yaml_path().is_symlink() and backing.read_bytes() == before


@pytest.mark.parametrize("invalid", [
    "case-collision", "path-collision", "identity-collision", "descendant", "secret-origin", "relative", "indirect-metadata",
])
def test_local_knowledge_registration_refuses_unsafe_or_registered_inputs_without_effects(native, monkeypatch, invalid):
    from agent_worktrees import repos_knowledge_registration as registration

    root = local_knowledge(native)
    name, path = "local-knowledge", str(root)
    if invalid == "case-collision":
        name = "SAMPLE"
    elif invalid == "path-collision":
        registry = repos._repos_yaml_path()
        raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
        raw["repos"]["existing-knowledge"] = {
            "class": "knowledge", repos._current_platform(): str(root), "default_branch": "main",
        }
        registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    elif invalid == "descendant":
        child = root / "descendant"
        child.mkdir()
        path = str(child)
    elif invalid == "relative":
        path = "relative-knowledge"
    elif invalid == "indirect-metadata":
        original = registration.project_state._is_link_or_reparse
        monkeypatch.setattr(
            registration.project_state, "_is_link_or_reparse",
            lambda selected: selected == root / ".git" or original(selected),
        )
    else:
        remote = (
            "git@github.com:EXAMPLE/SAMPLE.git" if invalid == "identity-collision"
            else f"https://{SECRET}@git.example.test/example/knowledge.git"
        )
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", remote],
            capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    before = tree(native)
    with pytest.raises(ConfigurationError) as error:
        registration.preview("sample", name, path, "main")
    assert SECRET not in str(error.value)
    assert tree(native) == before


def test_local_knowledge_registration_refuses_real_linked_task_and_checkout_shorthand(native):
    from agent_worktrees import repos_knowledge_registration as registration

    root = local_knowledge(native)
    for args in (
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
         "-c", "commit.gpgsign=false", "commit", "--allow-empty", "--quiet", "-m", "fixture"],
        ["worktree", "add", "--quiet", "-b", "task", str(native / "task")],
        ["checkout", "--quiet", "-b", "another"],
        ["checkout", "--quiet", "main"],
    ):
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    before = tree(native)
    with pytest.raises(ConfigurationError, match="anchor"):
        registration.preview("sample", "local-knowledge", str(native / "task"), "main")
    with pytest.raises(ConfigurationError, match="literal default branch"):
        registration.preview("sample", "local-knowledge", str(root), "@{-1}")
    assert tree(native) == before


def test_local_knowledge_registration_uses_one_owned_git_budget_and_scrubbed_context(native, monkeypatch):
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_worktrees import repos_knowledge_registration as registration

    root = local_knowledge(native)
    original = registration.run_caller_owned
    calls = []

    def measured(argv, **kwargs):
        assert "GIT_DIR" not in kwargs["env"] and "GIT_CONFIG_COUNT" not in kwargs["env"]
        calls.append(kwargs["budget"])
        return original(argv, **kwargs)

    monkeypatch.setattr(registration, "run_caller_owned", measured)
    monkeypatch.setenv("GIT_DIR", str(native / "foreign-git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://git.example.test/foreign/repository.git")
    registration.preview("sample", "local-knowledge", str(root), "main")
    assert len(calls) == 3 and all(budget is calls[0] for budget in calls)
    before = tree(native)
    expired = OwnedExecutionBudget(time.monotonic() - 1, 0.1)
    monkeypatch.setattr(registration.OwnedExecutionBudget, "from_timeout", lambda *args, **kwargs: expired)
    with pytest.raises((ConfigurationError, TimeoutError)):
        registration.preview("sample", "local-knowledge", str(root), "main")
    assert tree(native) == before


def test_local_knowledge_registration_waiter_rechecks_the_winning_native_write(native, monkeypatch):
    from agent_worktrees import repos_knowledge_registration as registration

    root = local_knowledge(native)
    token = registration.preview("sample", "local-knowledge", str(root), "main")["token"]
    original_lock = registration.registry_lock
    waiting = threading.Event()
    locked_paths = {
        repos._repos_yaml_path().with_suffix(".lock"),
        repos._repos_yaml_path().parent / "pr-rewrite-authority.lock",
    }

    def content():
        return {
            str(path.relative_to(native)): path.read_bytes() if path.is_file() else None
            for path in native.rglob("*") if path not in locked_paths
        }

    @contextmanager
    def observed_lock(path, **kwargs):
        waiting.set()
        with original_lock(path, **kwargs):
            yield

    monkeypatch.setattr(registration, "registry_lock", observed_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_lock(repos._repos_yaml_path()):
            future = pool.submit(registration.apply, "sample", "local-knowledge", str(root), "main", "", token)
            assert waiting.wait(2)
            assert not future.done()
            preferred = preview("sample", "sample", "set", "concurrent-login")
            apply("sample", "sample", "set", "concurrent-login", preferred["token"])
            before = content()
        with pytest.raises(ConfigurationError) as error:
            future.result(timeout=15)
        assert error.value.code == "preview-conflict"
    assert content() == before


@pytest.mark.parametrize("failure", [None, "before", "after"])
def test_local_knowledge_registration_refreshes_prepared_guards_and_reports_committed_failure(
    native, monkeypatch, capsys, failure,
):
    from agent_worktrees import guarded_anchors, repos_knowledge_registration as registration
    from agent_worktrees.repos_configuration import command

    root, caller, reader = prepared_knowledge_observer(native)
    initial = reader.read_map(caller, repos._repos_yaml_path().parent)
    assert initial.status == "ready"
    assert all(row["name"] != "local-knowledge" for row in initial.postures)
    before_git = {directory: tree(directory) for directory in (caller, root, native / "repository")}
    token = registration.preview("sample", "local-knowledge", str(root), "main")["token"]
    if failure is not None:
        def failed_refresh(*args, **kwargs):
            raise guarded_anchors.GuardPublicationError("synthetic publication failure")

        monkeypatch.setattr(
            guarded_anchors, "_withdraw_views" if failure == "before" else "_refresh_locked", failed_refresh,
        )
    capsys.readouterr()
    code = command([
        "knowledge-register-apply", "--context-project", "sample", "--repository", "local-knowledge",
        "--checkout-path", str(root), "--default-branch", "main", "--preview-token", token,
    ])
    receipt = json.loads(capsys.readouterr().out)
    current = reader.read_map(caller, repos._repos_yaml_path().parent)
    assert ("local-knowledge" in repos.read_registry(strict=True).repos) == (failure != "before")
    if failure == "before":
        assert code == 1 and receipt["code"] == "guard-publication-unavailable"
        assert current.status == "ready"
    elif failure == "after":
        assert code == 1 and receipt["status"] == "incomplete"
        assert receipt["registration_committed"] is True and current.status == "unusable"
    else:
        assert code == 0 and receipt["guard_publication"] == "complete" and current.status == "ready"
        assert any(row["name"] == "local-knowledge" for row in current.postures)
    assert all(tree(directory) == before for directory, before in before_git.items())


@pytest.mark.parametrize("phase", ["validation", "publication"])
def test_local_knowledge_registration_bounds_real_slow_policy_probes_before_and_after_commit(
    native, monkeypatch, phase,
):
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import run_caller_owned
    from agent_worktrees import inrepo_config_source as policy, repos_knowledge_registration as registration

    root, caller, reader = prepared_knowledge_observer(native)
    selected, registry = registration._selection(
        "sample", "local-knowledge", str(root), "main", "",
        OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2),
    )
    token = registration._token(selected)
    if phase == "publication":
        monkeypatch.setattr(registration, "_selection", lambda *args: (selected, registry))
    monkeypatch.setattr(registration, "OwnedExecutionBudget", SimpleNamespace(
        from_timeout=lambda *args, **kwargs: OwnedExecutionBudget.from_timeout(1.4, cleanup_reserve=0.2),
    ))
    original = policy.git_ops.git
    timeouts = []

    def slow_git(*args, **kwargs):
        if args[:2] != ("symbolic-ref", "-q") or not args[2].startswith("refs/remotes/"):
            return original(*args, **kwargs)
        timeout = kwargs["timeout"]
        timeouts.append(timeout)
        if timeout > 1.3:
            raise subprocess.TimeoutExpired("unbounded synthetic policy probe", timeout)
        return run_caller_owned(
            [sys.executable, "-c", "import time; time.sleep(3)"],
            timeout=timeout, max_stdout_bytes=1024, max_stderr_bytes=1024,
        )

    monkeypatch.setattr(policy.git_ops, "git", slow_git)
    started = time.monotonic()
    if phase == "validation":
        with pytest.raises((ConfigurationError, TimeoutError)):
            registration.preview("sample", "local-knowledge", str(root), "main")
        assert "local-knowledge" not in repos.read_registry(strict=True).repos
        assert reader.read_map(caller, repos._repos_yaml_path().parent).status == "ready"
    else:
        result = registration.apply("sample", "local-knowledge", str(root), "main", "", token)
        assert result["status"] == "incomplete" and result["registration_committed"] is True
        assert reader.read_map(caller, repos._repos_yaml_path().parent).status == "unusable"
    assert timeouts and max(timeouts) <= 1.3
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("kind", ["authority", "registry", "publication"])
def test_local_knowledge_registration_contention_respects_aggregate_apply_deadline(native, monkeypatch, kind):
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_worktrees import guarded_anchors, pr_authority, repos_knowledge_registration as registration, tracking

    root = local_knowledge(native)
    selected, registry = registration._selection(
        "sample", "local-knowledge", str(root), "main", "",
        OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2),
    )
    token = registration._token(selected)
    monkeypatch.setattr(registration, "_selection", lambda *args: (selected, registry))
    original_budget = OwnedExecutionBudget.from_timeout
    monkeypatch.setattr(
        registration.OwnedExecutionBudget, "from_timeout",
        lambda *args, **kwargs: original_budget(0.5, cleanup_reserve=0.1),
    )
    if kind == "authority":
        held = pr_authority.guard()
    else:
        path = (
            repos._repos_yaml_path() if kind == "registry"
            else guarded_anchors._prepare_directory(repos._repos_yaml_path().parent) / "publication"
        )
        held = tracking._RecordLock(path, require_sidecar=True)
    before = repos._repos_yaml_path().read_bytes()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with held:
            future = pool.submit(registration.apply, "sample", "local-knowledge", str(root), "main", "", token)
            with pytest.raises(TimeoutError):
                future.result(timeout=2)
            assert future.done(), "the native operation must time out, not merely this observer"
    assert repos._repos_yaml_path().read_bytes() == before


def test_local_knowledge_registration_restores_prepared_views_when_withdrawal_spends_deadline(native, monkeypatch):
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_worktrees import guarded_anchors, repos_knowledge_registration as registration

    root, caller, reader = prepared_knowledge_observer(native)
    selected, registry = registration._selection(
        "sample", "local-knowledge", str(root), "main", "",
        OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2),
    )
    token = registration._token(selected)
    monkeypatch.setattr(registration, "_selection", lambda *args: (selected, registry))
    budgets = []

    def budget(*args, **kwargs):
        value = OwnedExecutionBudget.from_timeout(1.4, cleanup_reserve=0.6)
        budgets.append(value)
        return value

    monkeypatch.setattr(registration, "OwnedExecutionBudget", SimpleNamespace(from_timeout=budget))
    registry_path = repos._repos_yaml_path()
    directory = registry_path.parent / "guarded-anchors"
    map_path = reader.map_path(caller, registry_path.parent)
    before_registry, before_map = registry_path.read_bytes(), map_path.read_bytes()
    before_files = set(directory.iterdir())
    original = guarded_anchors._withdraw_views

    def slow_withdrawal(*args, **kwargs):
        original(*args, **kwargs)
        assert reader.read_map(caller, registry_path.parent).status == "unusable"
        time.sleep(max(0, budgets[-1].execution_deadline - time.monotonic()) + 0.02)

    monkeypatch.setattr(guarded_anchors, "_withdraw_views", slow_withdrawal)
    with pytest.raises(TimeoutError):
        registration.apply("sample", "local-knowledge", str(root), "main", "", token)
    assert time.monotonic() < budgets[-1].deadline
    assert registry_path.read_bytes() == before_registry
    assert reader.read_map(caller, registry_path.parent).status == "ready"
    assert map_path.read_bytes() == before_map and set(directory.iterdir()) == before_files


@pytest.mark.parametrize("change", ["origin", "defaults"])
def test_local_knowledge_registration_rechecks_after_publication_lock_wait(native, monkeypatch, change):
    from agent_worktrees import guarded_anchors, repos_knowledge_registration as registration, tracking

    root, caller, reader = prepared_knowledge_observer(native)
    registry_root = repos._repos_yaml_path().parent
    map_path = reader.map_path(caller, registry_root)
    before_map = map_path.read_bytes()
    assert reader.read_map(caller, registry_root).status == "ready"
    token = registration.preview("sample", "local-knowledge", str(root), "main")["token"]
    inspected = threading.Event()
    original = registration._selection

    def observed(*args):
        selected = original(*args)
        inspected.set()
        return selected

    monkeypatch.setattr(registration, "_selection", observed)
    publication = guarded_anchors._prepare_directory(repos._repos_yaml_path().parent) / "publication"
    before = repos._repos_yaml_path().read_bytes()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tracking._RecordLock(publication, require_sidecar=True):
            future = pool.submit(registration.apply, "sample", "local-knowledge", str(root), "main", "", token)
            assert inspected.wait(5), "the proposal must be inspected before the publication wait"
            if change == "origin":
                result = subprocess.run(
                    ["git", "-C", str(root), "remote", "add", "origin", "https://github.com/example/changed.git"],
                    capture_output=True, text=True, timeout=5, **no_window_kwargs(),
                )
                assert result.returncode == 0, result.stderr
            else:
                path = cfg.global_config_path()
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                raw["default_copilot_account"] = "changed"
                path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            expected_git = tree(root)
        with pytest.raises(ConfigurationError) as failure:
            future.result(timeout=10)
    assert failure.value.code == "preview-conflict"
    assert repos._repos_yaml_path().read_bytes() == before
    assert tree(root) == expected_git
    assert reader.read_map(caller, registry_root).status == "ready"
    assert map_path.read_bytes() == before_map


def test_local_registration_parser_rejects_unrelated_preference_fields_before_inspection(native, capsys):
    from agent_worktrees.repos_configuration import command

    before = tree(native)
    assert command([
        "knowledge-register-preview", "--context-project", "sample", "--repository", "local-knowledge",
        "--checkout-path", str(native / "knowledge"), "--default-branch", "main",
        "--operation", "reset",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-request"
    assert tree(native) == before


def test_missing_binding_owner_refuses_without_any_native_configuration_write(native):
    before = tree(native)
    result = request("knowledge-preview", name="sample")
    assert result.returncode != 0
    assert json.loads(result.stdout)["code"] == "binding-owner-unavailable"
    assert SECRET not in result.stdout + result.stderr
    assert tree(native) == before


@pytest.mark.parametrize("operation", ["identities", "knowledge-resolve", "knowledge-preview"])
def test_real_cli_portable_knowledge_identity_and_owner_refusal(native, operation):
    from agent_worktrees import project_state

    path = repos._repos_yaml_path()
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["repos"]["knowledge"] = {
        "class": "knowledge", repos._current_platform(): str(native / "knowledge"),
        "remote": "https://github.com/example/knowledge.git", "account": "selected-account",
        "default_branch": "main",
    }
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    harness_id = project_state.canonical_repository_id("sample")
    knowledge_id = project_state.canonical_repository_id("knowledge")
    args = ["--context-project", "sample"]
    if operation != "identities":
        args += ["--harness-id", harness_id, "--knowledge-id", knowledge_id]
    before = tree(native)
    child = cli(operation, *args)
    result = json.loads(child.stdout)
    if operation == "knowledge-preview":
        assert child.returncode == 1
        assert result["code"] == "binding-owner-unavailable"
    else:
        assert child.returncode == 0, child.stderr
        if operation == "identities":
            entry = next(entry for entry in result["entries"] if entry["repository"] == "knowledge")
            assert entry["repository_id"] == knowledge_id and entry["account"] == "selected-account"
            assert result["capabilities"]["knowledge_preference"] == 1
        else:
            assert result["harness"]["repository_id"] == harness_id
            assert result["knowledge"]["repository_id"] == knowledge_id
            assert result["status"] == "suggested" and not result["binding_changed"]
    assert SECRET not in child.stdout + child.stderr
    assert tree(native) == before


def test_missing_binding_owner_catalog_exposes_visible_recovery_without_writes(native):
    source = native / "repository" / ".agent-worktrees" / "config.yaml"
    source.parent.mkdir()
    source.write_text("stateless: true\nrequires_external_state_root: true\n", encoding="utf-8")
    before = tree(native)
    result = cli("catalog", "--context-project", "sample")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["summary"]["knowledge_readiness"]["ready"] is False
    assert "Enable and prepare" in value["summary"]["banner_text"]
    assert "harness-knowledge" in value["summary"]["banner_text"]
    assert value["summary"]["banner_level"] == "warn"
    assert all(row["knowledge_bindable"] == "false" for row in value["entries"])
    assert SECRET not in result.stdout + result.stderr
    assert tree(native) == before


@pytest.mark.parametrize("selection", ["project-installed", "global-installed", "sandbox-missing"])
def test_binding_owner_uses_selected_home_and_project_activation(native, monkeypatch, selection):
    from agent_worktrees import repos_knowledge_configuration as binding

    host = native / "host-home"
    project = native / "repository"
    monkeypatch.setenv("HOME", str(host))
    monkeypatch.setenv("USERPROFILE", str(host))
    monkeypatch.setattr(binding.cfg, "load_project_config", lambda *_args, **_kwargs: SimpleNamespace(
        default_repo=SimpleNamespace(anchor=str(project)),
    ))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def payload(root):
        write(root / "plugin.json", {"name": "harness-knowledge"})
        script = root / "skills" / "binding-knowledge" / "scripts" / "bind_knowledge.py"
        script.parent.mkdir(parents=True)
        script.write_text("# Selected payload fixture\n", encoding="utf-8")
        return script.resolve()

    enabled = {"enabledPlugins": {"harness-knowledge@binding-source": True}}
    payload(host / ".copilot" / "installed-plugins" / "binding-source" / "harness-knowledge")
    write(host / ".copilot" / "settings.json", enabled)
    expected = None
    if selection != "sandbox-missing":
        expected = payload(native / ".copilot" / "installed-plugins" / "binding-source" / "harness-knowledge")
        write(native / ".copilot" / "settings.json", enabled)
    if selection == "project-installed":
        market = native / "global-marketplace"
        payload(market / "payload")
        write(market / ".claude-plugin" / "marketplace.json", {
            "name": "binding-source", "plugins": [{"name": "harness-knowledge", "source": "./payload"}],
        })
        write(native / ".copilot" / "settings.json", {**enabled, "extraKnownMarketplaces": {
            "binding-source": {"source": {"source": "directory", "path": str(market)}},
        }})
        write(project / ".github" / "copilot" / "settings.json", {**enabled, "extraKnownMarketplaces": {
            "binding-source": {"source": {"source": "github", "repo": "example/marketplace"}},
        }})
        observed = binding.resolve_active_plugins(
            home=native, project_root=project, plugin_names=("harness-knowledge",),
        ).active["harness-knowledge@binding-source"]
        assert observed.root == (market / "payload").resolve()
        assert observed.root_for_scope("project:current") == expected.parents[3]
    before = tree(native)
    if expected is None:
        assert binding.owner_status("sample") == "unavailable"
    else:
        assert binding.owner_script("sample") == expected
    assert tree(native) == before


@pytest.mark.parametrize("portable", [False, True])
def test_old_binding_owner_is_not_invoked_for_apply(native, monkeypatch, portable):
    from agent_procutil import owned_process
    from agent_worktrees import repos_knowledge_configuration as binding

    calls = []
    monkeypatch.setattr(binding, "owner_script", lambda _project: Path(__file__))

    def execute(argv, **kwargs):
        calls.append(argv)
        capabilities = {"version": 1}
        if portable:
            capabilities["existing_binding_configuration"] = 2
        return subprocess.CompletedProcess(argv, 0, json.dumps(capabilities), "")

    monkeypatch.setattr(owned_process, "run_caller_owned", execute)
    with pytest.raises(ConfigurationError) as caught:
        binding.invoke(
            "sample", "sample", "apply", "a" * 64,
            **({"portable_resolution": {"execution_machine": "example-worker"}} if portable else {}),
        )
    assert caught.value.code == "binding-owner-unsupported"
    assert len(calls) == 1 and "--capabilities" in calls[0]
    assert "--configuration" not in calls[0]


@pytest.mark.parametrize("freshness", [None, True, 0, "1", 1])
@pytest.mark.parametrize("mode", ["baseline", "preview", "accepted-apply"])
def test_accepted_defaults_require_writer_freshness_without_breaking_baseline(
    native, monkeypatch, freshness, mode,
):
    from agent_procutil import owned_process
    from agent_worktrees import repos_knowledge_configuration as binding

    monkeypatch.setattr(binding, "owner_script", lambda _project: Path(__file__))
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if "--capabilities" in argv:
            value = {"version": 1, "existing_binding_configuration": 2, "portable_binding_precondition": 1}
            if freshness is not None:
                value["binding_precondition_freshness"] = freshness
        else:
            value = {
                "version": 1, "operation": "set", "target": {"resource": "sample/sample"},
                "status": "persisted", "pointer_committed": True,
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(owned_process, "run_caller_owned", execute)
    options = (
        {"require_preference_freshness": True} if mode == "preview" else
        {"portable_resolution": {}, "preference_selection": {}} if mode == "accepted-apply" else {}
    )
    operation = "apply" if mode == "accepted-apply" else "preview"
    if mode == "baseline" or type(freshness) is int and freshness == 1:
        assert binding.invoke("sample", "sample", operation, **options)["version"] == 1
        assert len(calls) == 2
    else:
        with pytest.raises(ConfigurationError) as caught:
            binding.invoke("sample", "sample", operation, **options)
        assert caught.value.code == "binding-owner-unsupported"
        assert len(calls) == 1 and "--capabilities" in calls[0]


def test_native_binding_capability_and_worker_share_the_caller_deadline(native, monkeypatch):
    from agent_procutil import owned_process
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_worktrees import repos_knowledge_configuration as binding

    aggregate = OwnedExecutionBudget.from_timeout(4, cleanup_reserve=1)
    budgets = []
    monkeypatch.setattr(binding, "owner_script", lambda _project: Path(__file__))

    def execute(argv, **kwargs):
        budgets.append(kwargs["budget"])
        value = (
            {"version": 1, "existing_binding_configuration": 2} if "--capabilities" in argv else
            {"version": 1, "operation": "set", "target": {"resource": "sample/sample"}}
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(owned_process, "run_caller_owned", execute)
    assert binding.invoke("sample", "sample", "preview", budget=aggregate)["version"] == 1
    assert budgets == [aggregate, aggregate]


def test_binding_apply_keeps_registration_writer_out_until_pointer_commit(native, monkeypatch):
    from agent_worktrees import repos_knowledge_configuration as binding

    started, completed = threading.Event(), threading.Event()

    def write():
        started.set()
        try:
            return repos.set_copilot_account("sample", "after")
        finally:
            completed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = []
        script = native / "qualified-binding.py"
        script.write_text("# Owned binding fixture\n", encoding="utf-8")

        def delegated(_path, *, run_name, init_globals):
            assert run_name == "__main__"
            assert callable(init_globals["_NATIVE_BINDING_READ_RUNNER"])
            futures.append(pool.submit(write))
            assert started.wait(2)
            assert not completed.wait(0.1)
            return {}

        monkeypatch.setattr(binding, "owner_script", lambda _project: script)
        monkeypatch.setattr(binding.runpy, "run_path", delegated)
        binding._apply_worker("sample", "sample", "a" * 64, str(script))
        assert futures[0].result(timeout=5)
    assert repos.read_registry(strict=True).repos["sample"].copilot_account == "after"


def test_binding_worker_refuses_a_changed_payload_before_execution(native, monkeypatch):
    from agent_worktrees import repos_knowledge_configuration as binding

    monkeypatch.setattr(binding, "owner_script", lambda _project: native / "new-owner.py")
    monkeypatch.setattr(binding.runpy, "run_path", lambda *_args, **_kwargs: pytest.fail("changed owner executed"))
    with pytest.raises(ConfigurationError) as caught:
        binding._apply_worker("sample", "knowledge", "a" * 64, str(native / "old-owner.py"))
    assert caught.value.code == "binding-owner-changed"


def test_native_preview_worker_preserves_read_only_scope_without_a_writer_lock(native, monkeypatch):
    from agent_worktrees import repos_knowledge_configuration as binding, repos_registry_transaction

    script = native / "qualified-preview.py"
    script.write_text("# Native preview fixture\n", encoding="utf-8")
    monkeypatch.setattr(binding, "owner_script", lambda _project: script)
    monkeypatch.setattr(
        repos_registry_transaction, "registry_lock",
        lambda *_args, **_kwargs: pytest.fail("preview took a writer lock"),
    )
    previous = sys.argv
    calls = []

    def delegated(path, *, run_name, init_globals):
        assert path == str(script) and run_name == "__main__"
        assert sys.argv[sys.argv.index("--configuration") + 1] == "preview"
        assert "--preview-token" not in sys.argv
        assert callable(init_globals["_NATIVE_BINDING_READ_RUNNER"])
        calls.append(path)

    monkeypatch.setattr(binding.runpy, "run_path", delegated)
    before = tree(native)
    binding._preview_worker("sample", "knowledge", str(script))
    assert calls == [str(script)] and sys.argv is previous
    assert tree(native) == before


def test_binding_worker_aggregate_deadline_includes_probe_and_registry_wait(native, monkeypatch):
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_worktrees import repos_knowledge_configuration as binding
    from agent_worktrees.repos_registry_transaction import registry_lock

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    project = native / "repository"
    config = native / ".sample" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({
        "repo_name": "sample", "repos": {"sample": {"anchor": str(project)}},
    }), encoding="utf-8")
    (native / ".agent-worktrees" / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"sample": {"config_dir": str(config.parent)}},
    }), encoding="utf-8")
    payload = native / ".copilot" / "installed-plugins" / "fixture" / "harness-knowledge"
    write(payload / "plugin.json", {"name": "harness-knowledge"})
    write(native / ".copilot" / "settings.json", {
        "enabledPlugins": {"harness-knowledge@fixture": True},
    })
    script = payload / "skills" / "binding-knowledge" / "scripts" / "bind_knowledge.py"
    script.parent.mkdir(parents=True)
    marker = native / "late-execution"
    script.write_text(
        "import json,sys,time\nfrom pathlib import Path\n"
        "if '--capabilities' in sys.argv:\n"
        " time.sleep(0.5)\n"
        " print(json.dumps({'version':1,'existing_binding_configuration':2}))\n"
        "else:\n"
        f" Path({str(marker)!r}).touch()\n"
        " print(json.dumps({'status':'persisted','pointer_committed':True}))\n",
        encoding="utf-8",
    )
    original = OwnedExecutionBudget.from_timeout
    budgets = []

    def budget(_cls, timeout, *, cleanup_reserve):
        value = original(6 if timeout == 26 else timeout, cleanup_reserve=cleanup_reserve)
        if timeout == 26:
            budgets.append(value)
        return value

    monkeypatch.setattr(OwnedExecutionBudget, "from_timeout", classmethod(budget))
    with registry_lock(repos._repos_yaml_path()):
        with pytest.raises(ConfigurationError) as caught:
            binding.invoke("sample", "knowledge", "apply", "a" * 64)
        assert caught.value.code == "binding-owner-failed"
        assert isinstance(caught.value.__cause__, (subprocess.TimeoutExpired, TimeoutError))
        assert len(budgets) == 1 and time.monotonic() <= budgets[0].deadline + 0.5
    time.sleep(0.2)
    assert not marker.exists()
    assert yaml.safe_load(config.read_text("utf-8")).get("knowledge_repo") is None


def test_incomplete_binding_keeps_committed_receipt_and_reports_failure(native, monkeypatch, capsys):
    from agent_worktrees import repos_configuration, repos_knowledge_configuration

    monkeypatch.setattr(repos_knowledge_configuration, "invoke", lambda *_args: {
        "status": "incomplete", "pointer_committed": True, "plugins_status": "failed",
    })
    assert repos_configuration.command([
        "knowledge-apply", "--context-project", "sample", "--repository", "sample",
        "--preview-token", "a" * 64,
    ]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["pointer_committed"] is True and receipt["status"] == "incomplete"


def test_real_cli_set_reset_inheritance_and_allowlisted_local_writes(native):
    before = tree(native)
    catalog = request("catalog")
    assert catalog.returncode == 0, catalog.stderr
    rows = json.loads(catalog.stdout)["entries"]
    sample = next(row for row in rows if row["id"] == "sample")
    assert sample["effective"] == "inherited"
    assert sample["registration"] == "present"
    assert sample["configuration"]["same_machine_agent"] == "disabled"
    assert sample["configuration"]["pair"]["status"] == "unsupported"
    assert next(row for row in rows if row["id"] == "stale")["registration"] == "stale"
    assert next(row for row in rows if row["id"] == "foreign")["registration"] == "unsupported"
    proposed = request("preview", "--login", "chosen")
    assert proposed.returncode == 0, proposed.stderr
    token = json.loads(proposed.stdout)["token"]
    assert tree(native) == before
    result = request("apply", "--login", "chosen", "--preview-token", token)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == "persisted"
    assert repos.copilot_account_for("sample") == "chosen"
    assert repos.copilot_account_for("sample", project="sample", strict=True) == "chosen"
    after = tree(native)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    assert changed == {
        str(Path(".agent-worktrees") / "repos.yaml"),
        str(Path(".agent-worktrees") / "repos.lock"),
        str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
    }
    raw = yaml.safe_load(repos._repos_yaml_path().read_text(encoding="utf-8"))
    assert raw["unrecognized_owner_metadata"]["sentinel"] == SECRET
    assert raw["repos"]["sample"]["custom_owner_metadata"]["secret"] == SECRET
    source = cfg.global_config_path()
    source.write_text("default_copilot_account: changed-inheritance\n", encoding="utf-8")
    before_reset = tree(native)
    reset = request("preview", "--operation", "reset")
    assert reset.returncode == 0
    assert "changed-inheritance" in reset.stdout
    token = json.loads(reset.stdout)["token"]
    assert tree(native) == before_reset
    result = request("apply", "--operation", "reset", "--preview-token", token)
    assert result.returncode == 0
    assert repos.copilot_account_for("sample") == "changed-inheritance"
    raw = yaml.safe_load(repos._repos_yaml_path().read_text(encoding="utf-8"))
    assert "copilot_account" not in raw["repos"]["sample"]
    after_reset = tree(native)
    assert {
        key for key in before_reset if before_reset[key] != after_reset[key]
    } == {str(Path(".agent-worktrees") / "repos.yaml")}
    assert SECRET not in "".join([
        catalog.stdout, catalog.stderr, proposed.stdout, proposed.stderr, result.stdout, result.stderr,
    ])


def test_secret_shaped_repository_name_remains_browsable_and_editable(native):
    """A registered repository literally named like a secret (e.g. ``sk-ui``)
    is a resource name, not a credential: the secret-token rejection applies
    only to login values, never to repository/project identifiers."""
    registry = repos._repos_yaml_path()
    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    raw["repos"]["sk-ui"] = {
        "class": "worktree", repos._current_platform(): str(native / "repository"),
    }
    registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sk-ui")
    assert row["registration"] == "present"
    proposed = request("preview", "--login", "chosen", name="sk-ui")
    assert proposed.returncode == 0, proposed.stderr
    token = json.loads(proposed.stdout)["token"]
    result = request("apply", "--login", "chosen", "--preview-token", token, name="sk-ui")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert repos.copilot_account_for("sk-ui") == "chosen"


def test_overlong_registered_name_is_an_unsupported_row_not_a_catalog_abort(native, monkeypatch):
    """A registered name longer than the shared character-length cap (native
    `add_repo()` imposes none) must only mark that one row unsupported, never
    abort the whole catalog for the properly named selected project."""
    registry = repos._repos_yaml_path()
    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    overlong = "a" * 65
    raw["repos"][overlong] = {
        "class": "worktree", repos._current_platform(): str(native / "repository"),
    }
    raw["repos"]["knowledge"] = {
        "class": "knowledge", repos._current_platform(): str(native / "knowledge"),
    }
    registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = tree(native)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    rows = {row["id"]: row for row in json.loads(result.stdout)["entries"]}
    assert rows["sample"]["effective"] == "inherited" and rows["sample"]["editable"] == "true"
    assert rows[overlong]["effective"] == "unknown" and rows[overlong]["editable"] == "false"
    assert rows[overlong]["configuration"]["findings"]
    from agent_worktrees import repos_configuration_catalog as catalog, repos_knowledge_configuration as binding

    monkeypatch.setattr(catalog, "inspect_project", lambda *_args: catalog.Inspection(
        binding={"identity": ""}, readiness={"ready": False},
    ))
    monkeypatch.setattr(binding, "owner_status", lambda _project: "available")
    rows = {row["id"]: row for row in catalog.catalog("sample")["entries"]}
    assert rows["knowledge"]["knowledge_bindable"] == "true"
    assert rows[overlong]["knowledge_bindable"] == "false"
    assert tree(native) == before


@pytest.mark.parametrize("project,name", [
    ("sample", "_knowledge"), ("sample", ".knowledge"),
    ("sample", "-knowledge"), ("_project", "knowledge"),
])
def test_binding_name_rules_match_catalog_and_preview_without_blocking_preferences(native, monkeypatch, project, name):
    from agent_worktrees import repos_configuration_catalog as catalog, repos_knowledge_configuration as binding

    registry = repos._repos_yaml_path()
    raw = yaml.safe_load(registry.read_text("utf-8"))
    raw["repos"][project] = {"class": "worktree", repos._current_platform(): str(native / "repository")}
    for repository in (name, "sk-knowledge"):
        raw["repos"][repository] = {"class": "knowledge", repos._current_platform(): str(native / "knowledge")}
    registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = tree(native)
    monkeypatch.setattr(catalog, "inspect_project", lambda *_args: catalog.Inspection(
        binding={"identity": ""}, readiness={"ready": False, "code": "knowledge_binding_required"},
    ))
    monkeypatch.setattr(binding, "owner_status", lambda _project: "available")
    rows = {row["id"]: row for row in catalog.catalog(project)["entries"]}
    assert rows[name]["editable"] == "true"
    assert rows[name]["knowledge_bindable"] == "false"
    assert "Binding identifiers: unsupported" in rows[name]["card"]["body"]
    assert rows["sk-knowledge"]["knowledge_bindable"] == ("true" if project == "sample" else "false")
    assert any(row.get("knowledge_registration_available") == "true" for row in rows.values()) == (project == "sample")
    result = cli("knowledge-preview", f"--context-project={project}", f"--repository={name}")
    assert result.returncode != 0
    assert json.loads(result.stdout)["code"] == "binding-name-unsupported"
    assert SECRET not in result.stdout + result.stderr
    assert tree(native) == before


@pytest.mark.parametrize("change", ["registry", "global", "machine", "drop-in", "target", "login"])
def test_stale_previews_and_old_host_refuse_writes(native, change):
    proposed = preview("sample", "sample", "set", "chosen")
    name, login = "sample", "chosen"
    if change == "registry":
        # An ordinary real CLI mutation shares the very same native registry fence.
        result = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", "repos", "account", "set", "example", "other"],
            capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    elif change == "global":
        cfg.global_config_path().write_text("default_copilot_account: other\n", encoding="utf-8")
    elif change == "machine":
        cfg.project_dir("sample").mkdir()
        (cfg.project_dir("sample") / "config.yaml").write_text(
            "default_copilot_account: other\n", encoding="utf-8",
        )
    elif change == "drop-in":
        directory = cfg.project_dir("sample") / "config.d"
        directory.mkdir(parents=True)
        (directory / "local.yaml").write_text("default_copilot_account: other\n", encoding="utf-8")
    elif change == "target":
        name = "stale"
    else:
        login = "different"
    before = tree(native)
    for token in ("", "forged", proposed["token"]):
        result = request("apply", "--login", login, "--preview-token", token, name=name)
        assert result.returncode != 0, result.stdout
        assert tree(native) == before


def test_competing_real_cli_applies_have_one_winner(native):
    token = preview("sample", "sample", "set", "chosen")["token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: request("apply", "--login", "chosen", "--preview-token", token), range(2),
        ))
    assert sorted(result.returncode for result in results) == [0, 1]
    assert "preview-conflict" in next(result.stdout for result in results if result.returncode)


@pytest.mark.parametrize("content", [
    "[broken", "default_copilot_account: [not-a-login]\n",
    f"default_copilot_account: {SECRET}\n",
    'default_copilot_account: first\ndefault_copilot_account: ""\n',
    "copilot_identity_switch_enabled: not-a-boolean\n",
    f"? [{SECRET}]\n: invalid-key\n",
    f"? {{{SECRET}: value}}\n: invalid-key\n",
])
@pytest.mark.parametrize("source", ["global", "drop-in"])
def test_invalid_composition_is_unknown_not_empty_identity_and_sanitized(native, content, source):
    path = cfg.global_config_path()
    if source == "drop-in":
        directory = cfg.project_dir("sample") / "config.d"
        directory.mkdir(parents=True)
        path = directory / "local.yaml"
    path.write_text(content, encoding="utf-8")
    before = tree(native)
    catalog = request("catalog")
    assert catalog.returncode == 0, catalog.stderr
    row = next(row for row in json.loads(catalog.stdout)["entries"] if row["id"] == "sample")
    assert row["effective"] == "unknown" and row["editable"] == "false"
    assert row["configuration"]["findings"]
    proposed = request("preview", "--login", "chosen")
    assert proposed.returncode != 0
    assert SECRET not in catalog.stdout + catalog.stderr + proposed.stdout + proposed.stderr
    assert tree(native) == before
    with pytest.raises(ConfigurationError):
        repos.copilot_account_for("sample", project="sample", strict=True)


def test_absent_registration_and_fresh_root_are_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "first-touch"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["entries"][0]
    assert row["registration"] == "absent" and row["editable"] == "false"
    assert not (tmp_path / "first-touch").exists()


def test_malformed_projects_registry_is_sanitized_native_inspection(native):
    from agent_worktrees import installer

    installer.projects_yaml_path().write_text(f"projects: [{SECRET}\n", encoding="utf-8")
    before = tree(native)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    assert row["configuration"]["binding"]["status"] == "unknown"
    assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert tree(native) == before


@pytest.mark.parametrize("command", ["catalog", "knowledge-preview"])
def test_missing_default_repository_is_sanitized_and_read_only(native, command):
    from agent_worktrees import installer

    project = "multi"
    config = native / ".multi" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({
        "repo_name": project, "repos": {
            "one": {"anchor": str(native / "repository")},
            "two": {"anchor": str(native / "knowledge")},
        },
    }), encoding="utf-8")
    installer.projects_yaml_path().write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {project: {"config_dir": str(config.parent)}},
    }), encoding="utf-8")
    before = tree(native)
    with pytest.raises(KeyError):
        cfg.load_project_config(project, include_control_plane_related_pr=False).default_repo
    args = [command, "--context-project", project]
    if command == "knowledge-preview":
        args += ["--repository", "sample"]
    result = cli(*args)
    value = json.loads(result.stdout)
    if command == "catalog":
        assert result.returncode == 0, result.stderr
        assert value["summary"]["binding_owner"] == "unavailable"
        assert all(row["knowledge_bindable"] == "false" for row in value["entries"])
        row = next(row for row in value["entries"] if row["id"] == project)
        assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    else:
        assert result.returncode != 0
        assert value["code"] == "binding-owner-unavailable"
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert tree(native) == before


def test_disabled_managed_dropin_does_not_block_valid_preferences(native):
    source = "sample@example-marketplace"
    root = native / "plugin"
    target = root / "config.yaml"
    target.write_text("default_copilot_account: disabled-value\n", encoding="utf-8")
    settings = native / ".copilot" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"enabledPlugins": {source: False}}), encoding="utf-8")
    directory = cfg.project_dir("sample") / "config.d"
    directory.mkdir(parents=True)
    (directory / "managed.json").write_text(json.dumps({
        "schema_version": 1, "plugin": source, "plugin_root": str(root), "target": str(target),
    }), encoding="utf-8")
    before = tree(native)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    assert row["effective"] == "inherited" and row["editable"] == "true"
    proposed = request("preview", "--login", "chosen")
    assert proposed.returncode == 0, proposed.stderr
    assert tree(native) == before
    result = request("apply", "--login", "chosen", "--preview-token", json.loads(proposed.stdout)["token"])
    assert result.returncode == 0, result.stderr
    reset = request("preview", "--operation", "reset")
    assert reset.returncode == 0, reset.stderr
    result = request("apply", "--operation", "reset", "--preview-token", json.loads(reset.stdout)["token"])
    assert result.returncode == 0, result.stderr
    assert repos.copilot_account_for("sample", project="sample", strict=True) == "inherited"
    assert target.read_text(encoding="utf-8") == "default_copilot_account: disabled-value\n"


@pytest.mark.parametrize("location", ["worktree", "anchor", "outside", "foreign"])
def test_picker_bootstrap_workspace_is_a_tracked_selected_project_root(native, monkeypatch, capfd, location):
    from agent_worktrees import context_cli, tracking

    monkeypatch.setattr(context_cli._core(), "_INVOCATION_CWD", None)
    workspace = native / "sample-worktree"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    record = tracking.WorktreeRecord(
        worktree_id="wt-sample", branch="worktree/wt-sample", worktree_path=str(workspace),
        repo="foreign" if location == "foreign" else "sample",
        machine="fixture", platform=repos._current_platform(),
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None, sessions=[],
    )
    tracking.save_record(record, cfg.project_dir("sample") / "worktrees" / "wt-sample.yaml")
    monkeypatch.chdir(
        native / "repository" if location == "anchor" else native if location == "outside" else nested,
    )
    before = tree(native)
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["project"] == "sample"
    assert payload["workspace"] == (str(workspace) if location == "worktree" else None)
    assert payload["cwd"] != str(workspace)
    assert tree(native) == before


def test_request_errors_never_echo_raw_values(native):
    result = cli("preview", "--context-project", "sample", "--operation", SECRET)
    assert result.returncode != 0 and SECRET not in result.stdout + result.stderr
    with pytest.raises(ConfigurationError):
        apply("sample", "sample", "set", SECRET, "")


@pytest.fixture(params=["base", "marketplace"])
def route_policy(native, monkeypatch, request):
    from agent_worktrees import project_state, registry_paths

    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    if request.param == "marketplace":
        payload = Path(__file__).resolve().parents[1]
        helper = registry_paths._helper()._load_context_helper(payload)
        cell = helper.stamp_context(
            payload_root=payload, plugin_id="agent-worktrees",
            payload_version=json.loads((payload / "plugin.json").read_text(encoding="utf-8"))["version"],
            payload_origin="explicit", expected_namespace_generation=0,
            expected_install_generation=0, durable_home=native / "durable",
            source_descriptor={"source": "github", "repo": "example/marketplace"},
            marketplace_key="fixture", environment={},
        )
        for name in ("repos.yaml", "config.yaml"):
            (Path(cell["pluginRoot"]) / name).write_bytes((native / ".agent-worktrees" / name).read_bytes())
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", cell["installReceipt"])
        monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
        monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
        project_state.ensure_project_state("sample")
        policy.mkdir(parents=True)
        (policy / "related.yaml").write_text("related:\n  external:\n", encoding="utf-8")
        policy = policy / "marketplaces" / cell["marketplaceId"]
    policy.mkdir(parents=True)
    return policy / "related.yaml"


@pytest.mark.parametrize("route", [
    "related: [broken", "related:\n  external: 42\n",
    "related:\n  external:\n  external:\n",
    "related:\n  external: {role: 42}\n",
    "related:\n  external: {delegate: {via: 42}}\n",
])
def test_invalid_native_route_inspection_stays_unknown_not_valid_empty(native, route_policy, route):
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.write_text(route, encoding="utf-8")
    before = tree(native)
    state = next(row["configuration"] for row in catalog("sample")["entries"] if row["id"] == "sample")
    assert state["route"]["status"] == "unknown"
    assert state["findings"][0]["code"] == "native-inspection-unavailable"
    assert tree(native) == before


@pytest.mark.parametrize("entry", [
    {"locus": {"preferred": {"secret": SECRET}}},
    {"locus": {"preferred": [SECRET]}},
    {"locus": {"preferred": None}},
    {"locus": {"preferred": SECRET}},
    {"locus": {"preferred": f"machine:{SECRET}"}},
    {"locus": [SECRET]},
    {"delegate": {"via": {"secret": SECRET}}},
    {"delegate": {"via": SECRET}},
])
def test_route_projection_never_stringifies_or_exposes_invalid_source_values(native, route_policy, entry):
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.write_text(yaml.safe_dump({"related": {"external": entry}}), encoding="utf-8")
    before = tree(native)
    result = catalog("sample")
    state = next(row["configuration"] for row in result["entries"] if row["id"] == "sample")
    assert state["route"]["status"] == "unknown"
    assert state["findings"][0]["code"] == "native-inspection-unavailable"
    assert SECRET not in json.dumps(result)
    assert tree(native) == before


@pytest.mark.parametrize("entry", [
    None, 42, SECRET, {"base_repo": "false"}, {"base_repo": {"secret": SECRET}},
])
def test_invalid_project_entry_with_matching_route_is_a_sanitized_finding(native, route_policy, entry):
    from agent_worktrees import installer
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.write_text("related:\n  external:\n", encoding="utf-8")
    installer.projects_yaml_path().write_text(
        yaml.safe_dump({"projects": {"external": entry}}), encoding="utf-8",
    )
    before = tree(native)
    result = catalog("sample")
    assert result["entries"]
    for row in result["entries"]:
        assert row["configuration"]["route"]["status"] == "unknown"
        assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert SECRET not in json.dumps(result)
    assert tree(native) == before


def test_native_minimal_related_link_is_valid(native, route_policy):
    from agent_worktrees.repos_configuration_catalog import catalog
    from agent_worktrees.repos_configuration_sources import defaults

    route_policy.write_text("related:\n  external:\n", encoding="utf-8")
    assert defaults("sample").login == "inherited"
    before = tree(native)
    rows = {row["id"]: row for row in catalog("sample")["entries"]}
    assert not rows["sample"]["configuration"]["findings"]
    assert rows["external"]["participation"] == "related"
    assert rows["external"]["configuration"]["route"]["status"] == "present"
    assert tree(native) == before


@pytest.mark.parametrize("content", ["[broken", "stateless: true\nstateless: false\n", "42\n"])
def test_invalid_selected_repository_configuration_is_unknown(native, route_policy, content):
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.with_name("config.yaml").write_text(content, encoding="utf-8")
    before = tree(native)
    row = next(row for row in catalog("sample")["entries"] if row["id"] == "sample")
    assert row["configuration"]["binding"]["status"] == "unknown"
    assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert tree(native) == before


def test_machine_route_keeps_the_resolved_peer(native):
    from agent_worktrees.repos_configuration_catalog import catalog

    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    (policy / "related.yaml").write_text(
        "related:\n  external: {locus: {preferred: 'machine:peer-one'}}\n", encoding="utf-8",
    )
    row = next(row for row in catalog("sample")["entries"] if row["id"] == "external")
    assert row["configuration"]["route"]["preferred"] == "machine"
    assert row["configuration"]["route"]["target_machine"] == "peer-one"


def test_invalid_committed_configuration_cannot_hide_behind_valid_disk_copy(native):
    from agent_worktrees import git_ops
    from agent_worktrees.repos_configuration_catalog import catalog

    root = native / "repository"

    def git(*args):
        return git_ops.git(*args, cwd=root, capture=True, timeout=15)

    git("init", "-q", "-b", "main")
    git("config", "core.hooksPath", str(root / "no-hooks"))
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    policy = root / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    source = policy / "config.yaml"
    source.write_text("[broken", encoding="utf-8")
    git("add", ".")
    git("-c", "commit.gpgSign=false", "commit", "-qm", "fixture")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    source.write_text("stateless: true\n", encoding="utf-8")
    before = tree(native)
    row = next(row for row in catalog("sample")["entries"] if row["id"] == "sample")
    assert row["configuration"]["binding"]["status"] == "unknown"
    assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert tree(native) == before


def test_default_dropin_and_machine_precedence(native):
    directory = cfg.project_dir("sample") / "config.d"
    directory.mkdir(parents=True)
    (directory / "local.yaml").write_text("default_copilot_account: dropin\n", encoding="utf-8")
    source = directory.parent / "config.yaml"
    source.write_text("default_copilot_account: machine\ncopilot_identity_switch_enabled: true\n", encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    state = row["configuration"]
    assert state["effective"] == "machine" and state["switch_enabled"] is True
    assert [source["value"] for source in state["sources"]] == ["inherited", "dropin", "machine"]
    assert [source["wins"] for source in state["sources"]] == [False, False, True]
    assert repos.copilot_account_for("sample", project="sample", strict=True) == "machine"


def test_catalog_source_winners_are_independent_per_repository(native):
    path = repos._repos_yaml_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["repos"]["sample"]["copilot_account"] = "chosen"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    rows = {row["id"]: row["configuration"] for row in json.loads(result.stdout)["entries"]}
    overridden = rows["sample"]["sources"]
    inherited = rows["stale"]["sources"]
    assert [source["value"] for source in overridden] == ["inherited", "chosen"]
    assert [source["wins"] for source in overridden] == [False, True]
    assert [source["masked"] for source in overridden] == [True, False]
    assert [source["wins"] for source in inherited] == [True]
    assert [source["masked"] for source in inherited] == [False]


@pytest.mark.parametrize("operation", ["set", "reset"])
def test_preference_edit_does_not_modify_aliased_unselected_data(native, operation):
    path = repos._repos_yaml_path()
    path.write_text(
        "metadata: &shared\n  copilot_account: existing\n  custom: unchanged\n"
        "repos: &registrations\n  sample: *shared\n  other: *shared\n"
        "registry_metadata: *registrations\n",
        encoding="utf-8",
    )
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    login = "chosen" if operation == "set" else ""
    token = preview("sample", "sample", operation, login)["token"]
    apply("sample", "sample", operation, login, token)
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["metadata"] == before["metadata"]
    assert after["registry_metadata"] == before["registry_metadata"]
    assert after["repos"]["other"] == before["repos"]["other"]
    assert after["repos"]["sample"]["custom"] == "unchanged"
    assert after["repos"]["sample"].get("copilot_account", "") == login


@pytest.mark.parametrize("condition", [
    "present", "missing-sample", "missing-knowledge", "retired", "stale", "absent",
])
def test_native_routes_binding_and_real_tracked_pair_present_stale_absent(native, condition):
    from agent_worktrees import tracking

    registry_path = repos._repos_yaml_path()
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    raw["repos"]["knowledge"] = {
        "class": "knowledge", repos._current_platform(): str(native / "knowledge"),
    }
    registry_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    (policy / "config.yaml").write_text("stateless: true\n", encoding="utf-8")
    (policy / "related.yaml").write_text(yaml.safe_dump({
        "related": {
            "external": {
                "role": "product", "locus": {"preferred": "codespace"},
                "delegate": {"via": "agent-codespaces"}, "secret": SECRET,
            },
        },
    }), encoding="utf-8")
    machine = cfg.project_dir("sample")
    machine.mkdir()
    (machine / "config.yaml").write_text("knowledge_repo: knowledge\n", encoding="utf-8")
    paths = {name: native / f"{name}-worktree" for name in ("sample", "knowledge")}
    records = {}
    for name, role, sibling in (("sample", "harness", "knowledge"), ("knowledge", "knowledge", "sample")):
        paths[name].mkdir()
        record = tracking.WorktreeRecord(
            worktree_id=f"wt-{name}", branch=f"worktree/wt-{name}", worktree_path=str(paths[name]),
            repo=name, machine="fixture", platform=repos._current_platform(),
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None, sessions=[],
            pair_id="fixture-pair", pair_role=role, pair_ref=f"fixture/{sibling}/wt-{sibling}",
            pair_kind="worktree",
        )
        path = cfg.project_dir(name) / "worktrees" / f"wt-{name}.yaml"
        tracking.save_record(record, path)
        records[name] = path

    if condition.startswith("missing-"):
        paths[condition.removeprefix("missing-")].rmdir()
    elif condition == "retired":
        retired = tracking.load_record(records["knowledge"])
        retired.status = "finalized"
        tracking.save_record(retired, records["knowledge"])
    elif condition in {"stale", "absent"}:
        records["knowledge"].unlink()
        if condition == "absent":
            records["sample"].unlink()
    before = tree(native)
    result = request("catalog", "--workspace", str(paths["sample"]))
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)["entries"]
    row = next(row for row in rows if row["id"] == "sample")
    state = row["configuration"]
    assert not state["findings"], state["findings"]
    assert state["binding"]["identity"] == "knowledge"
    assert state["binding"]["anchor"] == str(native / "knowledge")
    assert state["binding"]["writable_workspace"] is False
    expected = "present" if condition in {"present", "retired"} else "absent" if condition == "absent" else "stale"
    assert state["pair"]["status"] == expected
    assert state["pair"]["writable_workspace"] is (condition == "present")
    if condition == "present":
        assert state["pair"]["workspace"] == str(paths["knowledge"])
    elif condition.startswith("missing-"):
        assert state["pair"]["workspace"] != str(native / "knowledge")
    elif condition == "retired":
        assert state["pair"]["workspace_status"] == "finalized"
    route = next(row for row in rows if row["id"] == "external")
    assert route["registration"] == "absent" and route["participation"] == "related"
    assert route["configuration"]["route"]["preferred"] == "codespace"
    assert route["configuration"]["route"]["editable"] is False
    assert SECRET not in result.stdout + result.stderr
    assert tree(native) == before


@pytest.mark.parametrize("mode", ["empty", "populated", "bytes-only"])
def test_mapping_report_replacement_is_opaque_without_reacquisition(tmp_path, monkeypatch, mode):
    from agent_worktrees import repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    path.write_text("marker: disk\n", encoding="utf-8")
    value = {} if mode == "empty" else {"marker": "available"}
    calls = []
    original_read, original_exists = Path.read_bytes, Path.exists

    def read(selected):
        if selected == path:
            calls.append("native-read")
        return original_read(selected)

    def exists(selected):
        if selected == path:
            pytest.fail("report added a presence probe")
        return original_exists(selected)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "exists", exists)

    def replacement(selected):
        assert selected == path
        calls.append("mapping")
        if mode == "bytes-only":
            sources.read_bytes(selected)
            calls.append("original-bytes")
        return value

    monkeypatch.setattr(sources, "mapping", replacement)
    monkeypatch.setattr(sources, "parse_mapping", lambda *a: pytest.fail("report replayed the parser"))
    report = sources.mapping_report(path)
    assert report.value is value
    assert calls == (["mapping", "native-read", "original-bytes"] if mode == "bytes-only" else ["mapping"])
    assert report.authority.value == "indeterminate"
    assert any(f.reason == "owner-coverage-unavailable" for f in report.findings)
