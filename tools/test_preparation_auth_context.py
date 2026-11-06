"""Authentication-directory inputs are preparation-scoped, not actor state."""

from __future__ import annotations

import importlib.util
import base64
import io
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "libs" / "installer-engine")]
import installer_dependency_policy as policy  # noqa: E402
import plugin_test_environment as preparation  # noqa: E402
from plugin_test_environment import isolated_environment, preparation_environment  # noqa: E402


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit"])
def test_auth_location_is_captured_before_isolation_and_not_given_to_actors(tmp_path, monkeypatch, explicit):
    from agent_procutil import owned_process

    original = tmp_path / "original"
    credentials = original / "credentials"
    base = {
        "HOME": str(original), "USERPROFILE": str(original), "LOCALAPPDATA": str(original),
        "AGENT_BRIDGE_CONFIG_DIR": str(original / "service"),
        "COPILOT_EXTENSIONS_CONTEXT": "synthetic-participant",
        "GH_TOKEN": "synthetic-not-a-real-token",
        "UV_KEYRING_PROVIDER": "subprocess",
        "UV_INDEX_FIXTURE_PASSWORD": "synthetic-index-password",
    }
    if explicit:
        base["UV_CREDENTIALS_DIR"] = str(credentials)
    monkeypatch.setattr(policy, "_select_uv_executable", lambda uv: "selected-uv")
    calls = []

    def metadata(argv, **options):
        calls.append(argv)
        assert argv == ["selected-uv", "auth", "dir"]
        assert options["env"]["HOME"] == str(original)
        assert not {"GH_TOKEN", "UV_INDEX_FIXTURE_PASSWORD", "COPILOT_EXTENSIONS_CONTEXT",
                    "AGENT_BRIDGE_CONFIG_DIR"} & options["env"].keys()
        assert options["binary"] and options["capture"]
        assert options["timeout"] == 15
        assert options["max_stdout_bytes"] == 32768
        assert options["max_stderr_bytes"] == 16384
        assert options["budget"].cleanup_reserve == 5
        return subprocess.CompletedProcess(argv, 0, (str(credentials) + "\r\n").encode(), b"")

    monkeypatch.setattr(owned_process, "run_caller_owned", metadata)
    project = tmp_path / "project"
    project.mkdir()
    prepared = preparation_environment(base, tmp_path / "prepared", project, uv="caller-uv")
    assert len(calls) == 1
    assert prepared["UV_CREDENTIALS_DIR"] == str(credentials)
    assert prepared["UV_KEYRING_PROVIDER"] == "subprocess"
    assert prepared["UV_INDEX_FIXTURE_PASSWORD"] == "synthetic-index-password"
    assert "GH_TOKEN" not in prepared and "COPILOT_EXTENSIONS_CONTEXT" not in prepared
    assert Path(prepared["HOME"]).is_relative_to(tmp_path / "prepared")
    assert Path(prepared["AGENT_BRIDGE_CONFIG_DIR"]).is_relative_to(tmp_path / "prepared")
    actor_base = dict(prepared)
    if os.name == "nt":
        actor_base["uv_credentials_dir"] = str(original / "lowercase-auth")
        actor_base["Uv_CrEdEnTiAlS_DiR"] = str(original / "mixedcase-auth")
    untouched = dict(actor_base)
    for host_state in (False, True):
        actor = isolated_environment(
            actor_base, tmp_path / f"actor-{host_state}",
            allow_explicit_tiers=host_state, allow_host_state=host_state,
        )
        assert Path(actor["UV_CREDENTIALS_DIR"]).is_relative_to(tmp_path / f"actor-{host_state}")
        assert actor["UV_CREDENTIALS_DIR"] != prepared["UV_CREDENTIALS_DIR"]
        if os.name == "nt":
            assert [name for name in actor if name.casefold() == "uv_credentials_dir"] == ["UV_CREDENTIALS_DIR"]
    assert actor_base == untouched


@pytest.mark.parametrize("failure", [
    "unsupported", "empty", "relative", "multiple", "nul", "utf8", "overflow",
    "timeout", "text-output", "explicit-mismatch", "file", "stderr-overflow",
])
def test_unknown_auth_directory_metadata_refuses_without_echoing_inputs(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(policy, "_select_uv_executable", lambda uv: "selected-uv")
    directory = tmp_path / "auth"
    base = {"HOME": str(tmp_path), "GH_TOKEN": "synthetic-private-value"}
    output = (str(directory) + "\n").encode()
    code = 0
    if failure == "unsupported":
        code = 2
    elif failure == "empty":
        output = b""
    elif failure == "relative":
        output = b"relative/path\n"
    elif failure == "multiple":
        output += output
    elif failure == "nul":
        output += b"\x00"
    elif failure == "utf8":
        output = b"\xff"
    elif failure == "overflow":
        output = b"x" * 32769
    elif failure == "text-output":
        output = str(directory)
    elif failure == "explicit-mismatch":
        base["UV_CREDENTIALS_DIR"] = str(tmp_path / "another")
    elif failure == "file":
        directory.write_text("synthetic fixture", encoding="utf-8")

    def metadata(argv, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 15, output="synthetic-private-value")
        stderr = b"x" * 16385 if failure == "stderr-overflow" else b"synthetic-private-value"
        return subprocess.CompletedProcess(argv, code, output, stderr)

    with pytest.raises(policy.DependencyPolicyError, match="uv auth dir") as caught:
        preparation.capture_preparation_auth("fixture-uv", base, tmp_path, run_metadata=metadata)
    assert "synthetic-private-value" not in str(caught.value)


def test_unsupported_auth_metadata_prevents_install_preparation(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("auth_preparation_runner", ROOT / "tools/run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runner)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "cache")
    calls = []
    monkeypatch.setattr(runner, "_prepare_venv", lambda *args, **kwargs: calls.append("install"))

    def unsupported(*args, **kwargs):
        raise policy.DependencyPolicyError("selected UV lacks uv auth dir metadata")

    monkeypatch.setattr(preparation, "capture_preparation_auth", unsupported)
    with pytest.raises(policy.DependencyPolicyError, match="uv auth dir"):
        runner._ensure_venv("fixture", "fixture-uv", reinstall=False)
    assert calls == []


def test_auth_metadata_unconfirmed_cleanup_retains_owned_job(tmp_path, monkeypatch):
    from agent_procutil import owned_process
    from plugin_test_containment import UnreapedProcessError

    monkeypatch.setattr(policy, "_select_uv_executable", lambda uv: "selected-uv")
    job = object()
    failure = owned_process.OwnedCleanupError(None, job=job)

    def unsettled(*args, **kwargs):
        raise failure

    monkeypatch.setattr(owned_process, "run_caller_owned", unsettled)
    with pytest.raises(UnreapedProcessError) as caught:
        preparation_environment({}, tmp_path, tmp_path, uv="fixture-uv")
    assert caught.value.job is job
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize("windows", [False, True], ids=["posix-home", "windows-profile"])
@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit"])
def test_netrc_capture_uses_documented_location_without_reading_contents(tmp_path, monkeypatch, windows, explicit):
    base = {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "profile")}
    expected = tmp_path / ("profile" if windows else "home") / ".netrc"
    if explicit:
        expected = tmp_path / "selected.netrc"
        base["NETRC"] = str(expected)
    original = dict(base)

    def forbidden(*args, **kwargs):
        raise AssertionError("netrc contents must not be read")

    monkeypatch.setattr(Path, "open", forbidden)
    assert preparation.capture_preparation_netrc(base, windows=windows) == {"NETRC": str(expected)}
    assert base == original


@pytest.mark.parametrize("bad", ["missing-home", "relative-home", "relative-netrc", "directory", "control"])
def test_unqualified_netrc_location_refuses(tmp_path, bad):
    base = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    if bad == "missing-home":
        base.clear()
    elif bad == "relative-home":
        base.update(HOME="relative", USERPROFILE="relative")
    elif bad == "relative-netrc":
        base["NETRC"] = "relative.netrc"
    elif bad == "directory":
        base["NETRC"] = str(tmp_path)
    else:
        base["NETRC"] = str(tmp_path) + "\n"
    with pytest.raises(policy.DependencyPolicyError, match="NETRC"):
        preparation.capture_preparation_netrc(base)


@pytest.mark.skipif(os.environ.get("COPILOT_EXTENSIONS_UV_AUTH_QUALIFICATION") != "1",
                    reason="explicit native synthetic UV authentication qualification")
@pytest.mark.portfolio_tier("T2")
def test_selected_uv_default_and_explicit_store_survive_only_preparation(tmp_path, record_property):
    uv = shutil.which("uv")
    assert uv
    project = tmp_path / "project"
    project.mkdir()
    minimal = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
               if key in os.environ}
    original = isolated_environment(minimal, tmp_path / "original")
    original.pop("UV_CREDENTIALS_DIR")
    original["UV_OFFLINE"] = "1"

    def native(arguments, env):
        return subprocess.run(
            [uv, *arguments], env=env, cwd=project, capture_output=True, text=True,
            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    record_property("selected-uv", native(["--version"], original).stdout.strip())
    auth_dir = native(["auth", "dir"], original)
    assert auth_dir.returncode == 0
    directory = Path(auth_dir.stdout.strip()).resolve()
    assert directory.is_relative_to(tmp_path.resolve())
    secret = "fixture-only-not-a-real-secret"
    service = "https://fixture.invalid"
    login = native(["auth", "login", service, "--username", "fixture", "--password", secret], original)
    assert login.returncode == 0
    for explicit in (False, True):
        base = dict(original)
        if explicit:
            base["UV_CREDENTIALS_DIR"] = str(directory)
        prepared = preparation_environment(base, tmp_path / f"prepared-{explicit}", project, uv=uv)
        assert prepared["UV_CREDENTIALS_DIR"] == str(directory)
        assert Path(prepared["HOME"]).is_relative_to(tmp_path / f"prepared-{explicit}")
        token = native(["auth", "token", service, "--username", "fixture"], prepared)
        assert token.returncode == 0 and token.stdout.strip() == secret
        actor = isolated_environment(prepared, tmp_path / f"actor-{explicit}")
        assert Path(actor["UV_CREDENTIALS_DIR"]).is_relative_to(tmp_path / f"actor-{explicit}")
        absent = native(["auth", "token", service, "--username", "fixture"], actor)
        assert absent.returncode != 0


@pytest.mark.skipif(os.environ.get("COPILOT_EXTENSIONS_UV_AUTH_QUALIFICATION") != "1",
                    reason="explicit native synthetic netrc authentication qualification")
@pytest.mark.portfolio_tier("T2")
@pytest.mark.parametrize("explicit", [False, True], ids=["default-netrc", "explicit-netrc"])
def test_selected_uv_netrc_survives_only_preparation(tmp_path, record_property, explicit):
    uv = shutil.which("uv")
    assert uv
    minimal = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
               if key in os.environ}
    original = isolated_environment(minimal, tmp_path / "original")
    original.pop("NETRC", None)
    alternate = tmp_path / "unused-home"
    alternate.mkdir()
    original["HOME" if os.name == "nt" else "USERPROFILE"] = str(alternate)
    home = Path(original["USERPROFILE" if os.name == "nt" else "HOME"])
    netrc = home / ".netrc"
    netrc.write_text("machine 127.0.0.1 login fixture password fixture-password\n", encoding="utf-8")
    netrc.chmod(0o600)
    if explicit:
        original["NETRC"] = str(netrc)
    project = tmp_path / "project"
    project.mkdir()
    requirements = project / "requirements.in"
    requirements.write_text("fixture-netrc-scope==1.0\n", encoding="utf-8")
    wheel_name = "fixture_netrc_scope-1.0-py3-none-any.whl"
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        prefix = "fixture_netrc_scope-1.0.dist-info/"
        archive.writestr(prefix + "METADATA", "Metadata-Version: 2.3\nName: fixture-netrc-scope\nVersion: 1.0\n")
        archive.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(prefix + "RECORD", "")
    wheel = content.getvalue()
    scheme = "Ba" + "sic "
    encoded = base64.b64encode(b"fixture:fixture-password").decode("ascii")
    expected = scheme + encoded
    events = []

    class Index(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            authenticated = self.headers.get("Authorization") == expected
            events.append(authenticated)
            if not authenticated:
                self.send_response(401)
                self.send_header("WWW-Authenticate", scheme + 'realm="synthetic"')
                self.end_headers()
                return
            if self.path.startswith("/simple/fixture-netrc-scope/"):
                body = ('<html><a href="/files/' + wheel_name + '">' + wheel_name + "</a></html>").encode()
            elif self.path.startswith("/files/"):
                body = wheel
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/octet-stream" if body is wheel else "text/html")
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Index) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            index = f"http://127.0.0.1:{server.server_port}/simple"

            def compile_with(env, label):
                result = subprocess.run(
                    [uv, "pip", "compile", str(requirements), "--python", sys.executable,
                     "--default-index", index, "--no-config", "--no-cache", "--no-deps",
                     "--no-python-downloads", "--output-file", str(project / (label + ".txt"))],
                    env=env, cwd=project, capture_output=True, timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                return result.returncode

            version = subprocess.run(
                [uv, "--version"], env=original, capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            record_property("selected-uv", version.stdout.strip())
            assert compile_with(original, "original") == 0
            prepared = preparation_environment(original, tmp_path / "prepared", project, uv=uv)
            assert compile_with(prepared, "prepared") == 0
            for host_state in (False, True):
                actor = isolated_environment(
                    prepared, tmp_path / f"actor-{host_state}",
                    allow_explicit_tiers=host_state, allow_host_state=host_state,
                )
                if os.name == "nt":
                    actor = isolated_environment(
                        dict(actor, netrc=str(netrc)), tmp_path / f"mixed-actor-{host_state}",
                        allow_explicit_tiers=host_state, allow_host_state=host_state,
                    )
                    assert [key for key in actor if key.casefold() == "netrc"] == ["NETRC"]
                private_actor = tmp_path / f"{'mixed-actor' if os.name == 'nt' else 'actor'}-{host_state}"
                assert Path(actor["NETRC"]).is_relative_to(private_actor)
                previous = len(events)
                assert compile_with(actor, f"actor-{host_state}") != 0
                assert len(events) > previous and not any(events[previous:])
            assert any(events)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()
