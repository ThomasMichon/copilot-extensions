"""Actual owner policy/native settings composed with controlled TLS acquisition."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from packaging.markers import default_environment
import pytest

import runtime_feed_configuration as configuration
from runtime_profile import ProfileError
from runtime_profile_wheels import profile_wheel_inputs, verify_profile_wheels
from test_installer_closed_wheels import engine as engine, plugin as plugin
from test_installer_wheel_acquisition import (
    CERTIFICATE, feeds as feeds, limits, worker_procutil,
)
from test_runtime_profile_policy_plan import adopter as adopter, owned_policy, plan
from test_runtime_profile_release import (
    _verification, release as release,
)
from test_promotion_python_artifacts import _write_index
from installer_wheel_acquisition import AcquisitionUnavailable, acquire_wheel_inputs


@pytest.fixture
def selected(adopter, release, monkeypatch):
    record = release[2]
    record["target"] = {"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"}
    record["wheels"][0]["requires_python"] = ""
    _write_index(release[0], release[1])
    _verification(monkeypatch, release)
    # The pre-existing keyed-identity helper has independent tests. These
    # synthetic projections must never create a real machine credential key.
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    return plan(adopter, release)


def environment(adopter, state, tmp_path, *, authenticate="never", **settings):
    path = tmp_path / "native-uv.toml"
    path.write_text(
        "system-certs=true\n"
        + "\n".join(key.replace("_", "-") + "=" + json.dumps(value) for key, value in settings.items())
        + "\n[[index]]\nname=\"governed\"\ndefault=true\n"
        + "url=" + json.dumps(state["origin"] + "/simple") + "\n"
        + "authenticate=" + json.dumps(authenticate) + "\n",
        encoding="utf-8",
    )
    result = {
        key: value for key, value in os.environ.items()
        if key.upper() in (
            "PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR", "COMSPEC",
            "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
        )
    }
    result.update(
        HOME=str(adopter[1]["os_profile"]), USERPROFILE=str(adopter[1]["os_profile"]),
        UV_CONFIG_FILE=str(path), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        SSL_CERT_FILE=str(CERTIFICATE),
    )
    return result


def capture(selected, adopter, environment, **changes):
    return configuration.retain_native_feed_inputs(
        selected, installation=adopter[1], uv="uv", python=Path(sys.executable),
        **({"deadline": time.monotonic() + 30, "env": environment} | changes),
    )


def serve_release(state, release):
    record = release[2]
    expected = record["wheels"][0]
    with zipfile.ZipFile(release[0] / release[1]["artifacts"][0]["filename"]) as bundle:
        content = bundle.read(expected["filename"])
    metadata = {
        "meta": {"api-version": "1.1"}, "name": expected["name"], "files": [{
            "filename": expected["filename"], "url": "/files/" + expected["filename"],
            "hashes": {"sha256": expected["sha256"].removeprefix("sha256:")},
            "upload-time": "2020-01-01T00:00:00Z",
        }],
    }
    state["routes"]["/simple/" + expected["name"] + "/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps(metadata).encode(),
    )
    state["routes"]["/files/" + expected["filename"]] = (200, {}, content)
    return record


def test_actual_owner_native_configuration_and_tls_download_into_complete_inspection(
    adopter, release, selected, feeds, tmp_path, engine, plugin, monkeypatch,
):
    state = feeds()
    record = serve_release(state, release)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "unselected-ca"))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1/unselected-proxy")
    deadline = time.monotonic() + 90
    with capture(selected, adopter, env, deadline=deadline) as inputs:
        assert state["requests"] == []
        assert inputs.profile.record() == record
        assert len(inputs.feeds) == 1
        assert inputs.feeds[0].index_url == state["origin"] + "/simple"
        with acquire_wheel_inputs(
            record["wheels"], feeds=inputs.feeds, download_origins=inputs.download_origins,
            cutoffs=inputs.cutoffs, policy_id=inputs.policy_id, limits=limits(),
            deadline=deadline, ssl_context=inputs.context, proxies=inputs.proxies,
            procutil=worker_procutil(),
        ) as acquired:
            verify_profile_wheels(
                inputs.profile, acquired.snapshot.archives,
                max_archive_bytes=4096, max_unpacked_bytes=4096, max_metadata_bytes=4096,
                deadline=deadline,
            )
            assert acquired.evidence()["policy_id"] == inputs.policy_id
            paths = tuple(acquired.snapshot.paths.values())
            unbound = profile_wheel_inputs(
                inputs.profile, acquired.snapshot, max_archive_bytes=4096,
                max_unpacked_bytes=4096, max_metadata_bytes=4096, deadline=deadline,
            )
            wheel_inputs = acquired.construction_inputs(unbound)
            for change in ("unexpected", "digest", "size", "origin"):
                altered = acquired.evidence()
                if change == "unexpected":
                    altered["Authorization"] = "synthetic-private-header"
                elif change == "digest":
                    altered["files"][0]["sha256"] = "sha256:" + "0" * 64
                elif change == "size":
                    altered["files"][0]["size"] += 1
                else:
                    altered["files"][0]["source_origins"] = ["https://files.example"]
                invalid = replace(
                    acquired, evidence_json=json.dumps(altered, sort_keys=True, separators=(",", ":")).encode(),
                )
                with pytest.raises(ValueError, match="acquisition facts"):
                    invalid.construction_inputs(unbound)
            manifest = plugin / "pyproject.toml"
            manifest.write_text(manifest.read_text().replace("demo-pkg", "demo"))
            poison = tmp_path / "unadmitted-cache"
            monkeypatch.setenv("UV_INDEX_URL", state["origin"] + "/unexpected-index")
            monkeypatch.setenv("UV_CONFIG_FILE", str(tmp_path / "missing-config"))
            monkeypatch.setenv("UV_CACHE_DIR", str(poison))
            root = tmp_path / "runtime"
            with engine.numbered_candidate(
                root, "1.0.0", plugin=plugin, uv=Path(shutil.which("uv")),
                base_python=Path(sys.executable), health_module="demo",
                local_packages=("demo",), wheel_inputs=wheel_inputs,
            ) as built:
                assert not built.reused
                assert engine.runtime.is_complete(root, "1.0.0", expect_hash=built.payload_hash)
                assert engine.runtime.current_version(root) is None
                receipt = json.loads((built.slot / ".runtime-input-origins.json").read_text())
                assert receipt["wheel_inputs"]["acquisition"]["policy_id"] == inputs.policy_id
                python = built.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                proof = subprocess.run(
                    [str(python), "-I", "-c", "import importlib.metadata as m, demo; print(m.version('demo'))"],
                    capture_output=True, text=True, check=True, timeout=deadline - time.monotonic(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                assert proof.stdout.strip() == "1.0"
            assert not poison.exists()
            route = "/simple/" + record["wheels"][0]["name"] + "/"
            status, headers, content = state["routes"][route]
            metadata = json.loads(content)
            metadata["files"].append({
                **metadata["files"][0], "filename": "unselected-2.0-py3-none-any.whl",
            })
            state["routes"][route] = status, headers, json.dumps(metadata).encode()
            with acquire_wheel_inputs(
                record["wheels"], feeds=inputs.feeds, download_origins=inputs.download_origins,
                cutoffs=inputs.cutoffs, policy_id=inputs.policy_id, limits=limits(),
                deadline=deadline, ssl_context=inputs.context, proxies=inputs.proxies,
                procutil=worker_procutil(),
            ) as reacquired:
                repeated = reacquired.construction_inputs(profile_wheel_inputs(
                    inputs.profile, reacquired.snapshot, max_archive_bytes=4096,
                    max_unpacked_bytes=4096, max_metadata_bytes=4096, deadline=deadline,
                ))
                assert reacquired.evidence()["metadata_bytes"] > acquired.evidence()["metadata_bytes"]
                assert repeated.receipt() == wheel_inputs.receipt()
                assert set(repeated.receipt()["acquisition"]) == {
                    "schema", "version", "policy_id", "files",
                }
                with engine.numbered_candidate(
                    root, "1.0.0", plugin=plugin, uv=Path(shutil.which("uv")),
                    base_python=Path(sys.executable), health_module="demo",
                    local_packages=("demo",), wheel_inputs=repeated,
                ) as reused:
                    assert reused.reused and reused.payload_hash == built.payload_hash
            altered = acquired.evidence()
            altered["policy_id"] = "sha256:" + "0" * 64
            changed = replace(
                wheel_inputs, acquisition_json=json.dumps(altered, sort_keys=True, separators=(",", ":")),
            )
            for candidate_inputs in (changed, unbound):
                with pytest.raises(RuntimeError, match="different content"):
                    with engine.numbered_candidate(
                        root, "1.0.0", plugin=plugin, uv=Path(shutil.which("uv")),
                        base_python=Path(sys.executable), health_module="demo",
                        local_packages=("demo",), wheel_inputs=candidate_inputs,
                    ):
                        pytest.fail("changed or removed acquisition policy reused the generation")
    assert len(state["requests"]) == 4
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("origin", ["named-environment", "retained-url"])
def test_native_named_and_retained_url_credentials_are_built_without_masking(
    adopter, release, selected, feeds, tmp_path, origin,
):
    state = feeds()
    env = environment(adopter, state, tmp_path, authenticate="always")
    user, password = "fixture-user", "synthetic-secret"
    record = serve_release(state, release)

    def authorized(headers):
        try:
            scheme, value = headers.get("Authorization", "").split(" ", 1)
            return scheme == "Ba" + "sic" and base64.b64decode(value, validate=True) == (user + ":" + password).encode()
        except ValueError:
            return False

    state["authorize"] = authorized
    if origin == "named-environment":
        env.update(UV_INDEX_GOVERNED_USERNAME=user, UV_INDEX_GOVERNED_PASSWORD=password)
    else:
        path = Path(env["UV_CONFIG_FILE"])
        path.write_text(path.read_text().replace(
            state["origin"] + "/simple",
            state["origin"].replace("https://", "https://" + user + ":" + password + "@") + "/simple",
        ))
    with capture(selected, adopter, env) as inputs:
        headers = dict(inputs.feeds[0].headers)
        value = headers["Authorization"]
        assert base64.b64decode(value.split(" ", 1)[1]) == (user + ":" + password).encode()
        assert password not in repr(inputs.feeds[0])
        with acquire_wheel_inputs(
            record["wheels"], feeds=inputs.feeds, download_origins=inputs.download_origins,
            cutoffs=inputs.cutoffs, policy_id=inputs.policy_id, limits=limits(),
            deadline=time.monotonic() + 20, ssl_context=inputs.context, proxies=inputs.proxies,
            procutil=worker_procutil(),
        ) as acquired:
            acquired.snapshot.verify()
    assert len(state["requests"]) == 2


def test_header_builder_exercises_real_bytes_not_a_mocked_transport():
    value = dict(configuration._basic("fixture-user", "synthetic-secret"))["Authorization"]
    assert base64.b64decode(value.split(" ", 1)[1]) == b"fixture-user:synthetic-secret"
    assert value.split(" ", 1)[0] == "Ba" + "sic"


def test_native_netrc_provider_is_bounded_and_preserved(adopter, selected, feeds, tmp_path):
    state = feeds()
    env = environment(adopter, state, tmp_path, authenticate="always")
    path = tmp_path / "selected-netrc"
    path.write_text("machine 127.0.0.1 login fixture-user password synthetic-secret\n")
    env["NETRC"] = str(path)
    with capture(selected, adopter, env) as inputs:
        value = dict(inputs.feeds[0].headers)["Authorization"]
        assert base64.b64decode(value.split(" ", 1)[1]) == b"fixture-user:synthetic-secret"
    assert state["requests"] == []


@pytest.mark.parametrize("provider", ["named-environment", "retained-url", "netrc"])
@pytest.mark.parametrize("change", ["account", "password"])
def test_native_authentication_changes_invalidate_opaque_reuse_identity(
    adopter, selected, feeds, tmp_path, monkeypatch, provider, change,
):
    import governed_feed_trust as trust

    monkeypatch.setattr(trust, "_provenance_key", lambda: b"synthetic-test-provenance-key")
    monkeypatch.setattr(configuration, "_opaque_index_identity", trust._opaque_index_identity)
    state = feeds()
    env = environment(adopter, state, tmp_path, authenticate="always")
    path = Path(env["UV_CONFIG_FILE"])
    config = path.read_text()

    def select(user, password):
        if provider == "named-environment":
            env.update(UV_INDEX_GOVERNED_USERNAME=user, UV_INDEX_GOVERNED_PASSWORD=password)
        elif provider == "retained-url":
            path.write_text(config.replace(
                state["origin"], state["origin"].replace("https://", "https://" + user + ":" + password + "@"),
            ))
        else:
            netrc_path = tmp_path / "selected-netrc"
            netrc_path.write_text(f"machine 127.0.0.1 login {user} password {password}\n")
            env["NETRC"] = str(netrc_path)

    select("fixture-user", "synthetic-secret")
    with capture(selected, adopter, env) as first:
        identity, feed = first.policy_id, first.feeds[0].identity
    with capture(selected, adopter, env) as unchanged:
        assert (unchanged.policy_id, unchanged.feeds[0].identity) == (identity, feed)
    select("other-user" if change == "account" else "fixture-user",
           "other-secret" if change == "password" else "synthetic-secret")
    with capture(selected, adopter, env) as changed:
        assert changed.policy_id != identity
        assert changed.feeds[0].identity != feed
        assert all(len(value) == 71 and value.startswith("sha256:") for value in (
            identity, feed, changed.policy_id, changed.feeds[0].identity,
        ))
    assert state["requests"] == []


def test_native_automatic_no_credentials_is_not_a_new_approval_map(adopter, selected, feeds, tmp_path):
    state = feeds()
    env = environment(adopter, state, tmp_path, authenticate="auto")
    with capture(selected, adopter, env) as inputs:
        assert inputs.feeds[0].headers == ()
    assert state["requests"] == []


def test_changed_owner_policy_cannot_be_replaced_by_a_stale_plan(adopter, selected, feeds, tmp_path):
    state = feeds()
    env = environment(adopter, state, tmp_path)
    owned_policy(adopter[0], adopter[1], downloadOrigins=["https://files.example"])
    with pytest.raises(ProfileError, match="installation owner"):
        with capture(selected, adopter, env):
            pytest.fail("stale owner plan was accepted")
    assert state["requests"] == []


@pytest.mark.parametrize("changes,message", [
    ({"index_strategy": "unsafe-best-match"}, "index strategy"),
    ({"keyring_provider": "subprocess"}, "credential provider"),
    ({"allow_insecure_host": ["127.0.0.1"]}, "insecure"),
])
def test_unsupported_native_settings_refuse_before_http(
    adopter, selected, feeds, tmp_path, changes, message,
):
    state = feeds()
    env = environment(adopter, state, tmp_path, **changes)
    with pytest.raises(ValueError, match=message):
        with capture(selected, adopter, env):
            pytest.fail("unsupported native policy was silently changed")
    assert state["requests"] == []


def test_unsupported_bundled_root_provider_is_not_silently_replaced(adopter, selected, feeds, tmp_path):
    state = feeds()
    env = environment(adopter, state, tmp_path)
    path = Path(env["UV_CONFIG_FILE"])
    path.write_text(path.read_text().replace("system-certs=true", "system-certs=false"))
    with pytest.raises(AcquisitionUnavailable, match="certificate provider"):
        with capture(selected, adopter, env):
            pytest.fail("native root provider was replaced")
    assert state["requests"] == []


def test_offline_native_configuration_is_a_typed_unavailable_outcome(adopter, selected, feeds, tmp_path):
    state = feeds()
    env = environment(adopter, state, tmp_path)
    env["UV_OFFLINE"] = "1"
    with pytest.raises(AcquisitionUnavailable, match="offline"):
        with capture(selected, adopter, env):
            pytest.fail("offline configuration sent a request")
    assert state["requests"] == []


def test_owner_selected_download_origins_survive_native_capture(
    adopter, release, feeds, tmp_path, monkeypatch,
):
    state = feeds()
    owned_policy(adopter[0], adopter[1], downloadOrigins=["https://files.example"])
    release[2]["target"] = {"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"}
    _write_index(release[0], release[1])
    _verification(monkeypatch, release)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    selected = plan(adopter, release)
    with capture(selected, adopter, environment(adopter, state, tmp_path)) as inputs:
        assert inputs.download_origins == ("https://files.example",)
    assert state["requests"] == []


def test_unchanged_native_policy_identity_excludes_retained_paths_and_current_cutoff(
    adopter, selected, feeds, tmp_path,
):
    state = feeds()
    env = environment(adopter, state, tmp_path)
    with capture(selected, adopter, env) as first:
        identity, feeds_first = first.policy_id, tuple(feed.identity for feed in first.feeds)
        cutoff = dict(first.cutoffs)
    with capture(selected, adopter, env) as second:
        assert second.policy_id == identity
        assert tuple(feed.identity for feed in second.feeds) == feeds_first
        assert all(second.cutoffs[name] >= value for name, value in cutoff.items())
    assert state["requests"] == []


def test_changed_relative_seasoning_changes_effective_policy_identity(
    adopter, selected, feeds, tmp_path,
):
    state = feeds()
    env = environment(adopter, state, tmp_path, exclude_newer="14d")
    with capture(selected, adopter, env) as first:
        identity, cutoff = first.policy_id, first.cutoffs["demo"]
    path = Path(env["UV_CONFIG_FILE"])
    path.write_text(path.read_text().replace('exclude-newer="14d"', 'exclude-newer="21d"'))
    with capture(selected, adopter, env) as second:
        assert second.policy_id != identity
        assert second.cutoffs["demo"] < cutoff
    assert state["requests"] == []


@pytest.mark.parametrize("variable", ["UV_CONSTRAINT", "UV_OVERRIDE", "UV_BUILD_CONSTRAINT"])
def test_machine_package_constraints_are_not_ignored_for_an_exact_profile(
    adopter, selected, feeds, tmp_path, variable,
):
    state = feeds()
    env = environment(adopter, state, tmp_path)
    declaration = tmp_path / "machine-requirements.txt"
    declaration.write_text("demo!=1.0\n")
    env[variable] = str(declaration)
    with pytest.raises(AcquisitionUnavailable, match="resolver-altering"):
        with capture(selected, adopter, env):
            pytest.fail("an exact profile bypassed unsupported machine constraints")
    assert state["requests"] == []


def test_selected_package_index_mapping_is_not_silently_replaced(adopter, selected, feeds, tmp_path):
    state = feeds()
    env = environment(adopter, state, tmp_path)
    project = Path(adopter[1]["payload_root"])
    (project / "pyproject.toml").write_text(
        '[project]\nname="fixture-host"\nversion="1.0"\ndependencies=["demo==1.0"]\n'
        '[tool.uv.sources]\nDemo={index="governed"}\n',
    )
    env["UV_PROJECT"] = str(project)
    with pytest.raises(AcquisitionUnavailable, match="per-package index"):
        with capture(selected, adopter, env):
            pytest.fail("a per-package native index choice was silently ignored")
    assert state["requests"] == []
