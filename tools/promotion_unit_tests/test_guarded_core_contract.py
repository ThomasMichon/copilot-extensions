"""Actor-free contracts for guarded core selection."""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import core_native_feed_resolution as guard
import pytest
from installer_wheel_acquisition import AcquisitionLimits

URL = "http://127.0.0.1:12345/capability/files/key/fixture-1.0-py3-none-any.whl"
INDEX = "http://127.0.0.1:12345/capability/simple/"
DIGEST = "a" * 64


def _projection(*, duplicate=False, host="127.0.0.1", username="", resolution="Highest",
                flat="[]", index_cutoff="None"):
    index = f'''Index {{
        url: Url(VerbatimUrl {{
            url: SafeUrl {{ scheme: "http", username: "{username}", password: None,
                host: Some(Ipv4({host},)), port: Some(12345,),
                path: "/capability/simple/", query: None, fragment: None, }},
            given: Some("{INDEX}"), expanded: false, force_relative: false,
        }}), explicit: false, default: true, format: Simple,
        authenticate: Never, ignore_error_codes: None, exclude_newer: {index_cutoff},
    }}'''
    indexes = index + (", " + index if duplicate else "")
    return f'''PipCompileSettings {{
        settings: PipSettings {{
            index_locations: IndexLocations {{ indexes: [{indexes}], flat_index: {flat}, no_index: false, }},
            resolution: {resolution},
        }},
        refresh: None,
    }}'''


@pytest.mark.parametrize("change", ["duplicate", "foreign", "credentials", "selection", "flat", "index-cutoff"])
def test_guarded_projection_refuses_an_escape_or_selection_change(change):
    observed = {
        "duplicate": _projection(duplicate=True),
        "foreign": _projection(host="127.0.0.2"),
        "credentials": _projection(username="unexpected"),
        "selection": _projection(resolution="Lowest"),
        "flat": _projection(flat="[Local(\"other\")]"),
        "index-cutoff": _projection(index_cutoff='Some("2020-01-01")'),
    }[change]
    guard._check_local_projection(_projection(), _projection(), INDEX)
    with pytest.raises(ValueError):
        guard._check_local_projection(_projection(), observed, INDEX)


def _limits():
    return AcquisitionLimits(
        max_packages=4, max_requests=8, max_redirects=2, max_candidates_per_project=16,
        max_index_bytes=4096, max_total_metadata_bytes=8192, max_wheel_bytes=16384,
        max_closure_bytes=32768, max_temporary_bytes=65536, max_workers=2,
    )


def _response():
    return {
        "status": "ok",
        "offered": {URL: {"filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + DIGEST}},
        "served": [URL],
        "projects": {"fixture": "b" * 64},
        "usage": {"requests": 3, "redirects": 0, "metadata_bytes": 100, "wheel_bytes": 100},
    }


@pytest.mark.parametrize("change", ["foreign", "unserved", "budget", "digest", "project", "missing-digest"])
def test_guarded_response_rejects_untrusted_evidence(change):
    response = _response()
    guard._validate_response(response, _limits(), time.monotonic() + 30)
    if change == "foreign":
        response["offered"] = {URL.replace("127.0.0.1", "example.invalid"): next(iter(response["offered"].values()))}
        response["served"] = list(response["offered"])
    elif change == "unserved":
        response["served"] = [URL + "-unknown"]
    elif change == "budget":
        response["usage"]["requests"] = 9
    elif change == "digest":
        response["offered"][URL]["sha256"] = "sha256:invalid"
    elif change == "project":
        response["projects"] = {}
    else:
        response["offered"][URL]["sha256"] = None
    with pytest.raises(ValueError):
        guard._validate_response(response, _limits(), time.monotonic() + 30)


@pytest.mark.parametrize("change", ["unserved", "foreign", "digest"])
def test_guarded_lock_requires_a_served_exact_witness(change):
    content = f'''lock-version = "1.0"
created-by = "uv"
requires-python = ">=3.9"
[[packages]]
name = "fixture"
version = "1.0"
wheels = [{{ url = "{URL}", hashes = {{ sha256 = "{DIGEST}" }} }}]
'''.encode()
    selected = [{"name": "fixture", "filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + DIGEST}]
    witness = _response()
    guard.verify_lock_witness(content, selected, witness, time.monotonic() + 30)
    changed = deepcopy(witness)
    if change == "unserved":
        changed["served"] = []
    elif change == "foreign":
        changed["offered"] = {URL.replace("127.0.0.1", "example.invalid"): changed["offered"][URL]}
    else:
        changed["offered"][URL]["sha256"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError):
        guard.verify_lock_witness(content, selected, changed, time.monotonic() + 30)


def test_guarded_configuration_removes_native_index_credentials(tmp_path):
    config = tmp_path / "uv.toml"
    config.write_text('[[index]]\nname="private"\nurl="https://example.invalid/simple"\ndefault=true\n')
    transaction = SimpleNamespace(
        argv=("uv", "pip", "compile", str(tmp_path / "requirements.in"), "--config-file", str(config)),
        environment={
            "UV_CONFIG_FILE": str(config), "UV_INDEX_PRIVATE_PASSWORD": "fixture-password",
            "UV_EXTRA_INDEX_URL": "https://other.invalid/simple", "UV_INDEX_STRATEGY": "first-index",
            "PIP_INDEX_URL": "https://third.invalid/simple",
        },
    )
    argv, environment, template = guard._local_configuration(transaction, tmp_path)
    assert "example.invalid" not in template and guard._PLACEHOLDER in template
    assert "UV_INDEX_PRIVATE_PASSWORD" not in environment and "UV_EXTRA_INDEX_URL" not in environment
    assert "PIP_INDEX_URL" not in environment and environment["UV_INDEX_STRATEGY"] == "first-index"
    assert argv[-3:] == ["--no-cache", "--allow-insecure-host", "127.0.0.1"]


def test_guarded_configuration_refuses_unattributed_source_mappings(tmp_path):
    config = tmp_path / "uv.toml"
    config.write_text('[sources]\nfixture = { git = "https://example.invalid/repo" }\n')
    transaction = SimpleNamespace(
        argv=("uv", "pip", "compile", str(tmp_path / "requirements.in"), "--config-file", str(config)),
        environment={"UV_CONFIG_FILE": str(config)},
    )
    with pytest.raises(ValueError):
        guard._local_configuration(transaction, tmp_path)


def test_guarded_compile_rechecks_retained_inputs_without_actors(tmp_path, monkeypatch):
    config, requirements, constraints = (tmp_path / name for name in ("uv.toml", "requirements.in", "constraints.in"))
    for path in (config, requirements, constraints):
        path.write_text("original\n")
    transaction = SimpleNamespace(
        sources={}, environment={"UV_CONFIG_FILE": str(config)},
        argv=("uv", "pip", "compile", str(requirements), "--constraints", str(constraints)),
    )
    monkeypatch.setattr(guard.snapshots, "_open", lambda path: path.open("rb"))
    with pytest.raises(ValueError), guard.hold_compile_inputs(transaction, time.monotonic() + 30):
        requirements.write_text("changed\n")


@pytest.mark.parametrize(
    "old,current", [("b" * 64, "c" * 64), (None, "b" * 64), ("b" * 64, None)],
    ids=["changed", "appeared", "disappeared"],
)
def test_guarded_final_index_recheck_refuses_changed_evidence(monkeypatch, old, current):
    request = {
        "feed": {"identity": "sha256:" + "a" * 64, "index_url": "https://example.invalid/simple", "headers": []},
        "limits": asdict(_limits()), "deadline": time.monotonic() + 30, "control_bytes": 0,
        "tls": {}, "projects": {"fixture": old},
    }
    monkeypatch.setattr(guard.acquisition, "_tls_context", lambda _record: object())
    monkeypatch.setattr(
        guard, "_fetch_project", lambda *_args, **_kwargs: None if old is None else ([], "", "", old),
    )
    guard._verify_worker(request)
    monkeypatch.setattr(
        guard, "_fetch_project", lambda *_args, **_kwargs: None if current is None else ([], "", "", current),
    )
    with pytest.raises(ValueError):
        guard._verify_worker(request)
