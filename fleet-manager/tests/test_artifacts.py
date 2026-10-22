import copy
import errno
import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import fleet_manager.artifacts as artifacts
from fleet_manager.planning import PlanError, digest


def inputs(tmp_path):
    release = {
        "schema": "fleet-manager.release", "version": 1, "id": "fixture-r1",
        "components": {}, "roles": {
            "client": ["cli"], "gateway": ["cli", "core"], "node": ["executor"],
        },
    }
    sources = {"schema": "fleet-manager.artifact-sources", "version": 1, "components": {}}
    for name, kind in (("cli", "client"), ("core", "control"), ("executor", "executor")):
        data = (name + "-fixture-bytes").encode()
        path = tmp_path / (name + ".artifact")
        path.write_bytes(data)
        release["components"][name] = {
            "kind": kind, "artifact_sha256": hashlib.sha256(data).hexdigest(),
            "api": {"min": 1, "max": 1}, "data_schemas": [1],
        }
        sources["components"][name] = str(path)
    return release, sources


@pytest.mark.parametrize("role,names", [
    ("client", ["cli"]), ("gateway", ["cli", "core"]), ("node", ["executor"]),
])
def test_selected_actual_bytes_and_truthful_output(tmp_path, role, names):
    release, sources = inputs(tmp_path)
    before = copy.deepcopy((release, sources))
    result = artifacts.verify_role_artifacts(release, sources, role=role)
    assert set(result) == {
        "schema", "version", "release", "release_digest", "role", "status",
        "components", "total_bytes", "evidence", "authenticity_verified",
        "activation_authorized", "live_readiness_verified", "mutations_performed",
    }
    assert result["schema"] == "fleet-manager.artifact-preflight" and result["version"] == 1
    assert result["release_digest"] == digest(release)
    assert [row["component"] for row in result["components"]] == names
    assert result["total_bytes"] == sum(row["bytes"] for row in result["components"])
    assert result["status"] == "integrity-match"
    assert result["evidence"] == "explicit-local-file-bytes"
    assert all(result[key] is False for key in (
        "authenticity_verified", "activation_authorized", "live_readiness_verified",
        "mutations_performed",
    ))
    assert str(tmp_path) not in json.dumps(result)
    assert (release, sources) == before


def test_client_never_opens_unselected_artifacts(tmp_path, monkeypatch):
    release, sources = inputs(tmp_path)
    sources["components"]["core"] = str(tmp_path / "missing")
    sources["components"]["executor"] = str(tmp_path)
    opened = []
    original = artifacts._open_artifact

    def track(path):
        opened.append(path.name)
        return original(path)

    monkeypatch.setattr(artifacts, "_open_artifact", track)
    artifacts.verify_role_artifacts(release, sources, role="client")
    assert opened == ["cli.artifact"]
    sources["components"] = {"cli": sources["components"]["cli"]}
    artifacts.verify_role_artifacts(release, sources, role="client")
    with pytest.raises(PlanError, match="not supplied"):
        artifacts.verify_role_artifacts(release, sources, role="gateway")


@pytest.mark.parametrize("change", [
    "tamper", "wrong-digest", "missing", "empty", "directory", "unknown-source",
    "unknown-field", "bool-version", "null-path", "empty-path", "nul-path",
    "long-path", "invalid-role", "invalid-release",
])
def test_refusals_are_explicit_and_path_free(tmp_path, change):
    release, sources = inputs(tmp_path)
    path = tmp_path / "private-artifact-name"
    path.write_bytes(b"private-artifact-content")
    role = "client"
    if change == "tamper":
        path = tmp_path / "cli.artifact"
        path.write_bytes(b"private-artifact-content")
    elif change == "wrong-digest":
        release["components"]["cli"]["artifact_sha256"] = "f" * 64
    elif change == "missing":
        sources["components"]["cli"] = str(path.with_name("missing-private-artifact"))
    elif change == "empty":
        (tmp_path / "cli.artifact").write_bytes(b"")
    elif change == "directory":
        sources["components"]["cli"] = str(tmp_path)
    elif change == "unknown-source":
        sources["components"]["private-component"] = str(path)
    elif change == "unknown-field":
        sources["private-field"] = "private-value"
    elif change == "bool-version":
        sources["version"] = True
    elif change == "null-path":
        sources["components"]["cli"] = None
    elif change == "empty-path":
        sources["components"]["cli"] = ""
    elif change == "nul-path":
        sources["components"]["cli"] = "private\0path"
    elif change == "long-path":
        sources["components"]["cli"] = "x" * 4097
    elif change == "invalid-role":
        role = "private-role"
    else:
        release["roles"]["client"].append("core")
    with pytest.raises(PlanError) as error:
        artifacts.verify_role_artifacts(release, sources, role=role)
    assert "private" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_exact_stream_and_aggregate_limits(tmp_path, monkeypatch):
    release, sources = inputs(tmp_path)
    size = (tmp_path / "cli.artifact").stat().st_size
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", size)
    monkeypatch.setattr(artifacts, "MAX_TOTAL_BYTES", size)
    assert artifacts.verify_role_artifacts(release, sources, role="client")["total_bytes"] == size
    with pytest.raises(PlanError, match="bounds"):
        artifacts.verify_role_artifacts(release, sources, role="gateway")
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", size - 1)
    with pytest.raises(PlanError, match="bounds"):
        artifacts.verify_role_artifacts(release, sources, role="client")


def test_path_timestamp_difference_does_not_invent_content_change(tmp_path, monkeypatch):
    release, sources = inputs(tmp_path)
    original = artifacts._file_stat

    def path_metadata(path):
        info = original(path)
        return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                               st_size=info.st_size, st_mtime_ns=info.st_mtime_ns - 100,
                               st_ctime_ns=info.st_ctime_ns - 100)

    monkeypatch.setattr(artifacts, "_file_stat", path_metadata)
    assert artifacts.verify_role_artifacts(release, sources, role="gateway")["status"] == "integrity-match"


@pytest.mark.parametrize("platform,version", [("nt", (3, 13)), ("nt", (3, 11)), ("posix", (3, 13))])
def test_content_stamp_uses_stable_windows_creation_and_posix_change_time(monkeypatch, platform, version):
    monkeypatch.setattr(artifacts, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(artifacts, "sys", SimpleNamespace(version_info=version))
    info = SimpleNamespace(st_size=10, st_mtime_ns=20, st_ctime_ns=30, st_birthtime_ns=40)
    baseline = artifacts._content_stamp(info)
    info.st_ctime_ns += 1
    assert (artifacts._content_stamp(info) == baseline) == (platform == "nt" and version >= (3, 12))
    current = artifacts._content_stamp(info)
    info.st_mtime_ns += 1
    assert artifacts._content_stamp(info) != current
    current = artifacts._content_stamp(info)
    info.st_size += 1
    assert artifacts._content_stamp(info) != current


def test_actual_64_mib_limit_and_bounded_stream_reads(tmp_path, monkeypatch):
    release, sources = inputs(tmp_path)
    path = tmp_path / "cli.artifact"
    block = b"x" * artifacts.CHUNK_BYTES
    expected = hashlib.sha256()
    with path.open("wb") as stream:
        for _ in range(artifacts.MAX_ARTIFACT_BYTES // len(block)):
            stream.write(block)
            expected.update(block)
    release["components"]["cli"]["artifact_sha256"] = expected.hexdigest()
    original = artifacts.os.fdopen
    reads = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            reads.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(artifacts.os, "fdopen",
                        lambda fd, mode, **kwargs: BoundedReader(original(fd, mode, **kwargs)))
    result = artifacts.verify_role_artifacts(release, sources, role="client")
    assert result["total_bytes"] == 64 * 1024 * 1024
    assert max(reads) == 64 * 1024
    with path.open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(PlanError, match="bounds"):
        artifacts.verify_role_artifacts(release, sources, role="client")


@pytest.mark.parametrize("parent", [False, True])
def test_native_link_is_refused_before_open(tmp_path, monkeypatch, parent):
    release, sources = inputs(tmp_path)
    link = tmp_path / "linked"
    try:
        link.symlink_to(tmp_path if parent else tmp_path / "cli.artifact",
                        target_is_directory=parent)
    except OSError as exc:
        pytest.skip(f"native symlink unavailable: {exc.errno}")
    sources["components"]["cli"] = str(link / "cli.artifact" if parent else link)
    monkeypatch.setattr(artifacts.os, "open", lambda *_: pytest.fail("linked artifact opened"))
    with pytest.raises(PlanError, match="link or reparse"):
        artifacts.verify_role_artifacts(release, sources, role="client")


@pytest.mark.parametrize("change", ["replace", "grow", "truncate"])
def test_file_changed_between_check_and_open_is_refused(tmp_path, monkeypatch, change):
    release, sources = inputs(tmp_path)
    original = artifacts._open_artifact

    def race(path):
        if change == "replace":
            replacement = path.with_name("replacement")
            replacement.write_bytes(b"replacement")
            os.replace(replacement, path)
        elif change == "grow":
            with path.open("ab") as stream:
                stream.write(b"additional")
        else:
            path.write_bytes(b"x")
        return original(path)

    monkeypatch.setattr(artifacts, "_open_artifact", race)
    with pytest.raises(PlanError, match="changed during preflight"):
        artifacts.verify_role_artifacts(release, sources, role="client")


def test_reparse_and_nonregular_metadata_refused(tmp_path, monkeypatch):
    release, sources = inputs(tmp_path)
    path = tmp_path / "cli.artifact"
    original = artifacts.Path.lstat

    class Reparse:
        st_mode = 0o100600
        st_file_attributes = 0x400

    monkeypatch.setattr(artifacts.Path, "lstat",
                        lambda self: Reparse() if self == path else original(self))
    with pytest.raises(PlanError, match="link or reparse"):
        artifacts.verify_role_artifacts(release, sources, role="client")


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative path")
def test_drive_relative_source_refused(tmp_path):
    release, sources = inputs(tmp_path)
    sources["components"]["cli"] = "C:private-artifact"
    with pytest.raises(PlanError, match="drive-relative") as error:
        artifacts.verify_role_artifacts(release, sources, role="client")
    assert "private" not in str(error.value)


@pytest.mark.parametrize("change", ["grow", "truncate"])
def test_real_file_mutation_during_streaming_is_refused(tmp_path, monkeypatch, change):
    release, sources = inputs(tmp_path)
    original = artifacts.os.fdopen
    path = tmp_path / "cli.artifact"
    monkeypatch.setattr(artifacts, "CHUNK_BYTES", 4)

    class MutatingReader:
        def __init__(self, stream):
            self.stream = stream
            self.first = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            data = self.stream.read(size)
            if self.first:
                self.first = False
                if change == "grow":
                    with path.open("ab") as writer:
                        writer.write(b"appended")
                else:
                    path.write_bytes(b"x")
            return data

    monkeypatch.setattr(artifacts.os, "fdopen",
                        lambda fd, mode, **kwargs: MutatingReader(original(fd, mode, **kwargs)))
    with pytest.raises(PlanError, match="changed during preflight|byte bounds|unchanged ordinary file"):
        artifacts.verify_role_artifacts(release, sources, role="client")


@pytest.mark.skipif(os.name != "nt", reason="native Windows read-sharing fence")
def test_native_reader_fences_writers_and_deletion_but_allows_readers(tmp_path):
    path = tmp_path / "artifact"
    path.write_bytes(b"unchanged artifact")
    with artifacts._reader(path) as reader:
        with artifacts._reader(path) as second:
            assert second.read() == b"unchanged artifact"
        for mutation in (
            lambda: path.write_bytes(b"foreign"),
            path.unlink,
        ):
            with pytest.raises(OSError) as error:
                mutation()
            assert error.value.errno == errno.EACCES
            assert error.value.winerror in (None, 32)
        assert reader.read() == b"unchanged artifact"
    path.write_bytes(b"writer allowed after custody")
    assert path.read_bytes() == b"writer allowed after custody"
    with path.open("r+b"):
        with pytest.raises(PermissionError) as error:
            with artifacts._reader(path):
                pytest.fail("an existing writer was admitted")
        assert error.value.winerror == 32


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="native FIFO unavailable")
def test_fifo_refused_before_open(tmp_path, monkeypatch):
    release, sources = inputs(tmp_path)
    path = tmp_path / "fifo"
    os.mkfifo(path)
    sources["components"]["cli"] = str(path)
    monkeypatch.setattr(artifacts.os, "open", lambda *_: pytest.fail("FIFO opened"))
    with pytest.raises(PlanError, match="not an ordinary file"):
        artifacts.verify_role_artifacts(release, sources, role="client")


def test_actual_installed_cli_outside_checkout_and_tamper(tmp_path):
    release, sources = inputs(tmp_path)
    release_path, sources_path = tmp_path / "release.json", tmp_path / "sources.json"
    release_path.write_text(json.dumps(release))
    sources_path.write_text(json.dumps(sources))
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    command = [sys.executable, "-I", "-m", "fleet_manager.artifacts",
               "--release", str(release_path), "--sources", str(sources_path), "--role", "client"]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=10, **flags)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "integrity-match"
    (tmp_path / "cli.artifact").write_bytes(b"tampered-private-value")
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=10, **flags)
    assert result.returncode == 1 and not result.stdout
    assert "digest mismatch" in result.stderr
    assert "private" not in result.stderr and str(tmp_path) not in result.stderr


def test_cli_invalid_json_and_missing_input_are_errors(tmp_path, capsys):
    release, sources = inputs(tmp_path)
    release_path, sources_path = tmp_path / "release.json", tmp_path / "sources.json"
    release_path.write_text(json.dumps(release))
    sources_path.write_text('{"schema":"private","schema":"private-duplicate"}')
    args = ["--release", str(release_path), "--sources", str(sources_path), "--role", "client"]
    assert artifacts.main(args) == 1
    output = capsys.readouterr()
    assert not output.out and "private" not in output.err
    sources_path.unlink()
    assert artifacts.main(args) == 1
    output = capsys.readouterr()
    assert not output.out and "could not be read" in output.err
