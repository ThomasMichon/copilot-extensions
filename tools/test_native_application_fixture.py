"""Source-only controls for the opt-in native fixture's input custody."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_owner_preserves_only_its_runner_owned_daemon_context():
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

    fixture = ROOT / "plugins/agent-worktrees/tests/test_source_native_owner.py"
    tree = ast.parse(fixture.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    assignment = next(
        node for node in function.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "environment" for target in node.targets)
    )
    incoming = {
        TEST_CONTEXT_ENV: "runner-owned-lifecycle",
        "COPILOT_EXTENSIONS_FOREIGN": "other-context",
        "AGENT_WORKTREES_ROOT": "other-runtime",
        "GIT_DIR": "other-repository",
        "PYTHONPATH": "other-python",
        "NORMAL_VALUE": "retained",
    }
    observed = eval(compile(ast.Expression(assignment.value), str(fixture), "eval"), {
        "os": SimpleNamespace(environ=incoming), "TEST_CONTEXT_ENV": TEST_CONTEXT_ENV,
    })
    assert observed == {
        TEST_CONTEXT_ENV: "runner-owned-lifecycle", "NORMAL_VALUE": "retained",
    }


@pytest.fixture
def fixtures(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "plugins" / "agent-worktrees" / "tests"))
    import runtime_install_fixture
    import source_application_fixture

    return runtime_install_fixture, source_application_fixture


def _context(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / "_installation_context_resolution.py").write_text(
        "profile_value = Path(get_account(get_uid()).pw_dir)\n", encoding="utf-8",
    )
    (directory / "installation-context.sh").write_text(
        'canonical_path_into CURRENT_PROFILE_HOME "$home_path" true || return\n', encoding="utf-8",
    )


def test_source_profile_isolation_covers_canonical_and_materialized_inputs(
    tmp_path, monkeypatch, fixtures,
):
    profile, _application = fixtures
    monkeypatch.setattr(profile, "os", SimpleNamespace(name="posix"))
    canonical = tmp_path / "libs" / "installation-context"
    worktrees = tmp_path / "plugins" / "agent-worktrees"
    containers = tmp_path / "plugins" / "agent-containers"
    directories = [
        canonical, worktrees / "scripts" / "installation-context",
        containers / "scripts" / "installation-context",
    ]
    for directory in directories:
        _context(directory)
    package = worktrees / "src" / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "_installation_context_resolution.py").write_bytes(
        (canonical / "_installation_context_resolution.py").read_bytes(),
    )
    home = tmp_path / "account's isolated home"
    profile.isolate_source_account_profile(tmp_path, home)
    for directory in [*directories, package]:
        value = (directory / "_installation_context_resolution.py").read_text(encoding="utf-8")
        assert value == f"profile_value = Path({str(home)!r})\n"
    for directory in directories:
        value = (directory / "installation-context.sh").read_text(encoding="utf-8")
        assert '"$home_path"' not in value and "account" in value
    assert not home.exists()


def test_unrecognized_profile_lookup_refuses_fixture_before_construction(
    tmp_path, monkeypatch, fixtures,
):
    profile, _application = fixtures
    monkeypatch.setattr(profile, "os", SimpleNamespace(name="posix"))
    canonical = tmp_path / "libs" / "installation-context"
    _context(canonical)
    (canonical / "_installation_context_resolution.py").write_text(
        "changed OS lookup\n", encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        profile.isolate_source_account_profile(tmp_path, tmp_path / "home")
    assert (canonical / "installation-context.sh").read_text(encoding="utf-8").startswith(
        'canonical_path_into CURRENT_PROFILE_HOME "$home_path"',
    )


@pytest.mark.parametrize("timeout", [False, True])
def test_application_fixture_commits_isolated_inputs_before_admission(
    tmp_path, monkeypatch, fixtures, capsys, timeout,
):
    _profile, application = fixtures
    repository = tmp_path / "repository with space"
    repository.mkdir()
    home = tmp_path / "home"
    events = []
    selected, original, deadline = "b" * 40, "a" * 40, 100.0

    def git(root, *arguments, **kwargs):
        assert kwargs == {"deadline": deadline}
        if root == repository:
            assert arguments == ("rev-parse", "HEAD")
            return original
        if "init" in arguments:
            (root / ".git").mkdir()
        if "fetch" in arguments:
            assert arguments[-2:] == (str(repository), original)
            assert "--depth=1" in arguments and "--no-recurse-submodules" in arguments
        if "checkout" in arguments:
            assert arguments[-1] == original
            (root / "copied").write_text("committed source")
        if "add" in arguments:
            assert (root / "isolated").read_text() == str(home)
            assert (root / ".git/info/attributes").read_text() == (
                "* -text -filter -ident -working-tree-encoding\n"
            )
        events.append(arguments)
        if "commit" in arguments:
            assert "trace2.eventTarget=2" in arguments
            if timeout:
                raise subprocess.TimeoutExpired(arguments, 10, stderr=b'{"event":"fixture timeout"}')
        if arguments[-3:] == ("diff", "--cached", "--name-only"):
            return "isolated"
        return selected if arguments[-2:] == ("rev-parse", "HEAD") else ""

    def isolate(checkout, account):
        assert account == home and (checkout / ".git").is_dir()
        assert (checkout / "copied").read_text() == "committed source"
        (checkout / "isolated").write_text(str(account))

    def admit(checkout, commit, branch, **kwargs):
        assert commit == selected and branch == "dev" and kwargs == {"deadline": deadline}
        assert "commit" in events[-3]
        assert events[-1][-3:] == ("update-ref", "refs/remotes/origin/dev", selected)
        assert not (checkout.parent / "source.tar").exists()

    monkeypatch.setitem(sys.modules, "prepare_runtime_source", SimpleNamespace(
        _git=git, _admit=admit,
        materializer=SimpleNamespace(uer=SimpleNamespace(_find_symlink=lambda *_a, **_k: None)),
    ))
    monkeypatch.setattr(application, "isolate_source_account_profile", isolate)
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired) as observed:
            application.application_source(
                repository, tmp_path / "fixture", home, deadline=deadline,
            )
        assert observed.value.timeout == 10
        output = capsys.readouterr().out
        assert '"event":"fixture timeout"' in output
        assert "Application source phase passed: verify ordinary committed source" in output
        assert "Application source phase failed: git -c trace2.eventTarget=2 commit" in output
        assert "Application source phase: admit isolated source" not in output
        assert "commit" in events[-1]
        return
    checkout, commit = application.application_source(
        repository, tmp_path / "fixture", home, deadline=deadline,
    )
    assert commit == selected and checkout == tmp_path / "fixture" / "checkout"
    assert list(repository.iterdir()) == []
    output = capsys.readouterr().out
    assert "Application source phase passed: verify ordinary committed source" in output
    assert "Application source phase passed: admit isolated source" in output
    assert "Application source phase failed:" not in output


@pytest.mark.parametrize("isolate", [False, True])
def test_application_fixture_checkout_is_independent_and_commits_only_isolation(
    tmp_path, monkeypatch, fixtures, isolate,
):
    import prepare_runtime_source as preparation

    _profile, application = fixtures
    repository = tmp_path / "repository with space"
    repository.mkdir()
    deadline = time.monotonic() + 60

    def git(*args):
        return preparation._git(repository, *args, deadline=deadline)

    git("init", "--quiet")
    (repository / ".gitattributes").write_text("*.ps1 text eol=crlf\n")
    (repository / "script.ps1").write_bytes(b"original\n")
    git("add", "--all")
    git("-c", "user.name=Runtime test", "-c", "user.email=runtime-test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "committed fixture")
    original = git("rev-parse", "HEAD")

    def isolate_profile(checkout, _home):
        assert (checkout / "script.ps1").read_bytes() == b"original\n"
        if isolate:
            (checkout / "script.ps1").write_bytes(b"account isolated\n")

    monkeypatch.setattr(application, "isolate_source_account_profile", isolate_profile)
    checkout, selected = application.application_source(
        repository, tmp_path / "fixture", tmp_path / "home", deadline=deadline,
    )
    assert (selected != original) is isolate
    assert not (checkout / ".git/objects/info/alternates").exists()
    repository.rename(tmp_path / "source-unavailable")
    assert preparation._admit(checkout, selected, "dev", deadline=deadline)[0] == selected
    assert preparation._git(checkout, "show", f"{selected}:script.ps1", deadline=deadline) == (
        "account isolated" if isolate else "original"
    )


@pytest.mark.parametrize("extras", [(), ("provider-exec",)])
def test_application_fixture_uses_existing_complete_provider(tmp_path, monkeypatch, fixtures, extras):
    _profile, application = fixtures
    checkout, output, snapshot = (tmp_path / name for name in ("checkout", "output", "snapshot"))
    commit, deadline, calls = "a" * 40, 100.0, []

    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return {"agent-containers": str(snapshot)}

    monkeypatch.setitem(sys.modules, "local_install_source", SimpleNamespace(
        prepare_applications=prepare,
    ))
    assert application.prepare_application(
        checkout, commit, "agent-containers", output, uv="fixture-uv",
        deadline=deadline, extras=extras,
    ) == snapshot
    assert calls == [((checkout, commit, commit, output), {
        "components": ("agent-containers",), "branch": "dev", "uv": "fixture-uv",
        "deadline": deadline, "extras": {"agent-containers": extras},
        "source_descriptor": {"source": "github", "repo": "Example/Plugins"},
    })]


def test_application_fixture_does_not_replace_failed_provider(tmp_path, monkeypatch, fixtures):
    _profile, application = fixtures

    def refuse(*_args, **_kwargs):
        raise ValueError("profile construction refused")

    monkeypatch.setitem(sys.modules, "local_install_source", SimpleNamespace(
        prepare_applications=refuse,
    ))
    with pytest.raises(ValueError, match="profile construction refused"):
        application.prepare_application(
            tmp_path, "a" * 40, "agent-worktrees", tmp_path / "output",
            uv="fixture-uv", deadline=100.0,
        )


@pytest.mark.parametrize("profile,timeout", [("base", 380), ("provider-exec", 250)])
def test_native_profile_timeout_reports_captured_phases_without_retry(
    tmp_path, monkeypatch, fixtures, capsys, profile, timeout,
):
    import test_source_profile_native as native

    failure = subprocess.TimeoutExpired(
        ["fixture"], timeout, output=b"last source phase\n", stderr=b"last child error\n",
    )
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    monkeypatch.setattr(native.shutil, "which", lambda _name: "fixture-command")
    monkeypatch.setattr(native.subprocess, "run", run)
    factory = SimpleNamespace(mktemp=lambda _name: tmp_path)
    with pytest.raises(subprocess.TimeoutExpired) as observed:
        native.test_native_source_profile_preserves_same_root_state_and_global_command(
            factory, profile,
        )
    assert observed.value is failure
    assert len(calls) == 1 and calls[0][1]["timeout"] == timeout
    output = capsys.readouterr().out
    assert "timeout stdout:\nlast source phase" in output
    assert "timeout stderr:\nlast child error" in output


@pytest.mark.parametrize("fails", [False, True])
def test_preparation_trace_preserves_calls_failures_and_originals(
    monkeypatch, fixtures, capsys, fails,
):
    _profile, application = fixtures
    calls = []
    failure = ValueError("original preparation failure")

    def operation(*args, **kwargs):
        calls.append((args, kwargs))
        if fails:
            raise failure
        return "original result"

    provider = SimpleNamespace(**{
        name: operation for name in (
            "compose", "_source_projects", "_build_input_roots",
            "_build_input_fingerprint", "_prepare_profiles",
        )
    })
    toolchain = SimpleNamespace(resolve_toolchain_lock=operation)
    promotion = SimpleNamespace(**{
        name: operation for name in (
            "add_scratch_worktree", "consume_pending_changes",
            "_seed_versions_from_main", "_sync_instruction_projections",
            "build_promotion_tree", "remove_scratch_worktree",
        )
    })
    materializer = SimpleNamespace(**{
        name: operation for name in (
            "materialize_file_pointers", "materialize_uv_editable_refs",
            "materialize_installer_engine_refs", "materialize_launch_wrapper_assets",
            "read_changefiles", "_consume_changefiles",
        )
    })
    promotion._load_module = lambda _path, _name: materializer
    monkeypatch.setitem(sys.modules, "local_install_source", provider)
    monkeypatch.setitem(sys.modules, "build_toolchain_lock", toolchain)
    monkeypatch.setitem(sys.modules, "promote_release", promotion)
    with application.trace_preparation(deadline=100.0):
        assert promotion._load_module(Path("fixture"), "promote_materialize_main") is materializer
        assert materializer.materialize_file_pointers is not operation
        if fails:
            with pytest.raises(ValueError) as observed:
                provider.compose("source", deadline=100.0)
            assert observed.value is failure
        else:
            assert provider.compose("source", deadline=100.0) == "original result"
    assert calls == [(("source",), {"deadline": 100.0})]
    assert provider.compose is operation and toolchain.resolve_toolchain_lock is operation
    assert materializer.materialize_file_pointers is operation
    output = capsys.readouterr()
    assert not output.out
    assert f"Application preparation {'failed' if fails else 'passed'}: operation" in output.err


@pytest.mark.parametrize("phase", ["reuse", "fresh", "foreign", "changed"])
def test_native_fixture_reuse_requires_the_original_completion_without_fresh_fallback(
    tmp_path, monkeypatch, fixtures, phase,
):
    _profile, application = fixtures
    expected = tmp_path / "prepared" / "profiles" / "demo" / "snapshot"
    marker = expected.parents[2] / "applications.json"
    marker.parent.mkdir()
    marker.write_text("original completion")
    output = tmp_path / "prepared-reuse"
    builds = []
    def build(*args, **kwargs):
        builds.append(True)
        pytest.fail("the fixture must block fresh construction")
    def prepare(*args, **kwargs):
        assert args == (tmp_path, "a" * 40, "a" * 40, output)
        assert kwargs["components"] == ("demo",) and kwargs["deadline"] == 100.0
        if phase == "fresh":
            provider._built_projects()
        if phase == "changed":
            marker.write_text("changed completion")
        return {"demo": str(expected if phase != "foreign" else tmp_path / "other")}
    provider = SimpleNamespace(prepare_applications=prepare, _built_projects=build)
    monkeypatch.setitem(sys.modules, "local_install_source", provider)
    if phase == "reuse":
        application.assert_application_reuse(
            tmp_path, "a" * 40, "demo", output, expected, uv="fixture-uv", deadline=100.0,
        )
    else:
        with pytest.raises(AssertionError):
            application.assert_application_reuse(
                tmp_path, "a" * 40, "demo", output, expected, uv="fixture-uv", deadline=100.0,
            )
    assert provider._built_projects is build
    assert builds == []
    assert not output.exists()
