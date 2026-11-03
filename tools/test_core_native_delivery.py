"""Core native inputs must remain bound to the released source composition."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest

from promotion_python_artifacts import PromotionArtifactError
from runtime_profile import ProfileError
from runtime_profile_cases import _record
from test_core_python_artifacts import _build
from test_installer_wheel_acquisition import (
    CERTIFICATE, FILENAME, IDENTITY, POLICY, feeds as feeds, listed, project,
)
from test_promotion_python_artifacts import _wheel_bytes


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
    assert calls == ["feed", "compile", "feed"]
    with zipfile.ZipFile(arguments["destination"]) as archive:
        profile = json.loads(archive.read("profile.json"))
        assert profile["component"] == "composition-core"
        assert archive.read("wheels.zip")
    assert result["profile_sha256"].startswith("sha256:")


@pytest.fixture
def tls_source(tmp_path, monkeypatch, feeds):
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
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    project(state, [listed(hashes={"sha256": digest})])
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
                download_origins=(), cutoffs={"fixture": datetime(2025, 1, 1, tzinfo=timezone.utc)},
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


def test_core_delivery_runs_native_resolver_against_local_tls(tmp_path, monkeypatch, tls_source):
    import governed_feed_trust

    uv = shutil.which("uv")
    git = shutil.which("git")
    if uv is None or git is None:
        pytest.skip("native uv and git are required")
    producer, wheels, manifest, state, _wheel, digest = tls_source
    git_environment = dict(os.environ) | {
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }

    def run_git(*arguments):
        return subprocess.run(
            [git, "-C", str(tmp_path), "-c", f"core.hooksPath={tmp_path / 'no-hooks'}",
             "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", *arguments],
            env=git_environment, capture_output=True, text=True, check=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout.strip()

    run_git("init", "-q")
    run_git("add", "-A")
    run_git("commit", "-qm", "fixture source")
    commit = run_git("rev-parse", "HEAD")
    manifest_path = tmp_path / "selected-core.json"
    manifest_path.write_bytes(json.dumps(manifest).encode())
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
    deadline = time.monotonic() + 120
    destination = tmp_path / "native-delivery.zip"
    result = producer.prepare_core_delivery(
        tmp_path, commit, commit, manifest_path, wheels,
        materials={}, material_paths={}, destination=destination, deadline=deadline, max_bytes=4 << 20,
        uv=uv, download_origins=(),
    )
    assert result["approval"] == "unattested"
    assert result["source_commit"] == commit
    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
    external = next(row for row in profile["wheels"] if row["name"] == "fixture")
    assert external["sha256"] == "sha256:" + digest
    assert any(path == "/simple/fixture/" for path, _ in state["requests"])
    assert any(path == "/files/" + FILENAME for path, _ in state["requests"])


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
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "--core-manifest" in result.stdout
