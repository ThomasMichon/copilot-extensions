"""Actor-free contracts for guarded core selection."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import threading
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace

import core_native_feed_resolution as guard
import core_native_feed_target as target_guard
import pytest
from installer_wheel_acquisition import AcquisitionLimits
from packaging.tags import Tag

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


class _PendingResolver:
    def __init__(self):
        self.killed = False
        self.waits = []

    def poll(self):
        return -9 if self.killed else None

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        self.waits.append(timeout)
        return -9


@pytest.mark.parametrize(("cause", "expected"), [
    ("wheel feed request failed or timed out", "guarded feed request failed or timed out"),
    ("wheel feed body read failed or timed out", "guarded feed response read failed or timed out"),
    ("wheel acquisition request budget exceeded", "guarded feed request budget exceeded"),
    ("guarded candidate changed after acquisition", "guarded candidate changed after acquisition"),
    ("private-origin-and-credential-detail", "guarded feed input was refused"),
], ids=["request", "body", "budget", "digest", "unknown"])
def test_guarded_resolver_stop_reports_only_safe_failure_categories(monkeypatch, cause, expected):
    process = _PendingResolver()
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *_args, **_kwargs: process)
    failed = threading.Event()
    failed.set()
    server = SimpleNamespace(budget=SimpleNamespace(failed=failed, failure=ValueError(cause)))

    with pytest.raises(guard.GuardedSelectionError) as caught:
        guard._run_uv(["unused"], {}, server, time.monotonic() + 30)

    assert str(caught.value) == expected
    assert "private-origin-and-credential-detail" not in str(caught.value)
    assert process.killed and process.waits == [10]


def test_guarded_resolver_stop_distinguishes_deadline(monkeypatch):
    process = _PendingResolver()
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *_args, **_kwargs: process)
    server = SimpleNamespace(budget=SimpleNamespace(failed=threading.Event(), failure=None))

    with pytest.raises(guard.GuardedSelectionError, match="deadline exceeded"):
        guard._run_uv(["unused"], {}, server, time.monotonic() - 1)

    assert process.killed and process.waits == [10]


def test_guarded_resolver_exit_reports_a_recorded_feed_failure(monkeypatch):
    failed = threading.Event()
    server = SimpleNamespace(budget=SimpleNamespace(
        failed=failed, failure=ValueError("wheel feed request failed or timed out"),
    ))

    class ExitedResolver:
        exited = False

        def poll(self):
            return 1 if self.exited else None

        def wait(self, timeout):
            self.exited = True
            failed.set()
            return 1

    monkeypatch.setattr(guard.subprocess, "Popen", lambda *_args, **_kwargs: ExitedResolver())
    with pytest.raises(guard.GuardedSelectionError, match="guarded feed request failed or timed out"):
        guard._run_uv(["unused"], {}, server, time.monotonic() + 30)


def test_guarded_isolated_timeout_reports_only_the_deadline(monkeypatch, tmp_path):
    def timeout(*_args, **_kwargs):
        raise guard.ChildTimeoutExpired(
            ["private-command"], 1, output=b"private-output", stderr=b"private-error",
        )

    monkeypatch.setattr(guard, "run_owned", timeout)
    monkeypatch.setattr(guard.snapshots, "_open", lambda path: path.open("rb"))
    with pytest.raises(guard.GuardedSelectionError) as caught:
        guard._run_isolated(
            {"control_bytes": 0}, tmp_path, {"worker.py": b"pass"},
            _limits(), time.monotonic() + 30, SimpleNamespace(),
        )

    assert str(caught.value) == "guarded native resolver deadline exceeded"
    assert "private" not in str(caught.value)


def test_guarded_isolated_prelaunch_expiry_uses_real_deadline_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(guard.snapshots, "_open", lambda path: path.open("rb"))
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("unexpected child"))
    procutil = SimpleNamespace(
        spawn_sync_in_kill_on_close_job=lambda *_args, **_kwargs: pytest.fail("unexpected child"),
    )

    with pytest.raises(guard.GuardedSelectionError, match="guarded native resolver deadline exceeded"):
        guard._run_isolated(
            {"control_bytes": 0}, tmp_path, {"worker.py": b"pass"},
            _limits(), time.monotonic() - 1, procutil,
        )


def test_guarded_project_uses_the_configured_feed_metadata_policy(monkeypatch):
    observed = []

    class Client:
        def __init__(self, *_args):
            pass

        def request(self, _location, _feed, origins, _accept, *, index=False):
            observed.append((origins, index))
            raise guard.acquisition.AcquisitionUnavailable("fixture unavailable")

    monkeypatch.setattr(guard.acquisition, "_Client", Client)
    feed = SimpleNamespace(index_url="https://feed.example/simple")
    assert guard._fetch_project(feed, "fixture", _limits(), time.monotonic() + 30, None, None) is None
    assert observed == [(None, True)]


def test_guarded_wheel_delegates_only_without_explicit_restrictions(monkeypatch):
    content = b"fixture wheel bytes"
    digest = hashlib.sha256(content).hexdigest()
    observed = []

    class Client:
        def __init__(self, *_args):
            pass

        def request(self, location, _feed, origins, _accept):
            observed.append(origins)
            return SimpleNamespace(close=lambda: None), location, []

        def read(self, _response, _limit, *, output):
            output.write(content)
            return b"", "sha256:" + digest, len(content)

    monkeypatch.setattr(guard.acquisition, "_Client", Client)
    monkeypatch.setattr(
        guard.acquisition, "_selected",
        lambda *_args: ("https://cdn.example/fixture.whl", "2020-01-01T00:00:00Z", None),
    )
    monkeypatch.setattr(
        guard, "inspect_wheel_facts",
        lambda *_args, **_kwargs: SimpleNamespace(requires_dist=[]),
    )
    server = object.__new__(guard._GuardedIndex)
    server.lock = threading.RLock()
    server.files = {"/wheel": ("fixture", "fixture-1.0-py3-none-any.whl", digest)}
    server.wheel_locks, server.cache, server.admitted = {}, {}, {}
    server.projects = {
        "fixture": {"entries": [], "base": "https://feed.example/", "origin": "https://feed.example"},
    }
    server.cutoffs, server.floor = {}, None
    server.limits, server.deadline = _limits(), time.monotonic() + 30
    server.context, server.budget, server.feed = None, None, None
    server.wheel_limits = SimpleNamespace(
        max_archive_bytes=100, max_expanded_bytes=100, max_metadata_member_bytes=100,
    )

    cases = (((), None), (("https://allowed.example",), {"https://feed.example", "https://allowed.example"}))
    for origins, expected in cases:
        server.origins = origins
        server.cache.clear()
        assert server.wheel("/wheel") == content
        assert observed[-1] == expected


def _response():
    return {
        "status": "ok",
        "offered": {URL: {"filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + DIGEST}},
        "admitted": {URL: {
            "upload-time": "2020-01-01T00:00:00Z", "requires-python": ">=3.9",
            "yanked": False, "withdrawn": False,
        }},
        "served": [URL],
        "projects": {"fixture": "b" * 64},
        "usage": {"requests": 3, "redirects": 0, "metadata_bytes": 100, "wheel_bytes": 100},
    }


@pytest.mark.parametrize(
    "change",
    ["foreign", "unserved", "budget", "digest", "project", "missing-digest", "upload-time", "yanked", "withdrawn"],
)
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
    elif change == "missing-digest":
        response["offered"][URL]["sha256"] = None
    elif change == "upload-time":
        response["admitted"][URL]["upload-time"] = "not-a-timestamp"
    else:
        response["admitted"][URL][change] = True
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
    assert guard.verify_lock_witness(content, selected, witness, time.monotonic() + 30) == {"fixture": URL}
    changed = deepcopy(witness)
    if change == "unserved":
        changed["served"] = []
    elif change == "foreign":
        changed["offered"] = {URL.replace("127.0.0.1", "example.invalid"): changed["offered"][URL]}
    else:
        changed["offered"][URL]["sha256"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError):
        guard.verify_lock_witness(content, selected, changed, time.monotonic() + 30)


@pytest.mark.parametrize(
    "cutoff,allowed",
    [("2020-01-01T00:00:00+00:00", True), ("2019-12-31T00:00:00+00:00", False)],
    ids=["boundary", "too-new"],
)
def test_guarded_reapplies_file_admission_at_the_selected_cutoff(cutoff, allowed):
    selected = [{"name": "fixture", "filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + DIGEST}]
    args = (selected, {"fixture": URL}, _response(), {"fixture": datetime.fromisoformat(cutoff)})
    if allowed:
        guard.verify_guarded_cutoffs(*args, deadline=time.monotonic() + 30)
    else:
        with pytest.raises(ValueError, match="seasoning"):
            guard.verify_guarded_cutoffs(*args, deadline=time.monotonic() + 30)


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


def _guarded_index(entries):
    index = object.__new__(guard._GuardedIndex)
    index.exclusions = set()
    index.lock = threading.RLock()
    index.limits = _limits()
    index.wheel_limits = guard.inspection.WheelLimits(
        max_archive_bytes=16384, max_expanded_bytes=16384, max_member_bytes=16384,
        max_metadata_bytes=4096, max_members=20, max_tags=100, max_metadata_member_bytes=4096,
    )
    index.deadline = time.monotonic() + 30
    index.project_locks, index.projects, index.files, index.offered = {}, {}, {}, {}
    index.filenames, index.candidate_count = set(), 0
    index.compatible_tags = frozenset({Tag("py3", "none", "any")})
    index.secret, index.base = "capability", "http://127.0.0.1:12345/capability"
    index._project = lambda _name: (entries, "https://example.invalid/simple/", "https://example.invalid", "b" * 64)
    return index


def test_guarded_index_counts_only_native_candidates_across_projects():
    projects = {
        name: [
            {"filename": f"{name}-{version}.0-cp1-cp1-any.whl", "url": "/old.whl",
             "hashes": {"sha256": DIGEST}}
            for version in range(2, 11)
        ] + [{"filename": f"{name}-1.0-py3-none-any.whl", "url": "/good.whl",
              "hashes": {"sha256": DIGEST}}]
        for name in ("fixture", "dependency")
    }
    index = _guarded_index([])
    index._project = lambda name: (projects[name], "https://example.invalid/simple/",
                                   "https://example.invalid", "b" * 64)

    pages = [json.loads(index.page(name)) for name in projects]

    assert [[item["filename"] for item in page["files"]] for page in pages] == [
        ["fixture-1.0-py3-none-any.whl"], ["dependency-1.0-py3-none-any.whl"],
    ]
    assert index.candidate_count == len(index.offered) == 2


def test_guarded_index_keeps_a_matching_compressed_tag():
    entries = [{"filename": "fixture-1.0-py2.py3-none-any.whl", "url": "/wheel.whl",
                "hashes": {"sha256": DIGEST}}]

    page = json.loads(_guarded_index(entries).page("fixture"))

    assert [item["filename"] for item in page["files"]] == ["fixture-1.0-py2.py3-none-any.whl"]


@pytest.mark.parametrize("kind", ["bad-hash", "wrong-project", "duplicate"])
def test_guarded_index_validates_incompatible_records_before_filtering(kind):
    filename = ("other" if kind == "wrong-project" else "fixture") + "-1.0-cp1-cp1-any.whl"
    entry = {"filename": filename, "url": "/old.whl",
             "hashes": {"sha256": "bad" if kind == "bad-hash" else DIGEST}}
    entries = [entry, dict(entry)] if kind == "duplicate" else [entry]

    with pytest.raises(ValueError):
        _guarded_index(entries).page("fixture")


def test_guarded_index_still_bounds_native_candidates():
    entries = [
        {"filename": f"fixture-{version}.0-py3-none-any.whl", "url": "/wheel.whl",
         "hashes": {"sha256": DIGEST}}
        for version in range(1, 18)
    ]

    with pytest.raises(ValueError, match="candidate count"):
        _guarded_index(entries).page("fixture")


@pytest.mark.parametrize("fault", ["tags", "python", "version", "reported-python", "platform", "universal"])
def test_guarded_target_filter_refuses_a_non_native_resolver(monkeypatch, fault):
    tags = [Tag("py3", "none", "any"), Tag("cp311", "cp311", "win_amd64")]
    monkeypatch.setattr(target_guard, "sys_tags", lambda: iter(tags))
    expected = [str(tag) for tag in tags]
    python, version = sys.executable, platform.python_version()
    reported, target_platform, universal = python, "None", "false"
    if fault == "tags":
        expected = expected[:1]
    elif fault == "python":
        python += ".other"
    elif fault == "version":
        version = "0.0.0"
    elif fault == "reported-python":
        reported += ".other"
    elif fault == "platform":
        target_platform = 'Some("other")'
    else:
        universal = "true"
    transaction = SimpleNamespace(argv=(
        "uv", "pip", "compile", "--python", python, "--python-version", version,
    ))
    projection = (
        "PipCompileSettings { settings: PipSettings { "
        f"python: Some({json.dumps(reported)}), python_platform: {target_platform}, "
        f"universal: {universal}, }}, }}"
    )

    with pytest.raises(ValueError, match="native"):
        target_guard.require_native_target(transaction, projection, expected)


def test_guarded_index_omits_withdrawn_candidates_without_actors():
    hint = ">=3.9" + ",>=3.9" * 100
    entries = [
        {"filename": f"fixture-{version}-py3-none-any.whl", "url": f"/{version}.whl",
         "hashes": {"sha256": DIGEST}, "requires-python": hint, "withdrawn": withdrawn}
        for version, withdrawn in (("1.0", False), ("2.0", True))
    ]
    index = _guarded_index(entries)

    page = json.loads(index.page("fixture"))

    assert [item["filename"] for item in page["files"]] == ["fixture-1.0-py3-none-any.whl"]
    assert page["files"][0]["requires-python"] == hint
    assert hint not in json.dumps(index.offered)
    assert {item["filename"] for item in index.offered.values()} == {"fixture-1.0-py3-none-any.whl"}


@pytest.mark.parametrize("bad_tag", ["py36+-none-any", "py3-none-any+", "py3..py2-none-any"])
def test_guarded_index_ignores_invalid_tag_records_without_actors(bad_tag):
    entries = [
        {"filename": f"fixture-0.18-{bad_tag}.whl", "url": "/old.whl", "hashes": {"sha256": DIGEST}},
        {"filename": "fixture-1.0-py3-none-any.whl", "url": "/good.whl", "hashes": {"sha256": DIGEST}},
    ]
    index = _guarded_index(entries)

    page = json.loads(index.page("fixture"))

    assert [item["filename"] for item in page["files"]] == ["fixture-1.0-py3-none-any.whl"]
    assert {item["filename"] for item in index.offered.values()} == {"fixture-1.0-py3-none-any.whl"}


@pytest.mark.parametrize("kind", ["unsafe-path", "wrong-project", "wrong-project-invalid-tag",
                                  "bad-hash", "tag-expansion"])
def test_guarded_index_still_refuses_unsafe_or_excessive_records(kind):
    tags = ".".join(f"py{i}" for i in range(11)) + "-" + ".".join(f"abi{i}" for i in range(11)) + "-any"
    filename = {
        "unsafe-path": "../fixture-1.0-py3-none-any.whl",
        "wrong-project": "other-1.0-py3-none-any.whl",
        "wrong-project-invalid-tag": "other-1.0-py36+-none-any.whl",
        "bad-hash": "fixture-1.0-py3-none-any.whl",
        "tag-expansion": f"fixture-1.0-{tags}.whl",
    }[kind]
    entries = [{"filename": filename, "url": "/wheel.whl",
                "hashes": {"sha256": "not-a-digest" if kind == "bad-hash" else DIGEST}}]

    with pytest.raises(ValueError):
        _guarded_index(entries).page("fixture")


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


@pytest.mark.parametrize("change", [
    "incompatible-metadata", "incompatible-added", "incompatible-removed",
    "compatible-metadata", "compatible-added", "compatible-removed",
    "incompatible-malformed",
])
def test_guarded_final_recheck_uses_the_validated_native_candidate_set(monkeypatch, change):
    entries = [
        {"filename": f"fixture-1.0-{tag}.whl", "url": f"/{tag}.whl",
         "hashes": {"sha256": DIGEST}, "upload-time": "2020-01-01T00:00:00Z"}
        for tag in ("py3-none-any", "cp1-cp1-any")
    ]

    class Client:
        def __init__(self, *_args):
            pass

        def request(self, location, *_args, **_kwargs):
            headers = SimpleNamespace(get_content_type=lambda: "application/vnd.pypi.simple.v1+json")
            return SimpleNamespace(headers=headers, close=lambda: None), location, []

        def read(self, *_args):
            content = json.dumps({
                "meta": {"api-version": "1.1"}, "name": "fixture", "files": entries,
            }).encode()
            return content, hashlib.sha256(content).hexdigest(), len(content)

    monkeypatch.setattr(guard.acquisition, "_Client", Client)
    monkeypatch.setattr(guard.acquisition, "_tls_context", lambda _record: object())
    index = _guarded_index(entries)
    monkeypatch.setattr(guard, "sys_tags", lambda: iter(index.compatible_tags))
    feed = SimpleNamespace(index_url="https://example.invalid/simple")
    index._project = lambda name: guard._fetch_project(
        feed, name, index.limits, index.deadline, None, None,
    )
    index.page("fixture")
    request = {
        "feed": {"identity": "sha256:" + DIGEST, "index_url": feed.index_url, "headers": []},
        "limits": asdict(index.limits), "wheel_limits": asdict(index.wheel_limits),
        "target_tags": [str(tag) for tag in index.compatible_tags],
        "deadline": index.deadline, "control_bytes": 0, "tls": {},
        "projects": {"fixture": index.projects["fixture"]["identity"]},
    }
    guard._verify_worker(request)
    position = 1 if change.startswith("incompatible-") else 0
    if change.endswith("-added"):
        entries.append({**entries[position], "filename": entries[position]["filename"].replace("1.0", "2.0")})
    elif change.endswith("-removed"):
        entries.pop(position)
    else:
        entries[position]["hashes"] = {"sha256": "bad" if change.endswith("-malformed") else "b" * 64}
        entries[position]["upload-time"] = "2021-01-01T00:00:00Z"

    if change in {"incompatible-metadata", "incompatible-added", "incompatible-removed"}:
        guard._verify_worker(request)
    else:
        with pytest.raises(ValueError):
            guard._verify_worker(request)


@pytest.mark.parametrize(
    "old,current", [("b" * 64, "c" * 64), (None, "b" * 64), ("b" * 64, None)],
    ids=["changed", "appeared", "disappeared"],
)
def test_guarded_final_index_recheck_refuses_changed_evidence(monkeypatch, old, current):
    index = _guarded_index([])
    request = {
        "feed": {"identity": "sha256:" + "a" * 64, "index_url": "https://example.invalid/simple", "headers": []},
        "limits": asdict(_limits()), "deadline": time.monotonic() + 30, "control_bytes": 0,
        "tls": {}, "projects": {"fixture": old},
        "wheel_limits": asdict(index.wheel_limits),
        "target_tags": [str(tag) for tag in guard.sys_tags()],
    }
    monkeypatch.setattr(guard.acquisition, "_tls_context", lambda _record: object())
    monkeypatch.setattr(guard, "_target_project", lambda project, *_args: project)
    monkeypatch.setattr(
        guard, "_fetch_project", lambda *_args, **_kwargs: None if old is None else ([], "", "", old),
    )
    guard._verify_worker(request)
    monkeypatch.setattr(
        guard, "_fetch_project", lambda *_args, **_kwargs: None if current is None else ([], "", "", current),
    )
    with pytest.raises(ValueError):
        guard._verify_worker(request)


def test_guarded_final_recheck_refuses_a_different_native_target(monkeypatch):
    index = _guarded_index([])
    request = {
        "feed": {"identity": "sha256:" + DIGEST, "index_url": "https://example.invalid/simple", "headers": []},
        "limits": asdict(index.limits), "wheel_limits": asdict(index.wheel_limits),
        "target_tags": [], "deadline": index.deadline,
    }
    monkeypatch.setattr(guard, "_fetch_project", lambda *_a, **_k: pytest.fail("unexpected feed access"))

    with pytest.raises(ValueError, match="native interpreter"):
        guard._verify_worker(request)
