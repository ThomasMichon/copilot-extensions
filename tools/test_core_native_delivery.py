"""Core native inputs must remain bound to the released source composition."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from promotion_python_artifacts import PromotionArtifactError
from runtime_profile import ProfileError
from runtime_profile_cases import _record
from test_core_python_artifacts import _build
from test_installer_wheel_acquisition import (
    CERTIFICATE,
    FILENAME,
    IDENTITY,
    POLICY,
    listed,
    project,
)
from test_installer_wheel_acquisition import feeds as local_tls_feed  # noqa: F401
from test_promotion_python_artifacts import _wheel_bytes


def _fixture_git(root, git, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    })
    return subprocess.run(
        [git, "-C", str(root), "-c", f"core.hooksPath={root / 'no-hooks'}",
         "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", *arguments],
        env=environment, capture_output=True, text=True, check=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout.strip()


def test_git_fixture_ignores_foreign_repository_selectors(tmp_path, monkeypatch):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    foreign, fixture = tmp_path / "foreign", tmp_path / "fixture"
    foreign.mkdir()
    fixture.mkdir()
    _fixture_git(foreign, git, "init", "-q")
    sentinel = foreign / "sentinel.txt"
    sentinel.write_text("original")
    _fixture_git(foreign, git, "add", "-A")
    _fixture_git(foreign, git, "commit", "-qm", "foreign baseline")
    original_head = _fixture_git(foreign, git, "rev-parse", "HEAD")
    original_index = (foreign / ".git" / "index").read_bytes()
    sentinel.write_text("changed but not staged")
    (fixture / "selected.txt").write_text("fixture")
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign / ".git" / "index"))
    _fixture_git(fixture, git, "init", "-q")
    assert (fixture / ".git").is_dir()
    _fixture_git(fixture, git, "add", "-A")
    _fixture_git(fixture, git, "commit", "-qm", "fixture only")
    assert _fixture_git(foreign, git, "rev-parse", "HEAD") == original_head
    assert (foreign / ".git" / "index").read_bytes() == original_index


def test_core_projection_uses_the_manifest_roots_and_source_requirements(tmp_path, monkeypatch):
    from core_native_delivery import project_core_inputs

    _wheels, manifest, _ = _build(tmp_path, monkeypatch)
    roots, requirements = project_core_inputs(
        tmp_path, json.dumps(manifest).encode(), _record()["target"],
        deadline=time.monotonic() + 60,
    )
    assert roots == ["agent-pull-requests-service==1.0", "agent-sample==1.0"]
    assert requirements == []


def test_core_projection_refuses_changed_source_before_resolution(tmp_path, monkeypatch):
    from core_native_delivery import project_core_inputs

    _wheels, manifest, _ = _build(tmp_path, monkeypatch)
    source = tmp_path / "plugins" / "agent-sample" / "src" / "agent_sample" / "__init__.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(PromotionArtifactError, match="payload hash differs"):
        project_core_inputs(
            tmp_path, json.dumps(manifest).encode(), _record()["target"],
            deadline=time.monotonic() + 60,
        )


def test_core_projection_refuses_active_local_wheel_without_registry_substitution(tmp_path, monkeypatch):
    import core_native_delivery as producer

    _wheels, manifest, _ = _build(tmp_path, monkeypatch)
    original = producer.native.dependencies._toml(producer.ROOT / "worktree-manager" / "pyproject.toml")
    requirement = next(value for value in original["project"]["dependencies"] if value.startswith("dbus-next"))
    relative = original["tool"]["uv"]["sources"]["dbus-next"]["path"]
    manager = tmp_path / "worktree-manager"
    wheel = manager / relative
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes((producer.ROOT / "worktree-manager" / relative).read_bytes())
    (manager / "pyproject.toml").write_text(
        '[project]\nname = ' + json.dumps(original["project"]["name"]) + '\nversion = "1.0"\n'
        + 'dependencies = [' + json.dumps(requirement) + ']\n'
        + '[tool.uv.sources]\ndbus-next = {path = ' + json.dumps(relative) + '}\n',
    )
    monkeypatch.setattr(producer, "validate_source_binding", lambda *_args: None)
    target = deepcopy(_record()["target"])
    target["environment"].update(
        os_name="posix", sys_platform="linux", platform_system="Linux",
        platform_machine="x86_64", platform_release="6",
    )
    target["tags"] = ["py3-none-any"]
    with pytest.raises(ProfileError, match="native core input projection refused.*dbus-next"):
        producer.project_core_inputs(
            tmp_path, json.dumps(manifest).encode(), target, deadline=time.monotonic() + 60,
        )


def test_compile_failure_reports_bounded_sanitized_diagnostic(monkeypatch, tmp_path):
    import core_native_delivery as producer

    secret = "private-example-value"
    stderr = (
        "No solution: fixture>=2 conflicts with fixture<2\n"
        + "https://private.example.invalid/simple?token=" + secret + "\n"
        + "credential=" + secret + "\n" + "more details " * 200
    ).encode()
    monkeypatch.setattr(
        producer.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b"", stderr=stderr),
    )
    transaction = SimpleNamespace(argv=("fixture-uv",), cwd=tmp_path, environment={"FEED_TOKEN": secret})
    with pytest.raises(ProfileError) as failure:
        producer._run_compile(transaction, deadline=time.monotonic() + 60)
    message = str(failure.value)
    assert "fixture>=2" in message
    assert secret not in message and "private.example.invalid" not in message
    assert len(message) < 600


def test_compile_timeout_does_not_expose_command_or_output(monkeypatch, tmp_path):
    import core_native_delivery as producer

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["https://private.example.invalid/input"], timeout=1,
            output=b"private diagnostic",
        )

    monkeypatch.setattr(producer.subprocess, "run", timeout)
    transaction = SimpleNamespace(argv=("fixture-uv",), cwd=tmp_path, environment={})
    with pytest.raises(ProfileError, match="native core dependency resolution timed out") as failure:
        producer._run_compile(transaction, deadline=time.monotonic() + 60)
    assert "private" not in str(failure.value)
    assert failure.value.__suppress_context__


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    import core_native_delivery as producer

    wheels, manifest, _ = _build(tmp_path, monkeypatch)
    calls = []

    @contextmanager
    def retain_compile(uv, arguments, **options):
        output = arguments[arguments.index("--output-file") + 1]
        with open(output, "w", encoding="utf-8") as stream:
            stream.write('lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.10"\n')
        options["_projection"].extend(["original", "retained"])
        yield SimpleNamespace(argv=(uv,), cwd=options["cwd"], environment={})

    def feed(*_args, **_kwargs):
        calls.append("feed")
        return (
            {"policyId": "fixture-policy"},
            SimpleNamespace(
                policy_id="fixture-acquisition", feeds=(), download_origins=(), cutoffs={},
                context=None, proxies={},
            ),
        )

    monkeypatch.setattr(producer.native.dependencies, "retain_uv_compile", retain_compile)
    monkeypatch.setattr(producer.native, "_profile_feed", feed)
    monkeypatch.setattr(producer, "_run_compile", lambda *_args, **_kwargs: calls.append("compile"))
    monkeypatch.setattr(
        producer, "acquire_wheel_inputs",
        lambda *_args, **_kwargs: pytest.fail("no external wheels should be acquired"),
    )
    arguments = {
        "source": tmp_path, "source_record": {
            "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40,
        },
        "manifest_data": json.dumps(manifest).encode(), "wheel_dir": wheels,
        "target": _record()["target"], "uv": "fixture-uv", "download_origins": (),
        "materials": {}, "material_paths": {}, "destination": tmp_path / "delivery.zip",
        "deadline": time.monotonic() + 120, "max_bytes": 4 << 20,
    }
    return producer, arguments, calls


def test_core_delivery_composes_existing_profile_and_transport_owners(delivery):
    producer, arguments, calls = delivery
    result = producer._build_from_snapshot(**arguments)
    assert result["approval"] == "unattested"
    assert result["lock_source"] == "offline-cache"
    assert calls == ["feed", "compile", "feed"]
    with zipfile.ZipFile(arguments["destination"]) as archive:
        profile = json.loads(archive.read("profile.json"))
        assert profile["component"] == "composition-core"
        assert archive.read("wheels.zip")
    assert result["profile_sha256"].startswith("sha256:")


def test_core_delivery_excludes_first_party_names_before_resolution(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    expected = tuple(sorted(row["name"].replace("_", "-") for row in
                            json.loads(arguments["manifest_data"])["wheels"]))
    original_compile = producer.native.dependencies.retain_uv_compile
    original_feed = producer.native._profile_feed
    observed = []

    @contextmanager
    def compile_with_exclusions(*args, **kwargs):
        assert kwargs["excluded_local_packages"] == expected
        with original_compile(*args, **kwargs) as transaction:
            yield transaction

    def feed_with_exclusions(*args, **kwargs):
        assert kwargs["excluded_local_packages"] == expected
        observed.append(kwargs["wheels"])
        return original_feed(*args, **kwargs)

    monkeypatch.setattr(producer.native.dependencies, "retain_uv_compile", compile_with_exclusions)
    monkeypatch.setattr(producer.native, "_profile_feed", feed_with_exclusions)
    producer._build_from_snapshot(**arguments)
    assert observed == [[], []]


def test_core_delivery_uses_selected_native_lock_without_compiling(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    lock = arguments["source"] / "selected-pylock.toml"
    lock.write_text('lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.10"\n')
    arguments["native_lock"] = lock
    monkeypatch.setattr(
        producer, "_run_compile", lambda *_args, **_kwargs: pytest.fail("selected lock was re-resolved"),
    )
    result = producer._build_from_snapshot(**arguments)
    assert result["approval"] == "unattested" and result["lock_source"] == "selected"
    assert arguments["destination"].exists()


def test_core_delivery_rechecks_selected_lock_before_publication(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    lock = arguments["source"] / "selected-pylock.toml"
    lock.write_text('lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.10"\n')
    arguments["native_lock"] = lock
    original = producer.build_profile_delivery

    def mutate_after_delivery(*args, **kwargs):
        result = original(*args, **kwargs)
        lock.write_text("changed")
        return result

    monkeypatch.setattr(producer, "build_profile_delivery", mutate_after_delivery)
    with pytest.raises((ValueError, OSError)):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


def test_core_delivery_rejects_malformed_selected_lock_before_feed(delivery):
    producer, arguments, calls = delivery
    lock = arguments["source"] / "selected-pylock.toml"
    lock.write_text("not a native lock")
    arguments["native_lock"] = lock
    with pytest.raises(ProfileError):
        producer._build_from_snapshot(**arguments)
    assert not calls and not arguments["destination"].exists()


@pytest.fixture
def tls_source(tmp_path, monkeypatch, local_tls_feed):  # noqa: F811
    import core_native_delivery as producer
    import standalone_python_artifacts as standalone
    import test_core_python_artifacts as core_tests

    original_fixture = core_tests._fixture
    original_wheel = core_tests._wheel_bytes

    def fixture(root):
        source = original_fixture(root)
        project_file = root / "plugins" / "agent-sample" / "pyproject.toml"
        project_file.write_text(
            project_file.read_text().replace('"shared-core>=1"', '"shared-core>=1", "fixture>=1"'),
        )
        for resource in standalone.standalone_build_resources(
            standalone.COMPONENTS["pr-service"], source_root=root,
        ):
            if not any(resource.iterdir()):
                (resource / ".fixture").write_text("tracked fixture resource\n")
        return source

    def first_party_wheel(name, version, **options):
        if name == "agent_sample":
            metadata = f"{name}-{version}.dist-info/METADATA"
            options["members"][metadata] += b"Requires-Dist: fixture>=1\n"
        return original_wheel(name, version, **options)

    monkeypatch.setattr(core_tests, "_fixture", fixture)
    monkeypatch.setattr(core_tests, "_wheel_bytes", first_party_wheel)
    wheels, manifest, _ = core_tests._build(tmp_path, monkeypatch)
    wheel = _wheel_bytes(
        "fixture", "1.0", members={"fixture-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: fixture\nVersion: 1.0\nRequires-Python: >=3.9\n"
        )},
    )
    digest = hashlib.sha256(wheel).hexdigest()
    state = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    project(state, [listed(hashes={"sha256": digest}, size=len(wheel))])
    state["routes"]["/files/" + FILENAME] = (200, {}, wheel)
    return producer, wheels, manifest, state, wheel, digest


def test_core_delivery_acquires_external_wheel_over_local_tls(tmp_path, monkeypatch, tls_source):
    from installer_wheel_acquisition import WheelFeed

    producer, wheels, manifest, state, wheel, digest = tls_source

    @contextmanager
    def compile_lock(uv, arguments, **options):
        assert "fixture>=1" in Path(arguments[0]).read_text()
        lock = Path(arguments[arguments.index("--output-file") + 1])
        lock.write_text(
            'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.10"\n'
            '[[packages]]\nname = "fixture"\nversion = "1.0"\n'
            f'wheels = [{{url = "{state["origin"]}/files/{FILENAME}", '
            f'hashes = {{sha256 = "{digest}"}}}}]\n',
        )
        options["_projection"].extend(["original", "retained"])
        yield SimpleNamespace(argv=(uv,), cwd=options["cwd"], environment={})

    def feed(*_args, **_kwargs):
        return (
            {"policyId": POLICY},
            SimpleNamespace(
                policy_id=POLICY, feeds=(WheelFeed(IDENTITY, state["origin"] + "/simple"),),
                download_origins=(), cutoffs={"fixture": datetime(2025, 1, 1, tzinfo=timezone.utc)},  # noqa: UP017
                context=ssl.create_default_context(cafile=str(CERTIFICATE)), proxies={},
            ),
        )

    monkeypatch.setattr(producer.native.dependencies, "retain_uv_compile", compile_lock)
    monkeypatch.setattr(producer.native, "_profile_feed", feed)
    monkeypatch.setattr(producer, "_run_compile", lambda *_args, **_kwargs: None)
    loaded_packages = []
    engine = producer.native.source_inputs.engine
    original_procutil = engine._procutil

    def trusted_procutil(inputs):
        loaded_packages.extend(inputs.packages)
        return original_procutil(inputs)

    monkeypatch.setattr(engine, "_procutil", trusted_procutil)
    destination = tmp_path / "delivery.zip"
    result = producer._build_from_snapshot(
        tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
        json.dumps(manifest).encode(), wheels, _record()["target"], uv="fixture-uv",
        download_origins=(), materials={}, material_paths={}, destination=destination,
        deadline=time.monotonic() + 120, max_bytes=4 << 20,
    )
    assert result["approval"] == "unattested"
    assert loaded_packages == [producer.ROOT / "libs" / "agent-procutil"]
    assert [path for path, _ in state["requests"]] == ["/simple/fixture/", "/files/" + FILENAME]
    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
        external = next(row for row in profile["wheels"] if row["name"] == "fixture")
        assert external["sha256"] == "sha256:" + digest
        with zipfile.ZipFile(io.BytesIO(delivery.read("wheels.zip"))) as bundle:
            assert bundle.read("wheels/" + FILENAME) == wheel


def _configure_native_tls(tmp_path, monkeypatch, state):
    import governed_feed_trust

    for key in tuple(os.environ):
        if key.upper().startswith(("UV_", "PIP_")) or key.upper() in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        }:
            monkeypatch.delenv(key, raising=False)
    config = tmp_path / "operator" / "uv.toml"
    config.parent.mkdir()
    config.write_text(
        '[[index]]\nname = "fixture"\ndefault = true\n'
        f'url = "{state["origin"]}/simple"\nauthenticate = "never"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("UV_CONFIG_FILE", str(config))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("SSL_CERT_FILE", str(CERTIFICATE))
    monkeypatch.setenv("BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS", "127.0.0.1")
    monkeypatch.setattr(
        governed_feed_trust, "_opaque_index_identity",
        lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )


def _write_native_lock(path, state, files):
    rows = ['lock-version = "1.0"', 'created-by = "uv"', 'requires-python = ">=3.10"']
    for name, filename, digest in files:
        rows.extend([
            "[[packages]]", "name = " + json.dumps(name), 'version = "1.0"',
            "wheels = [{url = " + json.dumps(state["origin"] + "/files/" + filename)
            + ', hashes = {sha256 = "' + digest + '"}}]',
        ])
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_core_delivery_uses_selected_native_lock_against_local_tls(tmp_path, monkeypatch, tls_source):
    uv = shutil.which("uv")
    git = shutil.which("git")
    if uv is None or git is None:
        pytest.skip("native uv and git are required")
    producer, wheels, manifest, state, _wheel, digest = tls_source
    _fixture_git(tmp_path, git, "init", "-q")
    _fixture_git(tmp_path, git, "add", "-A")
    _fixture_git(tmp_path, git, "commit", "-qm", "fixture source")
    commit = _fixture_git(tmp_path, git, "rev-parse", "HEAD")
    manifest_path = tmp_path / "selected-core.json"
    manifest_path.write_bytes(json.dumps(manifest).encode())
    _configure_native_tls(tmp_path, monkeypatch, state)
    lock = _write_native_lock(
        tmp_path / "operator" / "pylock.toml", state, [("fixture", FILENAME, digest)],
    )
    monkeypatch.setattr(
        producer, "_run_compile", lambda *_args, **_kwargs: pytest.fail("selected lock was re-resolved"),
    )
    deadline = time.monotonic() + 120
    destination = tmp_path / "native-delivery.zip"
    result = producer.prepare_core_delivery(
        tmp_path, commit, commit, manifest_path, wheels,
        materials={}, material_paths={}, destination=destination, deadline=deadline, max_bytes=4 << 20,
        uv=uv, download_origins=(), native_lock=lock,
    )
    assert result["approval"] == "unattested"
    assert result["source_commit"] == commit
    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
    external = next(row for row in profile["wheels"] if row["name"] == "fixture")
    assert external["sha256"] == "sha256:" + digest
    assert any(path == "/simple/fixture/" for path, _ in state["requests"])
    assert any(path == "/files/" + FILENAME for path, _ in state["requests"])

    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))
    materials = tmp_path / "materials.json"
    materials.write_text("{}", encoding="utf-8")
    output = tmp_path / "command-delivery.zip"
    command = [
        sys.executable, "-I", str(Path(producer.__file__)),
        "--source-checkout", str(tmp_path), "--source-commit", commit, "--main-head", commit,
        "--core-manifest", str(manifest_path), "--wheel-dir", str(wheels),
        "--materials", str(materials), "--material-dir", str(tmp_path),
        "--native-lock", str(lock), "--uv", uv, "--output", str(output),
        "--timeout", "120", "--max-bytes", str(4 << 20),
    ]
    command_result = subprocess.run(
        command, env=dict(os.environ), capture_output=True, text=True, timeout=140, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert command_result.returncode == 0, command_result.stderr
    command_record = json.loads(command_result.stdout)
    assert command_record["approval"] == "unattested" and command_record["lock_source"] == "selected"
    with zipfile.ZipFile(output) as delivery:
        command_profile = json.loads(delivery.read("profile.json"))
    assert next(row for row in command_profile["wheels"] if row["name"] == "fixture")[
        "sha256"
    ] == "sha256:" + digest


def test_core_delivery_rejects_first_party_in_selected_lock_before_feed(tmp_path, monkeypatch, tls_source):
    from packaging.tags import sys_tags

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    _configure_native_tls(tmp_path, monkeypatch, state)
    lock = _write_native_lock(
        tmp_path / "operator" / "pylock.toml", state,
        [("agent-sample", "agent_sample-1.0-py3-none-any.whl", "a" * 64)],
    )
    deadline = time.monotonic() + 120
    target = {
        "device": "cpu",
        "environment": producer.native._query_marker_environment(Path(sys.executable), deadline=deadline),
        "tags": [str(tag) for tag in sys_tags()],
    }
    destination = tmp_path / "rejected.zip"
    with pytest.raises(ProfileError, match="overlaps its first-party wheel set"):
        producer._build_from_snapshot(
            tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
            json.dumps(manifest).encode(), wheels, target, uv=uv, download_origins=(),
            materials={}, material_paths={}, destination=destination, deadline=deadline, max_bytes=4 << 20,
            native_lock=lock,
        )
    assert not destination.exists()
    assert not state["requests"]


def test_core_resolution_does_not_contact_unapproved_redirect(
    tmp_path, monkeypatch, tls_source, local_tls_feed,  # noqa: F811
):
    from packaging.tags import sys_tags

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, _digest = tls_source
    foreign = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    foreign["routes"]["/foreign.whl"] = (200, {}, wheel)
    _configure_native_tls(tmp_path, monkeypatch, state)
    state["routes"]["/files/" + FILENAME] = (
        302, {"Location": foreign["origin"] + "/foreign.whl"}, b"",
    )
    deadline = time.monotonic() + 120
    target = {
        "device": "cpu",
        "environment": producer.native._query_marker_environment(Path(sys.executable), deadline=deadline),
        "tags": [str(tag) for tag in sys_tags()],
    }
    with pytest.raises(ValueError):
        producer._build_from_snapshot(
            tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
            json.dumps(manifest).encode(), wheels, target, uv=uv, download_origins=(),
            materials={}, material_paths={}, destination=tmp_path / "rejected.zip",
            deadline=deadline, max_bytes=4 << 20,
        )
    assert not state["requests"]
    assert not foreign["requests"]


def test_core_selected_lock_rejects_unapproved_download_redirect(
    tmp_path, monkeypatch, tls_source, local_tls_feed,  # noqa: F811
):
    from packaging.tags import sys_tags

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    foreign = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    foreign["routes"]["/foreign.whl"] = (200, {}, wheel)
    state["routes"]["/files/" + FILENAME] = (
        302, {"Location": foreign["origin"] + "/foreign.whl"}, b"",
    )
    _configure_native_tls(tmp_path, monkeypatch, state)
    lock = _write_native_lock(
        tmp_path / "operator" / "pylock.toml", state, [("fixture", FILENAME, digest)],
    )
    deadline = time.monotonic() + 120
    target = {
        "device": "cpu",
        "environment": producer.native._query_marker_environment(Path(sys.executable), deadline=deadline),
        "tags": [str(tag) for tag in sys_tags()],
    }
    with pytest.raises(ValueError):
        producer._build_from_snapshot(
            tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
            json.dumps(manifest).encode(), wheels, target, uv=uv, download_origins=(),
            materials={}, material_paths={}, destination=tmp_path / "rejected.zip",
            deadline=deadline, max_bytes=4 << 20, native_lock=lock,
        )
    assert [path for path, _ in state["requests"]] == ["/simple/fixture/", "/files/" + FILENAME]
    assert not foreign["requests"]


def test_core_selected_lock_refuses_unreachable_wheel(tmp_path, monkeypatch, tls_source):
    from packaging.tags import sys_tags

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, digest = tls_source
    unused = _wheel_bytes("unused", "1.0")
    filename = "unused-1.0-py3-none-any.whl"
    unused_digest = hashlib.sha256(unused).hexdigest()
    state["routes"]["/simple/unused/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps({
            "meta": {"api-version": "1.1"}, "name": "unused", "files": [{
                "filename": filename, "url": "/files/" + filename,
                "hashes": {"sha256": unused_digest}, "upload-time": "2020-01-01T00:00:00Z",
            }],
        }).encode(),
    )
    state["routes"]["/files/" + filename] = (200, {}, unused)
    _configure_native_tls(tmp_path, monkeypatch, state)
    lock = _write_native_lock(
        tmp_path / "operator" / "pylock.toml", state,
        [("fixture", FILENAME, digest), ("unused", filename, unused_digest)],
    )
    deadline = time.monotonic() + 120
    target = {
        "device": "cpu",
        "environment": producer.native._query_marker_environment(Path(sys.executable), deadline=deadline),
        "tags": [str(tag) for tag in sys_tags()],
    }
    destination = tmp_path / "rejected.zip"
    with pytest.raises(ProfileError, match="unreachable wheels"):
        producer._build_from_snapshot(
            tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
            json.dumps(manifest).encode(), wheels, target, uv=uv, download_origins=(),
            materials={}, material_paths={}, destination=destination,
            deadline=deadline, max_bytes=4 << 20, native_lock=lock,
        )
    assert not destination.exists()


def test_core_delivery_refuses_untrusted_feed_before_compiling(delivery, monkeypatch):
    producer, arguments, calls = delivery
    monkeypatch.setattr(
        producer.native, "_profile_feed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ProfileError("untrusted feed")),
    )
    with pytest.raises(ProfileError, match="untrusted feed"):
        producer._build_from_snapshot(**arguments)
    assert not calls and not arguments["destination"].exists()


def test_core_delivery_rechecks_source_before_publishing(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    source = arguments["source"] / "plugins" / "agent-sample" / "src" / "agent_sample" / "__init__.py"
    monkeypatch.setattr(
        producer, "_run_compile",
        lambda *_args, **_kwargs: source.write_text("VALUE = 2\n", encoding="utf-8"),
    )
    with pytest.raises(PromotionArtifactError, match="payload hash differs"):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


def test_core_delivery_waits_for_retained_compiler_cleanup(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    original = producer.native.dependencies.retain_uv_compile

    @contextmanager
    def fail_on_exit(*args, **kwargs):
        with original(*args, **kwargs) as transaction:
            yield transaction
        raise RuntimeError("retained compiler cleanup failed")

    monkeypatch.setattr(producer.native.dependencies, "retain_uv_compile", fail_on_exit)
    with pytest.raises(RuntimeError, match="retained compiler cleanup failed"):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


def test_core_delivery_refuses_changed_feed_identity(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    identities = iter(("before", "after"))

    def feed(*_args, **_kwargs):
        return (
            {"policyId": "fixture-policy"},
            SimpleNamespace(policy_id=next(identities), feeds=(), download_origins=(), cutoffs={},
                            context=None, proxies={}),
        )

    monkeypatch.setattr(producer.native, "_profile_feed", feed)
    with pytest.raises(ProfileError, match="feed identity changed"):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


def _selected_material(arguments, name="notice.txt"):
    path = arguments["source"] / "selected-notice"
    content = b"selected notice"
    path.write_bytes(content)
    wheel = json.loads(arguments["manifest_data"])["wheels"][0]
    arguments["materials"] = {name: {
        "kind": "notice", "wheels": [wheel["sha256"]],
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(), "size": len(content),
    }}
    arguments["material_paths"] = {name: path}
    return path


def test_core_delivery_rejects_bad_material_before_compiling(delivery):
    producer, arguments, calls = delivery
    _selected_material(arguments, "../notice")
    with pytest.raises(ValueError):
        producer._build_from_snapshot(**arguments)
    assert not calls and not arguments["destination"].exists()


def test_core_delivery_rechecks_material_until_publication(delivery, monkeypatch):
    producer, arguments, _calls = delivery
    material = _selected_material(arguments)
    original = producer.build_profile_delivery

    def mutate_after_delivery(*args, **kwargs):
        result = original(*args, **kwargs)
        material.write_bytes(b"changed notice")
        return result

    monkeypatch.setattr(producer, "build_profile_delivery", mutate_after_delivery)
    with pytest.raises((ValueError, OSError)):
        producer._build_from_snapshot(**arguments)
    assert not arguments["destination"].exists()


def test_core_delivery_command_loads_without_ambient_python_paths(tmp_path):
    script = Path(__file__).with_name("core_native_delivery.py")
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-I", str(script), "--help"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "--core-manifest" in result.stdout and "--native-lock" in result.stdout
