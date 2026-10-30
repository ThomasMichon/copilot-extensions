"""The ordinary registry writer and Configuration share one conflict fence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import subprocess
import sys
import threading

from agent_procutil import no_window_kwargs
import pytest
import yaml

from agent_worktrees import repos
from agent_worktrees.repos_registry_transaction import RegistryConflictError, registry_lock


@pytest.fixture
def registry_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    repos.set_srcroot(str(tmp_path / "src"))
    return tmp_path


def test_stale_ordinary_writer_cannot_clobber_preference(registry_home):
    before = repos.read_registry()
    repos.set_account_map("example", "new-login")
    current = repos._repos_yaml_path().read_bytes()
    before.srcroot["linux"] = "/stale"
    with pytest.raises(RegistryConflictError, match="changed"):
        repos.write_registry(before)
    assert repos._repos_yaml_path().read_bytes() == current


def test_writer_refreshes_own_baseline(registry_home):
    registry = repos.read_registry()
    for value in ("first", "second"):
        registry.account_map["example"] = value
        repos.write_registry(registry)
    assert repos.read_registry().account_map["example"] == "second"


@pytest.mark.parametrize("populated", (False, True))
def test_ordinary_write_retains_loaded_schema_stamp_and_existing_data(registry_home, populated):
    path = repos._repos_yaml_path()
    initial = {
        "schema_version": 1,
        "srcroot": {"linux": "/existing/source"},
        "account_map": {"example": "existing-login"},
        "repos": {
            "sample": {"class": "reference", "agent": False, "linux": "/existing/sample"},
        } if populated else {},
    }
    path.write_text(yaml.safe_dump(initial), encoding="utf-8")
    registry = repos.read_registry(strict=True)
    assert registry.schema_version == 1
    for _ in range(2):
        repos.write_registry(registry)
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == initial
        registry = repos.read_registry(strict=True)


@pytest.mark.parametrize("version", (None, True, "1", 0, -1))
def test_invalid_schema_stamp_never_becomes_an_ordinary_write(registry_home, version):
    path = repos._repos_yaml_path()
    path.write_text(yaml.safe_dump({"schema_version": version, "repos": {}}), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(repos.RepositoryResolutionError, match="schema version"):
        repos.read_registry(strict=True)
    if version is not None:
        registry = repos.ReposRegistry(schema_version=version)
        with pytest.raises(ValueError, match="schema version"):
            repos.write_registry(registry)
    assert path.read_bytes() == original


def test_registry_writes_preserve_publication_authority_guard(registry_home, monkeypatch):
    from agent_worktrees import pr_authority, tracking

    entered = []
    original = tracking._atomic_write

    @contextmanager
    def guard():
        entered.append(True)
        try:
            yield
        finally:
            entered.pop()

    def checked_write(path, content):
        assert entered
        return original(path, content)

    monkeypatch.setattr(pr_authority, "guard", guard)
    monkeypatch.setattr(tracking, "_atomic_write", checked_write)
    repos.set_account_map("example", "guarded")
    assert not entered
    assert repos.read_registry().account_map["example"] == "guarded"


def test_unread_or_invalid_registry_cannot_replace_existing(registry_home):
    path = repos._repos_yaml_path()
    with pytest.raises(RegistryConflictError, match="read before"):
        repos.write_registry(repos.ReposRegistry())
    path.write_text("repos: [broken", encoding="utf-8")
    invalid = repos.read_registry()
    with pytest.raises(RegistryConflictError):
        repos.write_registry(invalid)
    assert path.read_text(encoding="utf-8") == "repos: [broken"


def test_changed_destination_requires_fresh_read(registry_home, monkeypatch):
    registry = repos.read_registry()
    monkeypatch.setenv("AGENT_HOME", str(registry_home / "different"))
    with pytest.raises(RegistryConflictError):
        repos.write_registry(registry)
    assert not repos._repos_yaml_path().exists()


def test_two_threads_have_exactly_one_winner(registry_home):
    barrier = threading.Barrier(2)

    def mutate(login):
        registry = repos.read_registry(strict=True)
        registry.account_map["example"] = login
        barrier.wait(timeout=5)
        try:
            repos.write_registry(registry)
            return "written"
        except RegistryConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, ("first", "second"))) == ["conflict", "written"]


def test_lock_covers_real_process_ordinary_writer(registry_home):
    path = repos._repos_yaml_path()
    script = (
        "from agent_worktrees import repos; "
        "repos.set_account_map('example', 'child-login')"
    )
    child = None
    try:
        with registry_lock(path):
            child = subprocess.Popen(
                [sys.executable, "-c", script], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
            )
            # The child cannot commit while the same native lock is held.
            with pytest.raises(subprocess.TimeoutExpired):
                child.communicate(timeout=0.3)
            assert "example" not in repos.read_registry().account_map
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, (stdout, stderr)
        assert repos.read_registry().account_map["example"] == "child-login"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_read_only_absent_registry_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "fresh"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    assert not repos.read_registry(strict=True).repos
    assert not (tmp_path / "fresh").exists()


@pytest.mark.parametrize("mode", ["absent", "empty-map", "partial", "malformed", "encoding"])
def test_registry_report_preserves_original_acquisition(tmp_path, monkeypatch, mode):
    """Specify provider coverage without changing permissive registry values."""
    from pathlib import Path
    from agent_worktrees import config_migrations, repos_registry_reader as reader, repos_registry_transaction as transaction

    path = tmp_path / "repos.yaml"
    bodies = {"empty-map": b"{}\n", "partial": b"repos:\n  good: {default_branch: dev}\n  bad: []\n",
              "malformed": b"repos: [\n", "encoding": b"\xff"}
    if mode != "absent":
        path.write_bytes(bodies[mode])
    original_read, original_revision, original_migrate = Path.read_bytes, transaction.revision, config_migrations.migrate_doc
    calls = []

    def read(selected):
        if selected == path:
            calls.append("read")
        return original_read(selected)

    def revision(content):
        calls.append("revision")
        return original_revision(content)

    def migrate(*args):
        calls.append("migration")
        return original_migrate(*args)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(transaction, "revision", revision)
    monkeypatch.setattr(config_migrations, "migrate_doc", migrate)
    legacy = reader.read_registry(path)
    expected_calls = list(calls)
    calls.clear()
    report = reader.read_registry_report(path)
    assert report.value == legacy
    assert report.value._baseline == legacy._baseline
    assert calls == expected_calls
    assert calls.count("read") == calls.count("revision") == 1
    assert calls.count("migration") == (1 if mode in ("empty-map", "partial") else 0)
    assert report.authority.value == ("absent" if mode == "absent" else "complete" if mode == "empty-map" else "indeterminate")
    if mode == "partial":
        assert report.value.repos["good"].default_branch == "dev"
        assert "bad" not in report.value.repos
    assert report.reusable is False


@pytest.mark.parametrize("native", [False, True])
def test_registry_report_preserves_strict_exception_identity_and_cause(tmp_path, monkeypatch, native):
    from pathlib import Path
    from agent_worktrees import repos_registry_reader as reader

    path = tmp_path / "repos.yaml"
    path.write_text("{}\n", encoding="utf-8")
    failure = PermissionError("synthetic private read failure") if native else reader.RepositoryResolutionError("synthetic strict rejection")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, "read_bytes", fail) if native else monkeypatch.setattr(reader.yaml, "load", fail)
    for load in (reader.read_registry, lambda selected, **kw: reader.read_registry_report(selected, **kw)):
        with pytest.raises(reader.RepositoryResolutionError) as caught:
            load(path, strict=True)
        assert (caught.value.__cause__ is failure) if native else (caught.value is failure)


@pytest.mark.parametrize("mode", ["unavailable", "error", "success"])
def test_migration_outcome_observes_original_branch_and_identity(monkeypatch, mode):
    from agent_worktrees import config_acquisition as acquisition, config_migrations

    original = {"private_setting": "private-value"}
    migrated = {"schema_version": 1}
    calls = []

    def migrate(doc, schema_id, registry):
        calls.append(doc)
        if mode == "error":
            raise ValueError("synthetic private migration failure")
        return migrated, True

    monkeypatch.setattr(config_migrations, "_AVAILABLE", mode != "unavailable")
    monkeypatch.setattr(config_migrations, "migrate_doc", migrate)
    with acquisition.capture_config_observations() as observations:
        value = config_migrations.migrate_loaded(original, config_migrations.SCHEMA_REPOS)
    assert value is (migrated if mode == "success" else original)
    assert calls == ([] if mode == "unavailable" else [original])
    assert any(o.stage == "config-migration" and o.authority.value == ("complete" if mode == "success" else "indeterminate")
               for o in observations)
    assert all(text not in repr(observations) for text in ("private_setting", "private-value", "synthetic private migration failure"))


def test_registry_report_retains_strict_precheck_uncertainty(tmp_path):
    from agent_worktrees import repos_registry_reader as reader

    report = reader.read_registry_report(tmp_path / "missing.yaml", strict=True)
    assert not report.value.repos
    assert report.value._baseline[1] == "absent"
    assert report.authority.value == "indeterminate"


@pytest.mark.parametrize("mode", ["empty", "populated", "partial"])
def test_registry_report_replacement_is_opaque_without_reacquisition(tmp_path, monkeypatch, mode):
    from pathlib import Path
    from agent_worktrees import config_migrations, repos_registry_reader as reader, repos_registry_transaction

    path = tmp_path / "repos.yaml"
    path.write_text("{}\n", encoding="utf-8")
    value = repos.ReposRegistry() if mode == "empty" else repos.ReposRegistry(repos={
        "sample": repos.RepoEntry(name="sample", default_branch="main"),
    })
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

    def replacement(selected, *, strict=False):
        assert selected == path and strict is False
        calls.append("registry")
        if mode == "partial":
            repos_registry_transaction.read_bytes(selected)
            config_migrations.migrate_loaded({}, config_migrations.SCHEMA_REPOS)
            calls.append("original-bytes-and-migration")
        return value

    monkeypatch.setattr(reader, "read_registry", replacement)
    report = reader.read_registry_report(path)
    assert report.value is value
    assert calls == (["registry", "native-read", "original-bytes-and-migration"] if mode == "partial" else ["registry"])
    assert report.authority.value == "indeterminate"
    assert any(f.reason == "owner-coverage-unavailable" for f in report.findings)
