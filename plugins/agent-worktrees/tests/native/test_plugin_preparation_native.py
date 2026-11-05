"""Opt-in native acquisition/CLI controls with caller-contained fixture state."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sysconfig
import threading
from urllib.parse import urlsplit

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_caller_owned
from plugin_activation import read_json_object

from agent_worktrees import plugin_preparation as preparation
import test_plugin_preparation as fixtures

pytestmark = [pytest.mark.real_installer_processes, pytest.mark.timeout(60)]
selected = fixtures.selected
write = fixtures.write


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
    for arguments, expected in (([], 0), (["--apply"], 1)):
        result = subprocess.run(
            [*command, *arguments], cwd=repo, env=dict(os.environ),
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == expected, result.stderr
        document = json.loads(result.stdout)
        assert document["schema"] == preparation.SCHEMA
        assert document["payloadsReady"] is False
    assert not (home / ".copilot" / "installed-plugins").exists()


@pytest.mark.parametrize("declaration_scope", ["repository", "trusted-repository", "user"])
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
            if declaration_scope != "user":
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
            plan = preparation.observe(repo, home=home)
            result = preparation.prepare(repo, plan["planDigest"], home=home)
            installed = home / ".copilot" / "installed-plugins" / "fixturemarket" / "sample"
            if declaration_scope == "repository":
                assert not result["payloadsReady"], result
                assert not installed.exists()
                _, config = read_json_object(home / ".copilot" / "config.json", jsonc_header=True)
                assert str(repo) not in config.get("trustedFolders", [])
                return
            assert result["payloadsReady"], result
            assert (installed / "skills" / "fixture" / "SKILL.md").read_text() == expected_payload
            state = home / ".copilot" / "settings.json"
            if state.exists():
                assert "sample@fixturemarket" not in json.loads(state.read_text()).get("enabledPlugins", {})
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()
