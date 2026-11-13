"""Fresh payload-only traversal of the actual source/build/constructor owners."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from test_runtime_source_build import (
    build_only_feed, options, packaging_feed, source_with_process_owner,
)
from test_runtime_source_build import configured_source as configured_source  # noqa: F401
from test_runtime_source_build import feeds as feeds  # noqa: F401
from test_runtime_source_build import adopter as adopter  # noqa: F401
from test_runtime_source_build import target as target  # noqa: F401
from test_runtime_source_build import tmp_path as tmp_path  # noqa: F401
from test_runtime_feed_configuration import environment
from test_runtime_source_lock import plan
from test_installer_wheel_acquisition import CERTIFICATE
from test_installer_packaging_seed import engine as engine  # noqa: F401
from test_installer_packaging_seed import seed, selected_seed
from test_runtime_profile_bundle import inputs as inputs  # noqa: F401
from test_runtime_profile_bundle import build as build_profile_bundle
from test_runtime_profile_bundle import isolated_manager as isolated_manager  # noqa: F401
from test_runtime_profile_delivery import produce
from test_runtime_profile_delivery import selected as selected  # noqa: F401
from promotion_unit_tests.test_runtime_profile_wheels import _record


def _toolkit(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "payload_toolkit_packager", repo / "libs" / "payload-invocation" / "runtime_admission.py",
    )
    assert spec and spec.loader
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    toolkit = tmp_path / "payload" / "_runtime_admission"
    for relative, content in packager.expected_files(repo).items():
        path = toolkit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
        if path.suffix == ".sh":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return toolkit


def test_copied_toolkit_validates_actual_bundle_and_sources_without_publishers(tmp_path):
    from promotion_python_artifacts import bundle_wheels
    from test_promotion_python_artifacts import _manifest, _STATIC_BUILD

    toolkit = _toolkit(tmp_path)
    candidate = tmp_path / "candidate"
    plugin = candidate / "plugins" / "demo"
    (plugin / "src" / "demo").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        _STATIC_BUILD + '[project]\nname = "demo"\nversion = "1.0"\n',
        encoding="utf-8",
    )
    (plugin / "src" / "demo" / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    (candidate / "tools").mkdir()
    (candidate / "tools" / "build_python_artifacts.py").write_text(
        "raise AssertionError('candidate tooling must never execute')", encoding="utf-8",
    )
    wheels, bundles = tmp_path / "wheels", tmp_path / "bundles"
    manifest = _manifest(wheels, candidate_root=candidate)
    bundles.mkdir()
    entry = bundle_wheels(manifest, wheels, bundles)
    script = """
import hashlib, importlib.abc, json, pathlib, subprocess, sys, zipfile
root, candidate, bundles = map(pathlib.Path, sys.argv[1:4])
entry = json.loads(sys.argv[4])
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
class RejectPublication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"publish_python_artifacts", "promotion_python_artifacts", "promote_release"}:
            raise AssertionError("bundle validation imported a publisher: " + fullname)
sys.meta_path.insert(0, RejectPublication())
import artifact_bundle_validation as validation
import artifact_release_discovery as discovery
import runtime_artifact_inputs as artifact_inputs
import build_python_artifacts as builder
assert pathlib.Path(validation.__file__).resolve().is_relative_to(root)
assert pathlib.Path(artifact_inputs.__file__).resolve().is_relative_to(root)
assert pathlib.Path(builder.__file__).resolve().is_relative_to(root)
original_roots = builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR
bundle = bundles / entry["filename"]
data = bundle.read_bytes()
downloaded = bundles / "downloaded"
downloaded.mkdir()
index = {
    "schema": discovery.INDEX_SCHEMA, "schema_version": 2,
    "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
    "admission": {"signer_digest": "c" * 40, "source_digest": "c" * 40,
                  "source_ref": "refs/heads/main"},
}
asset = {"id": 3, "name": bundle.name, "digest": "sha256:" + entry["sha256"],
         "size": len(data), "state": "uploaded"}
calls, verified = [], []
def runner(argv, **options):
    assert argv == ["api", "repos/example/plugins/releases/assets/3",
                    "-H", "Accept: application/octet-stream"]
    assert options == {"verification": True, "binary": True}
    calls.append(argv)
    return subprocess.CompletedProcess(argv, 0, data, b"")
def verify(path, **identity):
    assert path.read_bytes() == data
    assert identity == {"repository": "example/plugins", **index["admission"]}
    verified.append(identity)
arguments = dict(index=index, assets={bundle.name: asset}, repository="example/plugins",
                 component="demo", version="1.0", candidate_root=candidate,
                 runner=runner, verify_subject=verify)
manifest, digest = discovery.read_selected_source_artifact(downloaded, **arguments)
assert manifest["artifact_id"] == entry["artifact_id"]
with zipfile.ZipFile(bundle) as archive:
    assert digest == "sha256:" + hashlib.sha256(archive.read("manifest.json")).hexdigest()
assert len(calls) == len(verified) == 1
assert discovery.read_selected_source_artifact(
    downloaded, **{**arguments, "component": "actually-absent"},
) is None
assert len(calls) == 1, "an absent component downloaded another artifact"
for changes in (
    {"version": "2.0"},
    {"assets": {bundle.name: {**asset, "digest": "sha256:" + "d" * 64}}},
):
    try:
        discovery.read_selected_source_artifact(downloaded, **{**arguments, **changes})
    except validation.PromotionArtifactError:
        pass
    else:
        raise AssertionError("conflicting artifact evidence became fallback")
assert len(calls) == 1
assert (builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR) == original_roots
(candidate / "plugins" / "demo" / "src" / "demo" / "__init__.py").write_text("CHANGED = True")
try:
    discovery.read_selected_source_artifact(downloaded, **arguments)
except validation.PromotionArtifactError as exc:
    assert "payload hash differs" in str(exc), str(exc)
else:
    raise AssertionError("bundle accepted changed independently acquired source")
assert (builder.REPO, builder.PLUGINS_DIR, builder.LIBS_DIR) == original_roots
print("copied-bundle-source-validation-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(toolkit), str(candidate),
         str(bundles), json.dumps(entry)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "copied-bundle-source-validation-ok"


def test_copied_toolkit_discovers_the_source_bound_channel_without_publishers(tmp_path):
    toolkit = _toolkit(tmp_path)
    script = """
import hashlib, importlib.abc, json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
class RejectPublication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            "publish_python_artifacts", "promotion_python_artifacts",
            "promote_release", "promotion_artifact_readiness", "promotion_release_evidence",
        }:
            raise AssertionError("discovery imported a publication owner: " + fullname)
sys.meta_path.insert(0, RejectPublication())
import artifact_release_discovery as discovery
assert pathlib.Path(discovery.__file__).resolve().is_relative_to(root)
dev, main, tree = "a" * 40, "b" * 40, "c" * 40
tag = "python-artifacts-" + dev
name = "agent-example-1.0-" + "d" * 64 + ".zip"
subject = json.dumps({
    "schema": discovery.INDEX_SCHEMA, "schema_version": 2,
    "dev_head": dev, "content_tree": tree,
    "admission": {"signer_digest": main, "source_digest": main, "source_ref": "refs/heads/main"},
    "artifacts": [{"filename": name, "sha256": "d" * 64}],
}).encode()
assets = [
    {"name": discovery.INDEX_NAME, "id": 2, "state": "uploaded",
     "digest": "sha256:" + hashlib.sha256(subject).hexdigest(), "size": len(subject)},
    {"name": name, "id": 3, "state": "uploaded", "digest": "sha256:" + "d" * 64, "size": 1},
]
release = {"id": 1, "tag_name": tag, "draft": False, "prerelease": True, "assets": assets}
calls, verified = [], []
failure = None
def runner(argv, **options):
    calls.append((argv, options))
    assert argv[0] == "api", "discovery attempted a publication command"
    endpoint = next(value for value in argv[1:] if value.startswith("repos/"))
    if endpoint.endswith("/releases/tags/" + tag):
        if failure in ("absence", "permission"):
            status = 404 if failure == "absence" else 403
            return subprocess.CompletedProcess(argv, 1, "", "gh: refused (HTTP " + str(status) + ")")
        value = release
    elif endpoint.endswith("/git/ref/tags/" + tag):
        value = {"object": {"type": "commit", "sha": dev}}
    elif endpoint.endswith("/releases/1/assets?per_page=100"):
        value = [assets]
    elif endpoint.endswith("/releases/assets/2"):
        assert options == {"verification": True, "binary": True}
        return subprocess.CompletedProcess(argv, 0, subject if failure != "digest" else b"changed", b"")
    else:
        raise AssertionError("unexpected discovery endpoint: " + endpoint)
    return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
def verify(path, **identity):
    assert path.read_bytes() == subject
    assert identity == {
        "repository": "example/plugins", "signer_digest": main,
        "source_digest": main, "source_ref": "refs/heads/main",
    }
    verified.append(identity)
def ancestor(first, second):
    assert (first, second) == (main, "e" * 40)
    return failure != "ancestry"
for failure in (None, "absence", "permission", "digest", "ancestry"):
    directory = root.parent / ("discovery-" + str(failure))
    directory.mkdir()
    before = len(verified)
    arguments = dict(
        repository="example/plugins", dev_head=dev, main_before="e" * 40,
        content_tree=tree, is_ancestor=ancestor, runner=runner, verify_subject=verify,
    )
    if failure in ("permission", "digest", "ancestry"):
        try:
            discovery.read_admitted_source_index(directory, **arguments)
        except discovery.PromotionArtifactError:
            pass
        else:
            raise AssertionError("failed evidence was treated as fallback: " + failure)
        assert len(verified) == before
    else:
        selected = discovery.read_admitted_source_index(directory, **arguments)
        if failure == "absence":
            assert selected is None and not list(directory.iterdir()) and len(verified) == before
        else:
            assert selected == (json.loads(subject), {row["name"]: row for row in assets})
            assert [path.name for path in directory.iterdir()] == [discovery.INDEX_NAME]
            assert len(verified) == before + 1
assert all(not any(value.endswith("/releases/assets/3") for value in argv) for argv, _ in calls)
print("copied-source-bound-discovery-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(toolkit)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "copied-source-bound-discovery-ok"


def test_copied_toolkit_retains_a_delivery_without_checkout_imports(tmp_path, selected):
    toolkit = _toolkit(tmp_path)
    bundle = tmp_path / "delivery.zip"
    result = produce(selected, bundle)
    profile = tmp_path / "selected-profile.json"
    profile.write_text(json.dumps(selected[0]), encoding="utf-8")
    script = """
import importlib.abc, json, pathlib, sys, time
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
blocked = {"promotion_python_artifacts", "publish_python_artifacts", "promote_release"}
class RejectRepositoryAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError("unexpected repository adapter: " + fullname)
sys.meta_path.insert(0, RejectRepositoryAdapters())
import runtime_profile_delivery as delivery
assert pathlib.Path(delivery.__file__).resolve().is_relative_to(root)
record = json.loads(pathlib.Path(sys.argv[2]).read_text())
with delivery.retain_profile_delivery(
    record, pathlib.Path(sys.argv[3]), delivery_sha256=sys.argv[4],
    max_bytes=1_000_000, max_files=10, max_snapshot_bytes=10_000_000,
    deadline=time.monotonic() + 30,
) as retained:
    assert len(retained.wheels.archives) == 2
    assert retained.materials["source.tar.gz"].read() == b"independently qualified source archive"
    retained.wheels.verify()
assert not blocked.intersection(sys.modules)
print("copied-delivery-intake-ok")
"""
    observed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(toolkit), str(profile), str(bundle), result["sha256"]],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert observed.stdout.strip() == "copied-delivery-intake-ok"

def test_copied_toolkit_maps_real_main_history_before_channel_discovery(tmp_path):
    toolkit = _toolkit(tmp_path)
    script = """
import hashlib, importlib.abc, json, os, pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
class RejectPublication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"publish_python_artifacts", "promotion_python_artifacts", "promote_release"}:
            raise AssertionError("source mapping imported a publisher: " + fullname)
sys.meta_path.insert(0, RejectPublication())
import artifact_release_discovery as discovery
repo = root.parent / "acquired"
repo.mkdir()
def git(*arguments):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", *arguments],
        cwd=repo, capture_output=True, text=True, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout.strip()
git("init", "-b", "main")
(repo / "content.txt").write_text("actual frozen source")
git("add", "-A")
git("commit", "-m", "development source")
dev = git("rev-parse", "HEAD")
tree = git("rev-parse", "HEAD^{tree}")
state = repo / ".github" / "release-pipeline-state.json"
state.parent.mkdir()
state.write_text(json.dumps({
    "schema": "copilot-extensions.release-pipeline-state", "version": 1,
    "last_promotion": {"dev_head": dev, "main_before": dev,
                       "release_id": dev, "tag": "promote-dev-" + dev},
}))
coverage = state.parent / "coverage-baselines"
coverage.mkdir()
(coverage / "packet.json").write_text("{}")
git("add", "-A")
git("commit", "-m", "generated Main")
main = git("rev-parse", "HEAD")
tag = "python-artifacts-" + dev
name = "agent-example-1.0-" + "d" * 64 + ".zip"
subject = json.dumps({
    "schema": discovery.INDEX_SCHEMA, "schema_version": 2,
    "dev_head": dev, "content_tree": tree,
    "admission": {"signer_digest": dev, "source_digest": dev, "source_ref": "refs/heads/main"},
    "artifacts": [{"filename": name, "sha256": "d" * 64}],
}).encode()
assets = [
    {"name": discovery.INDEX_NAME, "id": 2, "state": "uploaded",
     "digest": "sha256:" + hashlib.sha256(subject).hexdigest(), "size": len(subject)},
    {"name": name, "id": 3, "state": "uploaded", "digest": "sha256:" + "d" * 64, "size": 1},
]
calls, verified = [], []
failure = None
def runner(argv, **options):
    calls.append(argv)
    assert argv[0] == "api"
    endpoint = next(value for value in argv[1:] if value.startswith("repos/"))
    if endpoint.endswith("/releases/tags/" + tag):
        if failure is not None:
            return subprocess.CompletedProcess(argv, 1, "", "gh: refused (HTTP " + str(failure) + ")")
        value = {"id": 1, "tag_name": tag, "draft": False, "prerelease": True, "assets": assets}
    elif endpoint.endswith("/git/ref/tags/" + tag):
        value = {"object": {"type": "commit", "sha": dev}}
    elif endpoint.endswith("/releases/1/assets?per_page=100"):
        value = [assets]
    elif endpoint.endswith("/releases/assets/2"):
        return subprocess.CompletedProcess(argv, 0, subject, b"")
    else:
        raise AssertionError("unexpected channel: " + endpoint)
    return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
def verify(path, **identity):
    assert path.read_bytes() == subject
    verified.append(identity)
for failure in (None, 404, 403):
    directory = root.parent / ("mapped-" + str(failure))
    directory.mkdir()
    arguments = dict(
        repository="example/plugins", repo=repo, main_commit=main,
        deadline=time.monotonic() + 20, runner=runner, verify_subject=verify,
    )
    if failure == 403:
        try:
            discovery.discover_promoted_source(directory, **arguments)
        except discovery.PromotionArtifactError:
            pass
        else:
            raise AssertionError("permission failure became source fallback")
    else:
        result = discovery.discover_promoted_source(directory, **arguments)
        assert (result is None) == (failure == 404)
        if result is not None:
            assert result[0]["dev_head"] == dev and result[0]["content_tree"] == tree
assert len(verified) == 1
assert all("python-artifacts-" + main not in " ".join(call) for call in calls)
print("copied-real-history-discovery-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(toolkit)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "copied-real-history-discovery-ok"

def test_copied_toolkit_acquires_actual_origin_before_packaging_or_source_fallback(
    adopter, configured_source, tmp_path,
):
    toolkit = _toolkit(tmp_path)
    from test_runtime_profile_policy import owned_policy

    owned_policy(adopter[0], adopter[1], allowSourceFallback=False)
    script = """
import importlib.abc, json, pathlib, sys, time
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
class RejectSemanticTooling(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {
            "packaging", "runtime_profile", "runtime_profile_policy", "runtime_source_lock",
            "publish_python_artifacts", "promotion_python_artifacts", "promote_release",
        }:
            raise AssertionError("origin acquisition required semantic/publisher tooling: " + fullname)
sys.meta_path.insert(0, RejectSemanticTooling())
from runtime_source_authentication import retain_installation_origin
installation = json.loads(sys.argv[2])
for key in ("payload_root", "legacy_root", "copilot_home", "project_root"):
    if installation.get(key) is not None:
        installation[key] = pathlib.Path(installation[key])
with retain_installation_origin(
    installation=installation, deadline=time.monotonic() + 60,
) as origin:
    record = origin.record()
    assert record["mode"] == "origin" and record["trigger"] is None
    assert origin.acquired.commit == origin.identity.source_commit == sys.argv[3]
    assert record["source"] == origin.identity.document()
    origin.verify()
    prepared = origin.payload
assert not prepared.exists()
assert "packaging" not in sys.modules
print("copied-stdlib-origin-acquisition-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(toolkit),
         json.dumps(adopter[1], default=str), configured_source[2]],
        cwd=tmp_path, capture_output=True, text=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "copied-stdlib-origin-acquisition-ok"

def test_copied_stdlib_origin_bootstraps_admitted_tools_and_rechecks_seed_revocation(
    adopter, configured_source, target, feeds, tmp_path,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    toolkit = _toolkit(tmp_path)
    script = """
import hashlib, importlib.abc, json, os, pathlib, shutil, subprocess, sys, time
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
class RejectAmbientSemantics(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {
            "packaging", "runtime_profile", "runtime_profile_policy", "runtime_source_lock",
        }:
            raise AssertionError("bootstrap imported ambient semantics: " + fullname)
sys.meta_path.insert(0, RejectAmbientSemantics())
import agent_procutil
import runtime_admission_bootstrap as bootstrap
sys.path.insert(0, sys.argv[4])
import runtime_input_verifier as verification
from runtime_source_authentication import retain_installation_origin
from installer_wheel_acquisition import AcquisitionLimits
from installer_wheel_inputs import WheelLimits
from installer_packaging_seed import SeedLimits
from runtime_profile_errors import ProfileError
bootstrap._opaque_index_identity = lambda value: hashlib.sha256(value.encode()).hexdigest()
installation = json.loads(sys.argv[2])
for key in ("payload_root", "legacy_root", "copilot_home", "project_root"):
    if installation.get(key) is not None:
        installation[key] = pathlib.Path(installation[key])
environment = json.loads(sys.argv[3])
deadline = time.monotonic() + 100
denied = set()
observed = False
with retain_installation_origin(installation=installation, deadline=deadline) as origin:
    runtime_root = pathlib.Path(origin.policy()["owner"]["runtimeRoot"])
    assert not (runtime_root / ".source-build-tools").exists()
    try:
        with bootstrap.retain_origin_tooling(
            origin, installation=installation, uv=pathlib.Path(shutil.which("uv")),
            python=pathlib.Path(sys.executable), env=environment, deadline=deadline,
            limits=SeedLimits(16_384, 32 << 20, 1 << 20),
            acquisition_limits=AcquisitionLimits(
                max_packages=1, max_requests=10, max_redirects=3, max_candidates_per_project=10,
                max_index_bytes=16_384, max_total_metadata_bytes=32_768,
                max_wheel_bytes=1 << 20, max_closure_bytes=1 << 20, max_temporary_bytes=4 << 20,
            ),
            wheel_limits=WheelLimits(1 << 20, 4 << 20, 1 << 20, 64 << 10, 100, 10, 32 << 10),
            procutil=agent_procutil, revoked_inputs=lambda: frozenset(denied),
        ) as ready:
            assert ready.slot.parent.parent == runtime_root / ".source-build-tools"
            receipt = json.loads((ready.slot / ".runtime-input-origins.json").read_text())["wheel_inputs"]
            assert set(receipt["distributions"]) == {"packaging"}
            assert origin.record()["mode"] == "origin" and origin.record()["trigger"] is None
            python = ready.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            check = '''
import importlib.metadata, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
import _runtime_admission
_runtime_admission.activate()
assert {d.metadata["Name"] for d in importlib.metadata.distributions()} == {"packaging"}
from runtime_profile import ProfileError, validate_profile
from runtime_source_authentication import borrow_installation_origin, runtime_source
import runtime_input_verifier as verification
def refuse_acquisition(*args, **kwargs):
    raise AssertionError("borrowed origin reacquired its exclusive lease")
runtime_source.acquire = refuse_acquisition
installation = json.loads(sys.argv[2])
for key in ("payload_root", "legacy_root", "copilot_home", "project_root"):
    if installation.get(key) is not None:
        installation[key] = pathlib.Path(installation[key])
origin = json.loads(sys.argv[3])
client = verification.current_client(json.loads(sys.argv[4]))
signal = pathlib.Path(sys.argv[5])
try:
    with borrow_installation_origin(
        pathlib.Path(origin["payload"]), origin["record"], origin["policy"],
        installation=installation, verify_owner=client.check,
        deadline=json.loads(sys.argv[4])["deadline"],
    ) as borrowed:
        borrowed.verify()
        assert borrowed.acquired is None
        assert borrowed.record()["mode"] == "origin"
        assert borrowed.identity.document() == origin["record"]["source"]
        signal.touch()
        borrowed.verify()
except verification.VerificationRefused:
    assert signal.exists(), "origin was refused before its successful live check"
else:
    raise AssertionError("borrowed origin ignored current seed revocation")
try:
    borrowed.verify()
except ProfileError as error:
    assert "custody is closed" in str(error)
else:
    raise AssertionError("borrowed origin escaped its invocation lifetime")
finally:
    verification.close_clients()
try:
    validate_profile({})
except ProfileError:
    pass
else:
    raise AssertionError("semantic validator did not execute")
print("admitted-semantic-tools-ok")
'''
            signal = root.parent / "revoke-current-seed"
            def verify_live():
                if signal.exists():
                    denied.add(receipt["distributions"]["packaging"]["sha256"])
                ready.verify()
            with verification.LiveInputVerifier(verify_live, deadline=deadline) as live:
                child = dict(os.environ)
                child["PYTHONPATH"] = str(pathlib.Path(sys.argv[4]))
                command = [
                    bootstrap.dependencies._native_retained_path(python), "-I", "-B", "-c",
                    "import sys;sys.path.insert(0,sys.argv.pop(1));" + check,
                    sys.argv[4], str(root), json.dumps(installation, default=str),
                    json.dumps({"payload": str(origin.payload), "record": origin.record(),
                                "policy": origin.policy()}),
                    json.dumps(live.identity), str(signal),
                ]
                result = verification.wait_for_settlement(command, child, verifier=live)
                assert result.returncode == 0
                assert isinstance(live.failure, ProfileError), live.failure
            assert "packaging" not in sys.modules
            observed = True
    except ProfileError as error:
        assert observed and "revoked" in str(error), str(error)
    else:
        raise AssertionError("current seed revocation was ignored at context exit")
assert not (runtime_root / "current-version").exists()
assert not (runtime_root / ".source-build-tools" / "current-version").exists()
print("copied-stdlib-origin-tooling-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(toolkit),
         json.dumps(adopter[1], default=str), json.dumps(env),
         str(Path(__file__).resolve().parents[1] / "plugins" / "agent-worktrees" / "scripts")],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("copied-stdlib-origin-tooling-ok")



def test_copied_toolkit_retains_both_isolated_environments(tmp_path, isolated_manager):  # noqa: F811
    toolkit = _toolkit(tmp_path)
    record, paths = isolated_manager
    transport = tmp_path / "wheels.zip"
    built = build_profile_bundle(
        record, paths, transport, max_wheels=3, layout="manager-isolated",
    )
    bundle = tmp_path / "delivery.zip"
    result = produce((record, transport, built["sha256"], {}, {}), bundle, layout="manager-isolated")
    profile = tmp_path / "selected-profile.json"
    profile.write_text(json.dumps(record), encoding="utf-8")
    script = """
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
import runtime_profile_delivery as delivery
assert pathlib.Path(delivery.__file__).resolve().is_relative_to(root)
record = json.loads(pathlib.Path(sys.argv[2]).read_text())
with delivery.retain_profile_delivery(
    record, pathlib.Path(sys.argv[3]), delivery_sha256=sys.argv[4],
    max_bytes=1_000_000, max_files=10, max_snapshot_bytes=10_000_000,
    deadline=time.monotonic() + 30,
) as retained:
    assert retained.layout == "manager-isolated"
    assert set(retained.environments) == {"shared", "manager"}
    assert len(retained.wheels.archives) == 3
    retained.wheels.verify()
print("copied-isolated-delivery-ok")
"""
    observed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(toolkit), str(profile), str(bundle), result["sha256"]],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert observed.stdout.strip() == "copied-isolated-delivery-ok"


def test_admitted_seed_runs_payload_local_semantic_validation(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    toolkit = _toolkit(tmp_path)
    record = tmp_path / "profile.json"
    record.write_text(json.dumps(_record()), encoding="utf-8")
    with selected_seed(
        adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
    ) as (_state, arguments, _checks, _rejected):
        with seed.retain_packaging_seed(**arguments) as tooling:
            python = tooling.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            script = """
import hashlib, importlib.metadata, json, pathlib, sys, time, zipfile
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
assert {d.metadata["Name"] for d in importlib.metadata.distributions()} == {"packaging"}
import agent_procutil
assert pathlib.Path(agent_procutil.__file__).resolve().is_relative_to(root)
from installer_wheel_inputs import WheelError
from installer_wheel_snapshot import retain_wheel_bundle, open_wheel_bundle_snapshot
workspace = pathlib.Path(sys.argv[2]).parent / "bundle-custody"
workspace.mkdir()
bundle = workspace / "wheels.zip"
name, content = "example-1-py3-none-any.whl", b"opaque selected wheel bytes"
with zipfile.ZipFile(bundle, "w") as archive:
    archive.writestr("wheels/" + name, content)
original = bundle.read_bytes()
expected = {name: "sha256:" + hashlib.sha256(content).hexdigest()}
limits = dict(
    bundle_sha256="sha256:" + hashlib.sha256(original).hexdigest(),
    max_bundle_bytes=len(original), max_wheels=1, max_archive_bytes=len(content),
    max_total_bytes=len(content), max_snapshot_bytes=2 * len(content),
    deadline=time.monotonic() + 10,
)
owner = workspace / "caller-owner"
owner.mkdir(mode=0o700)
for context in (
    retain_wheel_bundle(bundle, expected, **limits),
    open_wheel_bundle_snapshot(owner, bundle, expected, **limits),
):
    with context as snapshot:
        assert snapshot.archives[name].read() == content
        snapshot.verify()
for changed in (
    {"bundle_sha256": "sha256:" + "0" * 64},
    {"max_total_bytes": len(content) - 1},
):
    try:
        with retain_wheel_bundle(bundle, expected, **(limits | changed)):
            raise AssertionError("copied toolkit accepted invalid transport evidence")
    except WheelError:
        pass
assert bundle.read_bytes() == original
from runtime_profile import ProfileError, validate_profile
record = json.loads(pathlib.Path(sys.argv[2]).read_text())
profile = validate_profile(record)
assert profile.record() == record
record["wheels"].pop()
try:
    validate_profile(record)
except ProfileError:
    pass
else:
    raise AssertionError("admitted interpreter accepted an incomplete closure")
inventory = json.loads((root / "toolkit.json").read_text())
for name in inventory["entryModules"]:
    __import__(name)
for name in inventory["modules"]:
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
print("admitted-tooling-semantic-validation-ok")
"""
            result = subprocess.run(
                [str(python), "-I", "-c", script, str(toolkit), str(record)],
                executable=seed.dependencies._native_retained_path(python),
                cwd=tmp_path, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == "admitted-tooling-semantic-validation-ok"


def test_self_contained_toolkit_builds_and_constructs_without_checkout_imports(
    adopter, configured_source, target, feeds, tmp_path,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    toolkit = _toolkit(tmp_path)
    env = environment(adopter, state, tmp_path)
    arguments = options(adopter, target, env)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "policy": plan(adopter).policy(),
        "options": {
            **arguments,
            "acquisition_limits": vars(arguments["acquisition_limits"]),
            "wheel_limits": vars(arguments["wheel_limits"]),
        },
    }, default=str), encoding="utf-8")
    script = """
import hashlib, importlib.abc, json, os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
blocked = {
    "worktree_manager.self_install", "worktree_manager.__main__",
    "runtime_source_manager", "promotion_python_artifacts",
    "publish_python_artifacts", "promote_release",
}
class RejectRepositoryAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError("unexpected repository adapter: " + fullname)
sys.meta_path.insert(0, RejectRepositoryAdapters())
from installer_wheel_acquisition import AcquisitionLimits
from installer_wheel_inputs import WheelLimits
from runtime_profile_policy import InstallationProfilePlan
from runtime_source_build import engine, retain_source_runtime_inputs
import runtime_feed_configuration as configuration
# Match the existing credential-free source fixture; keyed identities have
# separate native controls and must not create a machine credential key here.
configuration._opaque_index_identity = lambda value: hashlib.sha256(value.encode()).hexdigest()
request = json.loads(pathlib.Path(sys.argv[2]).read_text())
options = request["options"]
options["python"] = pathlib.Path(options["python"])
options["acquisition_limits"] = AcquisitionLimits(**options["acquisition_limits"])
options["wheel_limits"] = WheelLimits(**options["wheel_limits"])
plan = InstallationProfilePlan(json.dumps(request["policy"]).encode(), None, "release-index-unavailable")
with retain_source_runtime_inputs(plan, **options) as source:
    source.verify()
    assert source.local_packages == ("agent-example", "agent-procutil")
    assert {wheel["name"] for wheel in source.profile.record()["wheels"]} == {
        "agent-example", "agent-procutil",
    }
    runtime = pathlib.Path(plan.policy()["owner"]["runtimeRoot"])
    construction = dict(
        plugin=source.payload, uv=pathlib.Path(options["uv"]), base_python=options["python"],
        health_module="agent_example", wheel_inputs=source.wheels,
        local_packages=source.local_packages,
        expected_source_identity=engine.runtime_source.manifest_fields(source.identity),
    )
    with engine.numbered_candidate(runtime, source.runtime_version, **construction) as candidate:
        assert not candidate.reused and candidate.slot != source.build_slot
        python = engine.runtime.slot_python(runtime, candidate.slot.name)
        result = subprocess.run(
            [str(python), "-I", "-c",
             "import agent_example, importlib.metadata as m; "
             "assert agent_example.VALUE == 'from-authenticated-source'; "
             "assert {d.metadata['Name'] for d in m.distributions()} == {'agent-example', 'agent-procutil'}"],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        receipt = (candidate.slot / ".runtime-input-origins.json").read_bytes()
    with engine.numbered_candidate(runtime, source.runtime_version, **construction) as reused:
        assert reused.reused
        assert (reused.slot / ".runtime-input-origins.json").read_bytes() == receipt
    source.verify()
    assert engine.runtime.current_version(runtime) is None
for name in json.loads((root / "toolkit.json").read_text())["modules"]:
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
assert not blocked.intersection(sys.modules)
print("payload-source-build-and-constructor-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(toolkit), str(request)],
        cwd=tmp_path, env={
            **os.environ, **env, "WORKTREE_MANAGER_ROOT": str(configured_source[0]),
        },
        capture_output=True, text=True, timeout=150,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "payload-source-build-and-constructor-ok"
    assert any(path.startswith("/files/") for path, _ in state["requests"])
