"""Acquisition preserves unrelated source/registry state and credential ownership."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex
import shutil

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import repos, repository_bootstrap as bootstrap, repository_setup as protocol
from test_repository_setup import URL, commit, consumer, git, source as source

pytestmark = pytest.mark.timeout(180)


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(repos, "_current_platform", lambda: "windows")
    monkeypatch.setattr(repos, "_best_effort_pin_credential", lambda *a: None)
    unrelated = repos.RepoEntry(
        name="precious", remote="https://example.invalid/other/precious",
        repo_class="worktree", account="separate", tags=["preserve"],
        paths={"windows": str(tmp_path / "precious")},
    )
    state = repos.read_registry(strict=True)
    state.repos["precious"] = unrelated
    repos.write_registry(state)
    return repos._repos_yaml_path()


def test_existing_source_registers_without_reclassifying_other_repos(source: Path, registry: Path) -> None:
    before = asdict(repos.read_registry(strict=True).repos["precious"])
    selected = bootstrap.acquire("sample", source, URL)
    state = repos.read_registry(strict=True)
    assert selected.commit == git(source, "rev-parse", "HEAD")
    assert asdict(state.repos["precious"]) == before
    assert state.repos["sample"].repo_class == "reference"
    assert state.repos["sample"].agent is False
    raw = registry.read_bytes()
    assert bootstrap.acquire("sample", source, URL) == selected
    assert registry.read_bytes() == raw


@pytest.mark.parametrize(("repo_class", "agent"), [("worktree", False), ("reference", True)])
def test_managed_name_without_local_path_refuses_before_acquisition(
    tmp_path, registry, monkeypatch, repo_class, agent,
) -> None:
    state = repos.read_registry(strict=True)
    state.repos["sample"] = repos.RepoEntry(
        name="sample", remote=URL, repo_class=repo_class, agent=agent,
        paths={"linux": "/preserved/remote-platform"},
    )
    repos.write_registry(state)
    before = registry.read_bytes()
    monkeypatch.setattr(bootstrap, "_clone", lambda *a, **k: pytest.fail("managed name acquired"))
    assert bootstrap.command([URL, "--target", str(tmp_path / "sample"), "--apply"]) == 1
    assert registry.read_bytes() == before
    assert not (tmp_path / "sample").exists()


@pytest.mark.parametrize("remote", ["", URL])
def test_acquisition_prioritizes_selected_registry_account(
    source, registry, tmp_path, monkeypatch, remote,
) -> None:
    state = repos.read_registry(strict=True)
    state.repos["first-alias"] = repos.RepoEntry(name="first-alias", remote=URL, account="other")
    state.repos["sample"] = repos.RepoEntry(name="sample", remote=remote, account="selected")
    repos.write_registry(state)
    observed = []
    monkeypatch.setattr(
        bootstrap, "_credential_args",
        lambda url, account, **kwargs: observed.append(account) or [],
    )
    monkeypatch.setattr(bootstrap, "_clone", lambda url, target, args, **k: shutil.copytree(source, target))
    target = tmp_path / "acquired"
    bootstrap.acquire("sample", target, URL)
    assert observed == ["selected"]
    assert repos.read_registry(strict=True).repos["sample"].account == "selected"


def test_selected_account_change_after_acquisition_refuses_registration(
    source, registry, tmp_path, monkeypatch,
) -> None:
    state = repos.read_registry(strict=True)
    state.repos["sample"] = repos.RepoEntry(name="sample", account="selected")
    repos.write_registry(state)
    monkeypatch.setattr(bootstrap, "_credential_args", lambda *a, **k: [])

    def clone(url, target, args, **kwargs):
        shutil.copytree(source, target)
        current = repos.read_registry(strict=True)
        current.repos["sample"].account = "changed"
        repos.write_registry(current)

    monkeypatch.setattr(bootstrap, "_clone", clone)
    target = tmp_path / "acquired"
    with pytest.raises(protocol.SetupError, match="account changed"):
        bootstrap.acquire("sample", target, URL)
    assert target.is_dir()
    assert repos.read_registry(strict=True).repos["sample"].local_path() is None


def test_conflicting_alias_accounts_require_selected_account(registry) -> None:
    state = repos.read_registry(strict=True)
    for name in ("first", "second"):
        state.repos[name] = repos.RepoEntry(name=name, remote=URL, account=name)
    with pytest.raises(protocol.SetupError, match="account is ambiguous"):
        bootstrap._account(state, "sample", URL)


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("owner_mapping", [False, True])
def test_account_is_not_inferred_from_an_unrelated_sibling(
    registry, registered, owner_mapping,
) -> None:
    selected = "https://github.com/example/sample.git"
    state = repos.read_registry(strict=True)
    state.repos["sibling"] = repos.RepoEntry(
        name="sibling", remote="https://github.com/example/unrelated.git", account="wrong",
    )
    if registered:
        state.repos["sample"] = repos.RepoEntry(name="sample", remote=selected)
    if owner_mapping:
        state.account_map["example"] = "declared-owner"
    assert bootstrap._account(state, "sample", selected) == (
        "declared-owner" if owner_mapping else ""
    )


@pytest.mark.parametrize("kind", ["dirty", "foreign", "registered-elsewhere", "managed", "alias"])
def test_refusal_preserves_registry_bytes_and_source(source: Path, registry: Path, kind: str) -> None:
    if kind == "dirty":
        (source / "precious.txt").write_text("unpublished work", encoding="utf-8")
    elif kind == "foreign":
        git(source, "remote", "set-url", "origin", "https://example.invalid/other/sample")
    else:
        state = repos.read_registry(strict=True)
        state.repos["alias" if kind == "alias" else "sample"] = repos.RepoEntry(
            name="sample", remote=URL,
            repo_class="worktree" if kind == "managed" else "reference",
            paths={"windows": str(source.parent / "elsewhere" if kind == "registered-elsewhere" else source)},
        )
        repos.write_registry(state)
    before = registry.read_bytes()
    files = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    assert bootstrap.command([URL, "--target", str(source), "--apply",
                              "--trust-repository-setup"]) == 1
    assert registry.read_bytes() == before
    assert {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()} == files


def test_plan_never_clones_registers_or_executes(source: Path, registry: Path, monkeypatch) -> None:
    before = registry.read_bytes()
    monkeypatch.setattr(bootstrap, "acquire", lambda *a: pytest.fail("plan acquired"))
    monkeypatch.setattr(protocol, "run", lambda *a, **k: pytest.fail("plan executed"))
    assert bootstrap.command([URL, "--target", str(source)]) == 0
    assert bootstrap.command([URL, "--target", str(source.parent / "absent")]) == 0
    assert registry.read_bytes() == before


def test_no_trust_stops_before_even_check(source: Path, registry: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_terminal", lambda: False)
    monkeypatch.setattr(protocol, "_call", lambda *a: pytest.fail("untrusted repository code"))
    assert bootstrap.command([URL, "--target", str(source), "--apply"]) == 1
    assert repos.read_registry(strict=True).repos["sample"].local_path() == str(source)


@pytest.mark.parametrize("helper", ["", "store", "!gh auth git-credential", "manager\ncredential.helper store"])
def test_explicit_account_never_falls_back_to_unqualified_helper(tmp_path, monkeypatch, helper) -> None:
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, f"credential.helper {helper}\n", "",
    ))
    with pytest.raises(protocol.SetupError, match="ambient authentication was not attempted"):
        bootstrap._credential_args("https://github.com/example/sample.git", "selected-login", cwd=tmp_path)


def test_explicit_gcm_route_contains_no_token_and_preserves_global_settings(tmp_path, monkeypatch) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "credential.helper manager\n", "")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    selected = "https://github.com/example/sample.git"
    args = bootstrap._credential_args(selected, "selected-login", cwd=tmp_path)
    assert f"credential.{selected}.username=selected-login" in args
    assert "credential.gitHubAccountFiltering=true" in args
    assert len(calls) == 1 and "--get-urlmatch" in calls[0][0]
    assert "credential.helper=" in args and "credential.helper=manager" in args
    assert f"http.{selected}.extraHeader=" in args
    assert not any("token" in a for a in args)


def test_real_multi_helper_and_http_header_isolation(tmp_path, monkeypatch) -> None:
    selected = "https://github.com/example/sample.git"
    config = tmp_path / "isolated.gitconfig"
    helper_bin = tmp_path / "helpers"
    helper_bin.mkdir()
    ambient = tmp_path / "ambient.py"
    ambient.write_text(
        "import pathlib\npathlib.Path(__file__).with_suffix('.called').write_text('unsafe')\n",
        encoding="utf-8",
    )
    owner = tmp_path / "owner.py"
    owner.write_text(
        "import pathlib,sys\n"
        "if sys.argv[-1] == 'get':\n"
        "    request=sys.stdin.read()\n"
        "    pathlib.Path(__file__).with_suffix('.called').write_text(request)\n"
        "    print('username=selected-login\\npassword=fixture-value\\n')\n",
        encoding="utf-8",
    )
    shim = helper_bin / "git-credential-manager"
    shim.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(owner)) + ' "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    git(tmp_path, "config", "--file", str(config), "credential.helper",
        "!" + shlex.quote(sys.executable) + " " + shlex.quote(str(ambient)))
    git(tmp_path, "config", "--file", str(config), f"credential.{selected}.helper", "manager")
    for key in ("http.extraHeader", f"http.{selected}.extraHeader"):
        git(tmp_path, "config", "--file", str(config), key, "Authorization: fixture-only")
    before = config.read_bytes()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_EXEC_PATH", str(helper_bin))
    args = bootstrap._credential_args(selected, "selected-login", cwd=tmp_path)
    headers = git(tmp_path, *args, "config", "--get-urlmatch", "http.extraHeader", selected)
    assert headers == ""
    credential = subprocess.run(
        ["git", *args, "credential", "fill"], cwd=tmp_path,
        input=f"url={selected}\n\n", text=True, capture_output=True, timeout=30,
        env=bootstrap._acquisition_environment(), **no_window_kwargs(),
    )
    assert credential.returncode == 0, credential.stderr
    assert "username=selected-login" in credential.stdout
    assert "password=fixture-value" in credential.stdout
    assert "username=selected-login" in owner.with_suffix(".called").read_text()
    assert not ambient.with_suffix(".called").exists()
    assert config.read_bytes() == before


@pytest.mark.parametrize("override", ["config-provider", "env-provider", "legacy-authority"])
def test_explicit_account_refuses_unqualified_gcm_provider(tmp_path, monkeypatch, override) -> None:
    config = tmp_path / "provider.gitconfig"
    git(tmp_path, "config", "--file", str(config), "credential.helper", "manager")
    if override == "config-provider":
        git(tmp_path, "config", "--file", str(config),
            "credential.https://github.com/example/sample.git.provider", "generic")
    elif override == "env-provider":
        monkeypatch.setenv("GCM_PROVIDER", "generic")
    else:
        monkeypatch.setenv("GCM_AUTHORITY", "basic")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    before = config.read_bytes()
    with pytest.raises(protocol.SetupError, match="ambient authentication was not attempted"):
        bootstrap._credential_args(
            "https://github.com/example/sample.git", "selected-login", cwd=tmp_path,
        )
    assert config.read_bytes() == before


def test_redirected_git_refuses_without_askpass_or_credential_ui(tmp_path, monkeypatch) -> None:
    config = tmp_path / "isolated.gitconfig"
    helper = tmp_path / "owner.py"
    helper.write_text(
        "import json,os,pathlib,sys\n"
        "sys.stdin.read()\n"
        "pathlib.Path(__file__).with_suffix('.called').write_text(json.dumps({"
        "k:os.environ.get(k) for k in ['GCM_INTERACTIVE','GCM_GUI_PROMPT']}))\n",
        encoding="utf-8",
    )
    askpass = tmp_path / "askpass.py"
    askpass.write_text(
        "import pathlib\npathlib.Path(__file__).with_suffix('.called').write_text('unsafe')\n"
        "print('fixture-value')\n", encoding="utf-8",
    )
    command = "!" + shlex.quote(sys.executable) + " " + shlex.quote(str(helper))
    ask = shlex.quote(sys.executable) + " " + shlex.quote(str(askpass))
    git(tmp_path, "config", "--file", str(config), "credential.helper", command)
    git(tmp_path, "config", "--file", str(config), "core.askPass", ask)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_ASKPASS", "SSH_ASKPASS"):
        monkeypatch.setenv(name, ask)
    for name in ("GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE", "GCM_GUI_PROMPT"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setattr(bootstrap, "_terminal", lambda: False)
    result = subprocess.run(
        ["git", "credential", "fill"], cwd=tmp_path, input=f"url={URL}\n\n",
        text=True, capture_output=True, timeout=30,
        env=bootstrap._acquisition_environment(), **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert not askpass.with_suffix(".called").exists()
    assert json.loads(helper.with_suffix(".called").read_text()) == {
        "GCM_INTERACTIVE": "0", "GCM_GUI_PROMPT": "0",
    }
    assert os.environ["GCM_INTERACTIVE"] == "1"
    assert os.environ["GIT_ASKPASS"] == ask


@pytest.mark.skipif(os.name != "nt", reason="native unavailable Windows drive")
@pytest.mark.parametrize("apply", [False, True])
def test_unreachable_drive_refuses_before_any_effect(tmp_path, monkeypatch, apply) -> None:
    unavailable = next(
        (Path(f"{letter}:\\") for letter in "ZYXWVUTSRQPONMLKJIHGFED"
         if not Path(f"{letter}:\\").exists()),
        None,
    )
    if unavailable is None:
        pytest.skip("No unavailable drive for the native refusal")
    monkeypatch.setattr(bootstrap, "acquire", lambda *a: pytest.fail("unreachable target acquired"))
    assert bootstrap.command([
        URL, "--target", str(unavailable / "source"), *(["--apply"] if apply else []),
    ]) == 1


def test_clone_failure_preserves_partial_target_and_does_not_register(tmp_path, registry, monkeypatch) -> None:
    target = tmp_path / "sample"
    before = registry.read_bytes()
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 128, "", "")
        if "ls-remote" in argv:
            return subprocess.CompletedProcess(argv, 0, URL, "")
        target.mkdir()
        (target / "partial").write_text("preserve", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 128)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    with pytest.raises(protocol.SetupError, match="partial target was preserved"):
        bootstrap.acquire("sample", target, URL)
    assert registry.read_bytes() == before
    assert (target / "partial").read_text() == "preserve"
    assert "timeout" not in calls[-1]
    assert not calls[-1].get("capture_output", False)
    if os.name == "nt":
        assert calls[-1]["stdin"] == 0
        assert calls[-1]["stdout"] == 1
    else:
        assert "stdin" not in calls[-1]


def test_rewrite_refused_before_any_clone(tmp_path, registry, monkeypatch) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 128, "", "")
        return subprocess.CompletedProcess(argv, 0, "https://elsewhere.invalid/sample", "")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    with pytest.raises(protocol.SetupError, match="rewrites"):
        bootstrap.acquire("sample", tmp_path / "sample", URL)
    assert len(calls) == 2 and not any("clone" in argv for argv in calls)
    assert not (tmp_path / "sample").exists()


def test_nested_acquisition_refuses_before_clone(source, registry, monkeypatch) -> None:
    before = registry.read_bytes()
    monkeypatch.setattr(bootstrap, "_clone", lambda *a: pytest.fail("nested source acquired"))
    with pytest.raises(protocol.SetupError, match="nested"):
        bootstrap.acquire("sample", source / "child", URL)
    assert registry.read_bytes() == before
    assert not (source / "child").exists()


def test_real_owner_cli_reaches_final_check(source: Path, registry: Path) -> None:
    consumer(source, """
if ($r.mode -eq 'apply') { Set-Content -LiteralPath $state -Value 'done' }
$s = if (Test-Path -LiteralPath $state) { 'ready' } else { 'needed' }
[IO.File]::WriteAllText($r.result_file,
    ('{"version":1,"status":"' + $s + '","message":"owner verified"}'))
""")
    assert bootstrap.command([URL, "--target", str(source), "--apply",
                              "--trust-repository-setup"]) == 0
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]


def test_rejected_selected_declaration_preserves_successful_acquisition(source, registry) -> None:
    (source / protocol.DECLARATION).write_text(json.dumps({"version": 99}), encoding="utf-8")
    commit(source)
    assert bootstrap.command([URL, "--target", str(source), "--apply",
                              "--trust-repository-setup"]) == 1
    assert repos.read_registry(strict=True).repos["sample"].local_path() == str(source)


@pytest.mark.parametrize("pinned", [False, True])
def test_real_manager_to_engine_to_repository_consumer(source, registry, tmp_path, pinned) -> None:
    consumer(source, """
if ($r.mode -eq 'apply') { Set-Content -LiteralPath $state -Value 'done' }
$s = if (Test-Path -LiteralPath $state) { 'ready' } else { 'needed' }
[IO.File]::WriteAllText($r.result_file,
    ('{"version":1,"status":"' + $s + '","message":"owner verified"}'))
""")
    root = Path(__file__).resolve().parents[3]
    log = tmp_path / "core-events.json"
    driver = root / "worktree-manager" / "tests" / "assets" / "repository-setup-driver.py"
    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, str(driver), str(root / "worktree-manager" / "src"),
         URL, str(source), str(log), *([git(source, "rev-parse", "HEAD")] if pinned else [])],
        capture_output=True, text=True, timeout=180, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(log.read_text()) == ["prerequisites", "core"]
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]
    assert "Repository setup: ready" in result.stdout
    assert repos.read_registry(strict=True).repos["sample"].local_path() == str(source)


@pytest.mark.parametrize("value", ["", "main", "a" * 39, "g" * 40, "a" * 41, "HEAD^{commit}", "--all"])
def test_invalid_commit_refuses_before_any_effect(tmp_path, registry, monkeypatch, value) -> None:
    before = registry.read_bytes()
    monkeypatch.setattr(bootstrap, "_selection", lambda *a, **k: pytest.fail("invalid intent selected"))
    assert bootstrap.command([URL, "--commit", value, "--apply"]) == 2
    with pytest.raises(protocol.SetupError, match="full"):
        bootstrap.acquire("sample", tmp_path / "absent", URL, commit=value)
    assert registry.read_bytes() == before
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("apply", [False, True])
def test_existing_commit_mismatch_preserves_head_source_and_registry(
    source, registry, monkeypatch, apply,
) -> None:
    old = git(source, "rev-parse", "HEAD")
    (source / "newer.txt").write_text("preserve newer work", encoding="utf-8")
    commit(source)
    head = git(source, "rev-parse", "HEAD")
    before = registry.read_bytes()
    monkeypatch.setattr(bootstrap, "_clone", lambda *a, **k: pytest.fail("existing source acquired"))
    monkeypatch.setattr(protocol, "run", lambda *a, **k: pytest.fail("mismatched source executed"))
    assert bootstrap.command([
        URL, "--target", str(source), "--commit", old,
        *(["--apply", "--trust-repository-setup"] if apply else []),
    ]) == 1
    assert git(source, "rev-parse", "HEAD") == head
    assert git(source, "status", "--porcelain") == ""
    assert (source / "newer.txt").read_text() == "preserve newer work"
    assert registry.read_bytes() == before


def test_existing_exact_commit_reuses_without_fetch_or_checkout(source, registry, monkeypatch) -> None:
    head = git(source, "rev-parse", "HEAD")
    monkeypatch.setattr(bootstrap, "_clone", lambda *a, **k: pytest.fail("existing source acquired"))
    selected = bootstrap.acquire("sample", source, URL, commit=head.upper())
    before = registry.read_bytes()
    assert selected.commit == head
    assert bootstrap.acquire("sample", source, URL, commit=head) == selected
    assert registry.read_bytes() == before


@pytest.mark.parametrize("phase", ["plan", "apply", "reverify"])
def test_replacement_tree_cannot_satisfy_an_approved_commit(
    source, registry, monkeypatch, phase,
) -> None:
    approved = git(source, "rev-parse", "HEAD")
    for name in ("setup.ps1", "setup.sh"):
        (source / name).write_text("# unapproved replacement source\n", encoding="utf-8")
    commit(source)
    replacement = git(source, "rev-parse", "HEAD")
    git(source, "replace", approved, replacement)
    git(source, "-c", "core.hooksPath=" + os.devnull, "checkout", "-q", "--detach", approved)
    assert git(source, "rev-parse", "HEAD") == approved
    assert git(source, "status", "--porcelain") == ""
    before = registry.read_bytes()
    monkeypatch.setattr(protocol, "_interpreter", lambda *a: pytest.fail("replacement source executed"))
    if phase == "reverify":
        # Revalidate a setup authorized by a reader that honored replacement refs.
        script = source / ("setup.ps1" if sys.platform == "win32" else "setup.sh")
        selected = protocol.Setup(
            protocol.Source(source, URL, approved), script,
            "powershell" if sys.platform == "win32" else "bash",
            protocol._digest(source / protocol.DECLARATION), protocol._digest(script),
        )
        with pytest.raises(protocol.SetupError, match="source changes"):
            protocol.run(selected, apply=True, trusted=True)
    else:
        assert bootstrap.command([
            URL, "--target", str(source), "--commit", approved,
            *(["--apply", "--trust-repository-setup"] if phase == "apply" else []),
        ]) == 1
    assert registry.read_bytes() == before
    assert git(source, "rev-parse", "HEAD") == approved
    assert git(source, "rev-parse", f"refs/replace/{approved}") == replacement
    assert (source / "setup.ps1").read_text() == "# unapproved replacement source\n"


@pytest.mark.parametrize("failure", ["peeled-tag", "checkout"])
def test_new_commit_selection_failure_preserves_partial_target_without_registration(
    tmp_path, registry, monkeypatch, failure,
) -> None:
    target = tmp_path / "selected"
    before = registry.read_bytes()
    calls = []
    requested = "a" * 40

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 128, "", "")
        if "ls-remote" in argv:
            return subprocess.CompletedProcess(argv, 0, URL, "")
        if "clone" in argv:
            assert "--no-replace-objects" in argv
            target.mkdir()
            (target / ".git").mkdir()
            return subprocess.CompletedProcess(argv, 0)
        assert failure == "checkout" and "checkout" in argv
        assert "--no-replace-objects" in argv
        assert "core.hooksPath=" + os.devnull in argv
        assert "--detach" in argv and requested in argv and "--force" not in argv
        assert "timeout" not in kwargs and not kwargs.get("capture_output", False)
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(protocol, "_git", lambda *a: "b" * 40 if failure == "peeled-tag" else requested)
    with pytest.raises(protocol.SetupError, match="exact selected commit|status 7"):
        bootstrap.acquire("sample", target, URL, commit=requested)
    assert registry.read_bytes() == before
    assert (target / ".git").is_dir()
    assert any("clone" in argv and "--no-checkout" in argv for argv, _ in calls)
    assert not any("fetch" in argv for argv, _ in calls)
    assert any("checkout" in argv for argv, _ in calls) is (failure == "checkout")
