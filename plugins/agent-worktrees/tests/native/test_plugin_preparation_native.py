"""Opt-in native acquisition/CLI controls with caller-contained fixture state."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import re
import sys
import sysconfig
import threading
import time
from urllib.parse import urlsplit

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_caller_owned
from plugin_activation import activation_preservation_pending, read_json_object

from agent_worktrees import plugin_preparation as preparation, update_acquisition
import test_plugin_preparation as fixtures

pytestmark = [pytest.mark.real_installer_processes, pytest.mark.timeout(60)]
selected = fixtures.selected
write = fixtures.write


@pytest.mark.skipif(os.name != "nt", reason="Native Windows hard deadline and Job settlement")
@pytest.mark.timeout(30)
def test_prepared_windows_absolute_deadline(selected, monkeypatch):
    import plugin_resolve.preparation as native

    home, repo, _ = selected
    monkeypatch.setattr(update_acquisition._auth, "mode", lambda: "off")

    def drive(source, definition, *, staging, repo, copilot_command, environment, runner,
              check_cancelled, authentication):
        return runner(
            [sys.executable, "-u", "-c",
             "import time\nwhile True:\n print('fixture progress',flush=True)\n time.sleep(.05)"],
            cwd=repo, timeout=3, env={**environment, "COPILOT_HOME": str(home / ".copilot")},
        )

    monkeypatch.setattr(native, "prepare_payload", drive)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        update_acquisition.acquire_prepared(
            "sample@first", {"source": {"source": "github", "repo": "example/first"}},
            sys.executable, cwd=repo, staging=home / "p", revalidate=lambda: None,
        )
    assert time.monotonic() - started < 10
    assert not activation_preservation_pending(home / ".copilot")


def test_real_cli_repository_local_profile(selected):
    home, repo, settings = selected
    write(settings, {})
    config = repo / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("copilot_plugin_profile: local-profile.json\n", encoding="utf-8")
    payload = repo / ".ai" / "sample"
    write(payload / "plugin.json", {"name": "sample", "version": "1.0.0"})
    write(payload.parent / ".github" / "plugin" / "marketplace.json", {
        "name": "local", "plugins": [{"name": "sample", "source": "./sample"}],
    })
    write(repo / "local-profile.json", {
        "enabledPlugins": {"sample@local": True},
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    for args in (
        ["init", "-q", "--initial-branch=main"], ["add", "."],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"],
    ):
        result = run_caller_owned(
            ["git", "-C", str(repo), *args], cwd=repo, env=dict(os.environ),
            timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert result.returncode == 0, result.stderr
    entry = Path(sysconfig.get_path("scripts")) / ("agent-worktrees.exe" if os.name == "nt" else "agent-worktrees")
    result = run_caller_owned(
        [str(entry), "prepare-plugin-payloads", "--repo", str(repo), "--include-tool-profile"],
        cwd=repo, env=dict(os.environ), timeout=30,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    row, = observed["entries"]
    assert observed["payloadsReady"] and observed["selectionMode"] == preparation.PROFILE_CAPABILITY
    assert row["source"] == "sample@local" and Path(row["payloadRoot"]) == payload
    assert preparation.observe(repo, home=home)["entries"] == []
    assert json.loads(settings.read_text()) == {}
    assert not (home / ".copilot").exists()


@pytest.mark.timeout(240)
def test_native_profile_catalog(tmp_path, monkeypatch, native_powershell):
    native = os.environ.get("NATIVE_COPILOT_TEST_EXE")
    if not native or os.environ.get("NATIVE_COPILOT_PUBLIC_MARKETPLACE") != "1":
        pytest.skip("Explicit existing native executable and public acquisition lane not selected")
    assert Path(native).is_absolute() and Path(native).is_file()
    home, repo = tmp_path / "h", tmp_path / "r"
    home.mkdir()
    repo.mkdir()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT"):
        monkeypatch.delenv(key, raising=False)
    for key in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(home))
    for key, path in (
        ("COPILOT_HOME", home / ".copilot"),
        ("COPILOT_CACHE_HOME", home / ".cache"),
        ("APPDATA", home / "AppData" / "Roaming"),
        ("LOCALAPPDATA", home / "AppData" / "Local"),
    ):
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("COPILOT_AUTO_UPDATE", "false")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    config = repo / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("copilot_plugin_profile: p.json\n", encoding="utf-8")
    write(repo / "p.json", {
        "enabledPlugins": {"agent-machines@copilot-extensions": True},
        "extraKnownMarketplaces": {"copilot-extensions": {"source": {
            "source": "github", "repo": "ThomasMichon/copilot-extensions",
        }}},
    })
    for arguments in (
        ["init", "-q", "--initial-branch=main"], ["add", "."],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"],
    ):
        result = run_caller_owned(
            ["git", "-C", str(repo), *arguments], cwd=repo, env=dict(os.environ),
            timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert result.returncode == 0, result.stderr
    before = {path: path.read_bytes() for path in repo.rglob("*") if path.is_file() and ".git" not in path.parts}
    monkeypatch.setattr(preparation.reconcile, "resolve_copilot", lambda: native)
    plan = preparation.observe(repo, home=home, include_tool_profile=True)
    assert not plan["payloadsReady"]
    result = preparation.prepare(repo, plan["planDigest"], home=home, include_tool_profile=True)
    assert result["payloadsReady"], result
    row, = result["entries"]
    payload = Path(row["payloadRoot"])
    environment = {**os.environ, "COPILOT_HOME": row["payloadContext"]["copilotHome"],
                   "AGENT_MACHINES_NO_SELFPROVISION": "1"}
    emitted = run_caller_owned(
        [native_powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(payload / "scripts" / "emit-command-catalog.ps1")],
        cwd=repo, env=environment, timeout=30,
        max_stdout_bytes=256 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert emitted.returncode == 0, emitted.stderr
    context = json.loads(emitted.stdout)["additionalContext"]
    catalogs = re.findall(r"```json\s*\n(.*?)\n```", context, re.DOTALL)
    assert len(catalogs) == 1
    catalog = json.loads(catalogs[0])
    command, = catalog["commands"]
    assert catalog["plugin"] == command["id"] == "agent-machines"
    assert command["availability"] == "ready" and command["shell"] == "direct"
    assert Path(command["argv"][0]).is_file() and Path(command["argv"][0]).is_relative_to(payload)
    assert before == {path: path.read_bytes() for path in repo.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert not (home / ".copilot" / "settings.json").exists()
    assert not (home / ".copilot" / "installed-plugins").exists()
    assert not (home / ".agent-machines").exists()
    assert preparation.observe(repo, home=home)["entries"] == []
    assert preparation.observe(repo, home=home, include_tool_profile=True)["payloadsReady"]


def test_native_acquisition_preserves_activation_and_checks_actual_payload(selected):
    home, repo, settings = selected
    document = json.loads(settings.read_text())
    document["enabledPlugins"]["sample@second"] = False
    write(settings, document)
    write(home / ".copilot" / "settings.json", {"enabledPlugins": {"unrelated@first": False}})
    # Python stands in only for the finite native acquisition process.
    (repo / "plugin").write_text(
        "import json, os, pathlib, sys\n"
        "assert sys.argv[1:] == ['install', 'sample@first']\n"
        "root = pathlib.Path(os.environ['COPILOT_HOME'])\n"
        "settings = root / 'settings.json'\n"
        "data = json.loads(settings.read_text()); data['enabledPlugins']['sample@first'] = True\n"
        "settings.write_text(json.dumps(data))\n"
        "(root / 'config.json').write_text(json.dumps({'installedPlugins': ["
        "{'name': 'sample', 'marketplace': 'first', 'enabled': True}]}))\n"
        "payload = root / 'installed-plugins' / 'first' / 'sample'\n"
        "payload.mkdir(parents=True)\n"
        "(payload / 'plugin.json').write_text(json.dumps({'name': 'sample', 'version': '1.0.0'}))\n",
        encoding="utf-8",
    )
    plan = preparation.observe(repo, home=home)
    result = preparation.prepare(repo, plan["planDigest"], home=home)
    assert result["payloadsReady"], result
    assert json.loads((home / ".copilot" / "settings.json").read_text()) == {
        "enabledPlugins": {"unrelated@first": False},
    }
    assert json.loads((home / ".copilot" / "config.json").read_text())["installedPlugins"][0]["enabled"] is False


def test_real_cli_observation_and_unpinned_apply_never_acquire(selected):
    home, repo, _ = selected
    entry = Path(sysconfig.get_path("scripts")) / (
        "agent-worktrees.exe" if os.name == "nt" else "agent-worktrees"
    )
    assert entry.is_file()
    command = [
        str(entry), "prepare-plugin-payloads",
        "--repo", str(repo),
    ]
    for arguments, expected in (
        ([], 0), (["--apply"], 1), (["--include-tool-profile"], 0),
        (["--include-tool-pro"], 2),
    ):
        result = subprocess.run(
            [*command, *arguments], cwd=repo, env=dict(os.environ),
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == expected, result.stderr
        if expected == 2:
            assert "unrecognized arguments" in result.stderr
            continue
        document = json.loads(result.stdout)
        assert document["schema"] == preparation.SCHEMA
        assert document["payloadsReady"] is False
        if expected == 0:
            assert preparation.PROFILE_CAPABILITY in document["capabilities"]
        if arguments == ["--include-tool-profile"]:
            assert document["selectionMode"] == preparation.PROFILE_CAPABILITY
    assert not (home / ".copilot" / "installed-plugins").exists()


@pytest.mark.parametrize("declaration_scope", ["repository", "trusted-repository", "user", "tool-profile"])
def test_native_copilot_preserves_source_and_trust_scope(selected, tmp_path, monkeypatch, declaration_scope):
    native = os.environ.get("NATIVE_COPILOT_TEST_EXE")
    if not native:
        pytest.skip("Explicit existing native Copilot executable not selected")
    assert Path(native).is_absolute() and Path(native).is_file()
    home, repo, settings = selected
    monkeypatch.setenv("COPILOT_AUTO_UPDATE", "false")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("COPILOT_CACHE_HOME", str(home / ".cache" / "copilot"))
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    source = tmp_path / "market-source"
    source.mkdir()
    write(source / ".github" / "plugin" / "marketplace.json", {
        "name": "fixturemarket", "owner": {"name": "Fixture"},
        "plugins": [{"name": "sample", "source": "./plugins/sample", "description": "Fixture"}],
    })
    write(source / "plugins" / "sample" / "plugin.json", {
        "name": "sample", "version": "1.0.0", "description": "Fixture", "skills": "./skills",
    })
    skill = source / "plugins" / "sample" / "skills" / "fixture" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    expected_payload = (
        "---\nname: native-fixture\ndescription: A local acquisition fixture\n---\n"
        "Native acquisition fixture payload.\n"
    )
    skill.write_text(expected_payload, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    git_executable = shutil.which("git")
    assert git_executable

    def git(*args):
        subprocess.run(
            [git_executable, *map(str, args)], check=True, capture_output=True,
            text=True, timeout=15, **no_window_kwargs(),
        )

    git("-C", source, "init", "-q", "--initial-branch=main")
    git("-C", source, "add", ".")
    git("-C", source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")
    git("-C", source, "tag", "fixture-source")
    skill.write_text(expected_payload + "Wrong default-branch payload.\n", encoding="utf-8")
    git("-C", source, "add", ".")
    git("-C", source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "New default branch")
    git("-C", repo, "init", "-q", "--initial-branch=main")
    served = tmp_path / "served"
    served.mkdir()
    bare = served / "market.git"
    git("clone", "--bare", source, bare)
    git("--git-dir", bare, "update-server-info")

    class GitHandler(BaseHTTPRequestHandler):
        def serve_git(self):
            route = urlsplit(self.path)
            if not route.path.startswith("/market.git/"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            assert length <= 1024 * 1024
            result = run_caller_owned(
                [git_executable, "http-backend"], cwd=served,
                env={
                    **os.environ, "GIT_PROJECT_ROOT": str(served), "GIT_HTTP_EXPORT_ALL": "1",
                    "PATH_INFO": route.path, "QUERY_STRING": route.query,
                    "REQUEST_METHOD": self.command, "CONTENT_LENGTH": str(length),
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
                },
                input_text=self.rfile.read(length).decode("ascii"),
                binary=True, timeout=15, max_stdout_bytes=4 * 1024 * 1024,
                max_stderr_bytes=64 * 1024,
            )
            assert result.returncode == 0, result.stderr
            headers, separator, body = result.stdout.partition(b"\r\n\r\n")
            if not separator:
                headers, separator, body = result.stdout.partition(b"\n\n")
            assert separator
            fields = [
                line.decode("ascii").split(":", 1) for line in headers.splitlines() if b":" in line
            ]
            status = next((int(value.strip().split()[0]) for key, value in fields if key == "Status"), 200)
            self.send_response(status)
            for key, value in fields:
                if key != "Status":
                    self.send_header(key, value.strip())
            self.end_headers()
            self.wfile.write(body)

        do_GET = serve_git
        do_POST = serve_git

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), GitHandler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/market.git"
            transport = run_caller_owned(
                [git_executable, "clone", "--depth=1", url, str(tmp_path / "transport-check")],
                cwd=tmp_path, env=dict(os.environ), timeout=15,
                max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
            )
            assert transport.returncode == 0, transport.stderr
            definition = {"fixturemarket": {"source": {
                    "source": "git", "url": url, "ref": "fixture-source",
            }}}
            target = {"enabledPlugins": {"sample@fixturemarket": True}}
            profile_mode = declaration_scope == "tool-profile"
            if profile_mode:
                # This lane follows the actual default branch, not the tag control.
                definition["fixturemarket"]["source"].pop("ref")
                config = repo / ".agent-worktrees" / "config.yaml"
                config.parent.mkdir()
                config.write_text("copilot_plugin_profile: tool-plugins.json\n", encoding="utf-8")
                write(repo / "tool-plugins.json", {**target, "extraKnownMarketplaces": definition})
                target = {}
                write(home / ".copilot" / "settings.json", {"enabledPlugins": {"unrelated@fixturemarket": False}})
            elif declaration_scope != "user":
                target["extraKnownMarketplaces"] = definition
            else:
                write(home / ".copilot" / "settings.json", {"extraKnownMarketplaces": definition})
            write(settings, target)
            git("-C", repo, "add", ".")
            git("-C", repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")
            if declaration_scope == "trusted-repository":
                write(home / ".copilot" / "config.json", {"trustedFolders": [str(repo)]})
            monkeypatch.setattr(preparation.reconcile, "resolve_copilot", lambda: native)
            original_settings = settings.read_bytes()
            user_settings = home / ".copilot" / "settings.json"
            original_user = user_settings.read_bytes() if user_settings.exists() else None
            plan = preparation.observe(repo, home=home, include_tool_profile=profile_mode)
            result = preparation.prepare(
                repo, plan["planDigest"], home=home, include_tool_profile=profile_mode,
            )
            installed = Path(result["entries"][0]["payloadRoot"]) if result["payloadsReady"] else (
                home / ".copilot" / "installed-plugins" / "fixturemarket" / "sample"
            )
            if declaration_scope == "repository":
                assert not result["payloadsReady"], result
                assert not installed.exists()
                _, config = read_json_object(home / ".copilot" / "config.json", jsonc_header=True)
                assert str(repo) not in config.get("trustedFolders", [])
                return
            assert result["payloadsReady"], result
            assert (installed / "skills" / "fixture" / "SKILL.md").read_text() == (
                expected_payload + "Wrong default-branch payload.\n" if profile_mode else expected_payload
            )
            if profile_mode:
                row, = result["entries"]
                qualified = preparation.context.resolve_payload_source(
                    payload_root=installed, plugin_id="sample",
                    copilot_home=row["payloadContext"]["copilotHome"], environment={},
                )
                assert qualified["sourceFingerprint"] == row["sourceFingerprint"]
                assert settings.read_bytes() == original_settings
                assert user_settings.read_bytes() == original_user
                assert not (home / ".copilot" / "installed-plugins").exists()
                assert not (repo / ".github" / "copilot" / "settings.local.json").exists()
                assert preparation.observe(repo, home=home)["entries"] == []
                assert preparation.observe(repo, home=home, include_tool_profile=True)["payloadsReady"]
            state = home / ".copilot" / "settings.json"
            if state.exists():
                assert "sample@fixturemarket" not in json.loads(state.read_text()).get("enabledPlugins", {})
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()
