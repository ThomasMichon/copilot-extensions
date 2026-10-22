from __future__ import annotations

import importlib.util
import base64
import hashlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "worktree-manager" / "scripts" / "picker-snapshot" / "install.py"
SPEC = importlib.util.spec_from_file_location("seasoned_npm_install", PATH)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
STRONG_SRI = "sha512-" + base64.b64encode(hashlib.sha512(b"fixture").digest()).decode()
WEAK_SRI = "sha1-" + base64.b64encode(bytes.fromhex("a" * 40)).decode()


def lock():
    return {"lockfileVersion": 3, "packages": {"": {}, "node_modules/demo": {
        "version": "1.0.0", "integrity": STRONG_SRI, "resolved": "https://registry.example/demo.tgz",
    }}}


def metadata(timestamp=None):
    return {"time": {"1.0.0": timestamp}, "dist": {
        "integrity": STRONG_SRI, "tarball": "https://registry.example/demo.tgz",
    }}


@pytest.mark.parametrize("seconds", [604800, 604801])
def test_exact_boundary_including_locked_transitive_and_optional(seconds):
    project = lock()
    project["packages"]["node_modules/demo/node_modules/transitive"] = dict(
        project["packages"]["node_modules/demo"], optional=True,
    )
    calls = []

    def query(name, version):
        calls.append(name)
        return metadata((NOW - timedelta(seconds=seconds)).isoformat())

    assert installer.audit_lock(project, query, now=NOW) == 2
    assert calls == ["demo", "transitive"]


@pytest.mark.parametrize("timestamp", [
    None, "missing", "2026-10-01", (NOW - timedelta(seconds=604799)).isoformat(),
    (NOW + timedelta(days=1)).isoformat(),
])
def test_unknown_naive_future_and_new_timestamps_refuse(timestamp):
    with pytest.raises(installer.AdmissionError, match="timestamp"):
        installer.audit_lock(lock(), lambda *args: metadata(timestamp), now=NOW)


@pytest.mark.parametrize("field,value", [
    ("integrity", "different"), ("resolved", "https://another.example/demo.tgz"), ("link", True),
])
def test_locked_integrity_and_source_are_bound(field, value):
    project = lock()
    project["packages"]["node_modules/demo"][field] = value
    with pytest.raises(installer.AdmissionError):
        installer.audit_lock(project, lambda *args: metadata("2020-01-01T00:00:00Z"), now=NOW)


def test_metadata_lookup_uses_npm_configuration_and_never_overrides_registry(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        from subprocess import CompletedProcess
        import json
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, json.dumps(metadata("2020-01-01T00:00:00Z")), "")

    monkeypatch.setattr(installer.subprocess, "run", run)
    installer.read_metadata(["node", "npm-cli.js"], tmp_path, "demo", "1.0.0")
    assert "--registry" not in calls[0][0]
    assert calls[0][1]["timeout"] == 30
    assert calls[0][1]["cwd"] == tmp_path


def test_actual_attributable_npm_cli_can_be_located():
    import shutil
    if not shutil.which("npm"):
        pytest.skip("npm unavailable")
    command = installer.npm_command()
    assert Path(command[1]).is_file()


@pytest.mark.parametrize("legacy_integrity", [None, WEAK_SRI])
def test_legacy_metadata_binds_strong_lock_and_cleans_archive(monkeypatch, tmp_path, legacy_integrity):
    import json
    from subprocess import CompletedProcess
    calls = []
    data = metadata("2020-01-01T00:00:00Z")
    data["dist"]["integrity"] = legacy_integrity
    data["dist"]["shasum"] = "a" * 40

    def run(command, **kwargs):
        calls.append(command)
        if "view" in command:
            return CompletedProcess(command, 0, json.dumps(data), "")
        temporary = Path(command[command.index("--pack-destination") + 1])
        (temporary / "fixture.tgz").write_bytes(b"fixture")
        return CompletedProcess(command, 0, json.dumps([{
            "shasum": "a" * 40, "integrity": STRONG_SRI,
        }]), "")

    monkeypatch.setattr(installer.subprocess, "run", run)
    admitted = installer.read_metadata(["node", "npm-cli.js"], tmp_path, "demo", "1.0.0")
    assert admitted["dist"]["integrity"] == STRONG_SRI
    assert installer.audit_lock(lock(), lambda *args: admitted, now=NOW) == 1
    pack = calls[1]
    assert "--ignore-scripts" in pack
    assert not Path(pack[pack.index("--pack-destination") + 1]).exists()


def test_matching_sha1_metadata_and_lock_cannot_skip_strong_verification():
    project = lock()
    project["packages"]["node_modules/demo"]["integrity"] = WEAK_SRI
    release = metadata("2020-01-01T00:00:00Z")
    release["dist"]["integrity"] = WEAK_SRI
    with pytest.raises(installer.AdmissionError, match="locked bytes"):
        installer.audit_lock(project, lambda *args: release, now=NOW)


@pytest.mark.parametrize("value", [
    "sha512-fixture", "md5-Zml4dHVyZQ==", "sha256-%%%",
    "sha512-" + base64.b64encode(b"short").decode(),
])
def test_malformed_or_unsupported_integrity_is_refused(value):
    with pytest.raises(installer.AdmissionError, match="integrity"):
        installer.integrity_digests(value)


def test_default_registry_mapping_is_explicit_not_an_arbitrary_source_escape():
    project = lock()
    project["packages"]["node_modules/demo"]["resolved"] = "https://registry.npmjs.org/demo.tgz"
    query = lambda *args: metadata("2020-01-01T00:00:00Z")
    with pytest.raises(installer.AdmissionError, match="source"):
        installer.audit_lock(project, query, now=NOW)
    assert installer.audit_lock(project, query, now=NOW, replace_default_registry=True) == 1
    project["packages"]["node_modules/demo"]["resolved"] = "https://unapproved.example/demo.tgz"
    with pytest.raises(installer.AdmissionError, match="source"):
        installer.audit_lock(project, query, now=NOW, replace_default_registry=True)


@pytest.mark.parametrize("timestamp,generic_project,legacy_metadata,mutation", [
    (timestamp, generic, legacy, None)
    for timestamp in ["2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z", None]
    for generic, legacy in [(False, False), (True, False), (True, True)]
] + [
    ("2020-01-01T00:00:00Z", False, False, mutation)
    for mutation in [
        "package-lock.json", "package.json", ".npmrc", "install", "health", "publish",
        "publish-package-lock.json", "publish-package.json", "publish-.npmrc",
    ]
])
def test_real_npm_admission_before_locked_install(
    tmp_path, monkeypatch, capsys, timestamp, generic_project, legacy_metadata, mutation,
):
    import base64
    import hashlib
    import io
    import json
    import tarfile
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    try:
        installer.npm_command()
    except installer.AdmissionError:
        pytest.skip("native npm unavailable")
    artifacts, releases, downloads, requests, audit_requests = {}, {}, [], [], []
    started = time.monotonic()
    names = ["@resvg/resvg-js", "fixture-transitive"]
    for name in names:
        package = {"name": name, "version": "1.0.0", "main": "index.js"}
        if name == names[0]:
            package["dependencies"] = {names[1]: "1.0.0"}
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path, body in [
                ("package/package.json", json.dumps(package).encode()),
                ("package/index.js", b"module.exports = {};\n"),
            ]:
                member = tarfile.TarInfo(path)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        artifacts[name.rsplit("/", 1)[-1] + ".tgz"] = buffer.getvalue()
        releases[name] = package

    class Registry(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            audit_requests.append(self.path)
            self.send_error(501, "advisory requests are outside locked installation")

        def do_GET(self):
            from urllib.parse import unquote
            requests.append((round(time.monotonic() - started, 2), self.path))
            name = unquote(self.path).lstrip("/")
            if name in releases:
                body = json.dumps({
                    "name": name, "dist-tags": {"latest": "1.0.0"},
                    "versions": {"1.0.0": releases[name]},
                    "time": {} if timestamp is None else {"1.0.0": timestamp},
                }).encode()
                content_type = "application/json"
            elif name in artifacts:
                downloads.append(name)
                body, content_type = artifacts[name], "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    url = f"http://127.0.0.1:{server.server_port}/"
    packages = {"": {"name": "fixture-root", "version": "1.0.0",
                     "dependencies": {names[0]: "1.0.0"}}}
    for name in names:
        filename = name.rsplit("/", 1)[-1] + ".tgz"
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(artifacts[filename]).digest()).decode()
        releases[name]["dist"] = {"tarball": url + filename, "integrity": integrity}
        if legacy_metadata:
            digest = hashlib.sha1(artifacts[filename]).digest()
            releases[name]["dist"] = {
                "tarball": url + filename,
                "integrity": "sha1-" + base64.b64encode(digest).decode(),
                "shasum": digest.hex(),
            }
        packages["node_modules/" + name] = {
            **releases[name], "resolved": url + filename, "integrity": integrity,
        }
    (tmp_path / "package.json").write_text(json.dumps(packages[""]), encoding="utf-8")
    original = json.dumps({"name": "fixture-root", "version": "1.0.0",
                           "lockfileVersion": 3, "packages": packages})
    (tmp_path / "package-lock.json").write_text(original, encoding="utf-8")
    empty_config = tmp_path / "empty.npmrc"
    empty_config.touch()
    global_config = tmp_path / "global.npmrc"
    global_config.touch()
    monkeypatch.setattr(installer, "__file__", str(tmp_path / "install.py"))
    monkeypatch.delenv("npm_config_registry", raising=False)
    (tmp_path / ".npmrc").write_text(
        "registry=" + url + "\naudit=true\nfund=false\nupdate-notifier=false\n", encoding="utf-8",
    )
    monkeypatch.setenv("npm_config_userconfig", str(empty_config))
    monkeypatch.setenv("npm_config_globalconfig", str(global_config))
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "cache"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = tmp_path / "node_modules" / "retained"
    previous.mkdir(parents=True)
    (previous / "healthy").write_text("previous dependency closure", encoding="utf-8")
    call = installer.subprocess.call
    rename = Path.rename
    snapshots = []

    def install_call(command, **kwargs):
        snapshot = Path(kwargs["cwd"])
        snapshots.append(snapshot)
        assert snapshot != tmp_path
        if "ci" in command:
            if mutation in {"package-lock.json", "package.json", ".npmrc"}:
                target = tmp_path / mutation
                if mutation == ".npmrc":
                    target.write_text("registry=http://127.0.0.1:1/\n", encoding="utf-8")
                else:
                    replaced = json.loads(target.read_text(encoding="utf-8"))
                    if mutation == "package-lock.json":
                        replaced["packages"]["node_modules/" + names[0]]["version"] = "2.0.0"
                    else:
                        replaced["dependencies"][names[0]] = "2.0.0"
                    target.write_text(json.dumps(replaced), encoding="utf-8")
            assert (snapshot / "package-lock.json").read_text(encoding="utf-8") == original
            assert json.loads((snapshot / "package.json").read_text(encoding="utf-8")) == packages[""]
            result = call(command, **kwargs)
            return 29 if mutation == "install" and result == 0 else result
        if mutation == "health":
            command = [*command, "missing-selected-module"]
        return call(command, **kwargs)

    def publish(source, destination):
        if mutation == "publish" and source.name == "node_modules" and source.parent in snapshots:
            raise OSError("publication denied")
        result = rename(source, destination)
        if (mutation and mutation.startswith("publish-") and source.name == "node_modules"
                and source.parent in snapshots):
            target = tmp_path / mutation.removeprefix("publish-")
            target.write_text("replaced during publication\n", encoding="utf-8")
        return result

    if not generic_project:
        monkeypatch.setattr(installer.subprocess, "call", install_call)
        monkeypatch.setattr(Path, "rename", publish)
    try:
        arguments = (["--directory", str(tmp_path), "--health-module", names[0],
                      "--health-module", names[1]] if generic_project else [])
        if generic_project:
            try:
                completed = subprocess.run(
                    [sys._base_executable, str(PATH), *arguments],
                    capture_output=True, text=True, timeout=45, creationflags=installer.HEADLESS,
                )
            except subprocess.TimeoutExpired as exc:
                pytest.fail(f"{exc}; registry requests: {requests}")
            result, error = completed.returncode, completed.stderr
        else:
            result = installer.main(arguments)
            error = capsys.readouterr().err
        assert audit_requests == [], "locked installation must not query advisory endpoints"
        if timestamp == "2020-01-01T00:00:00Z" and mutation is None:
            assert result == 0, error
            assert set(downloads) == set(artifacts), error
            assert (tmp_path / "node_modules" / "@resvg" / "resvg-js" / "package.json").is_file()
            assert not previous.exists()
        elif mutation is not None:
            assert result != 0
            assert set(downloads) == set(artifacts), error
            assert (previous / "healthy").read_text(encoding="utf-8") == "previous dependency closure"
            assert not (tmp_path / "node_modules" / "@resvg").exists()
            if mutation in {"package-lock.json", "package.json", ".npmrc"}:
                assert "changed during installation" in error
            elif mutation.startswith("publish-"):
                assert "changed during publication" in error
            elif mutation == "health":
                assert "selected dependency health check failed" in error
            elif mutation == "publish":
                assert "publication denied" in error
        else:
            assert result != 0
            assert "unseasoned release timestamp" in error
            assert downloads == []
            assert (previous / "healthy").is_file()
        if mutation not in {"package-lock.json", "publish-package-lock.json"}:
            assert (tmp_path / "package-lock.json").read_text(encoding="utf-8") == original
        assert all(not path.exists() for path in snapshots)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
