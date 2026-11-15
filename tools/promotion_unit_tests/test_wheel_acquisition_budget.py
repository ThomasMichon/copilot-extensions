"""Pure shared transfer-budget and worker-policy controls; native overlap is separate."""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from os import stat_result
from email.message import Message
from typing import Any, Callable, Iterable, Iterator, Literal, TypeVar, cast
from urllib.request import Request
import hashlib
import json
import ssl
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "installer-engine"))
import installer_wheel_acquisition as acquisition
import installer_dependency_policy as dependencies

_T = TypeVar("_T")
_U = TypeVar("_U")


def limits(**changes: Any) -> acquisition.AcquisitionLimits:
    return replace(acquisition.AcquisitionLimits(
        max_packages=16, max_requests=4, max_redirects=2,
        max_candidates_per_project=8, max_index_bytes=8,
        max_total_metadata_bytes=12, max_wheel_bytes=10,
        max_closure_bytes=16, max_temporary_bytes=20,
    ), **changes)


@pytest.mark.parametrize("change", ["identity", "headers", "tls", "duplicate", "filename", "policy"])
def test_acquisition_input_validation_can_be_reused_without_downloading(change: str) -> None:
    item: Any = {
        "name": "fixture", "filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + "a" * 64,
    }
    feed = acquisition.WheelFeed("sha256:" + "b" * 64, "https://feed.invalid/simple")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    options: dict[str, Any] = {
        "feeds": (feed,), "download_origins": (),
        "cutoffs": {"fixture": datetime.fromisoformat("2020-01-01T00:00:00+00:00")},
        "policy_id": "sha256:" + "c" * 64, "limits": limits(),
        "deadline": time.monotonic() + 5, "ssl_context": context,
        "proxies": {}, "procutil": ModuleType("test_procutil"),
    }
    assert acquisition.validate_acquisition_inputs([item], **options) == ([item], set())
    if change == "identity":
        options["feeds"] = (replace(feed, identity="invalid"),)
    elif change == "headers":
        options["feeds"] = (replace(feed, headers=(("X-Unsupported", "value"),)),)
    elif change == "tls":
        context.check_hostname = False
    elif change == "duplicate":
        item = [item, item]
    elif change == "filename":
        item = [{**item, "filename": "../fixture.whl"}]
    else:
        options["policy_id"] = "invalid"
    selected = item if isinstance(item, list) else [item]
    with pytest.raises(acquisition.WheelError):
        acquisition.validate_acquisition_inputs(selected, **options)


@pytest.mark.parametrize("workers", [1, 4, 8])
def test_worker_policy_keeps_serial_and_bounded_parallel_selection(workers: int) -> None:
    selected = limits(max_workers=workers)
    selected.verify()
    assert selected.max_workers == workers
    assert limits().max_workers == 8


@pytest.mark.parametrize("workers", [0, -1, True, 9, "8"])
def test_worker_policy_refuses_noninteger_unbounded_or_empty_selection(workers: object) -> None:
    with pytest.raises(acquisition.WheelError, match="positive|eight workers"):
        limits(max_workers=workers).verify()


@pytest.mark.parametrize("kind,maximum", [
    ("requests", 4), ("redirects", 2), ("metadata_bytes", 12), ("wheel_bytes", 13),
])
def test_transfer_budgets_are_shared_inclusive_and_never_partially_reserved(kind: str, maximum: int) -> None:
    budget = acquisition._Budget(limits(), time.monotonic() + 5, control_bytes=7)
    budget.reserve(kind, maximum - 1)
    budget.reserve(kind, 1)
    assert budget.current(kind) == maximum
    with pytest.raises(acquisition.WheelError, match="budget"):
        budget.reserve(kind, 1)
    assert budget.current(kind) == maximum


def test_peer_failure_cancels_further_reservations_without_replacing_the_cause() -> None:
    budget = acquisition._Budget(limits(), time.monotonic() + 5)
    original = acquisition.WheelError("selected wheel digest changed")
    budget.fail(original)
    budget.fail(acquisition.WheelError("peer cancellation"))
    assert budget.failure is original
    with pytest.raises(acquisition.WheelError, match="cancelled"):
        budget.reserve("requests", 1)
    assert budget.current("requests") == 0


def test_peer_failure_while_waiting_for_budget_lock_cannot_admit_another_request() -> None:
    budget = acquisition._Budget(limits(), time.monotonic() + 5)

    class PeerFailsBeforeEntry:
        def __enter__(self) -> None:
            budget.failed.set()

        def __exit__(self, *_args: object) -> Literal[False]:
            return False

    budget.lock = cast(Any, PeerFailsBeforeEntry())
    with pytest.raises(acquisition.WheelError, match="cancelled"):
        budget.reserve("requests", 1)
    assert budget.counts["requests"] == 0


@pytest.mark.parametrize("wrapped", [False, True])
def test_reset_retry_charges_budget_without_changing_the_origin_chain(wrapped: bool) -> None:
    feed = acquisition.WheelFeed("sha256:" + "b" * 64, "https://feed.invalid/simple")
    client = acquisition._Client(limits(), time.monotonic() + 5, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), {})
    response = object()
    calls = []

    def open_request(request: Request, *, timeout: float) -> object:
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            error = ConnectionResetError(10054, "fixture reset")
            raise URLError(error) if wrapped else error
        return response

    cast(Any, client.opener).open = open_request
    result, location, chain = client.request(
        "https://feed.invalid/simple/fixture/", feed, None, "application/json", index=True,
    )
    assert result is response and location == calls[0][0] == calls[1][0]
    assert chain == [hashlib.sha256(b"https://feed.invalid").hexdigest()]
    assert client.requests == 2 and client.redirects == 0


@pytest.mark.parametrize("failure,maximum,expected_calls", [
    ("reset", 4, 2), ("reset", 1, 1), ("tls", 4, 1), ("http", 4, 1), ("missing", 4, 1),
])
def test_retry_is_bounded_and_does_not_retry_other_failures(failure: str, maximum: int, expected_calls: int) -> None:
    feed = acquisition.WheelFeed("sha256:" + "b" * 64, "https://feed.invalid/simple")
    client = acquisition._Client(
        limits(max_requests=maximum), time.monotonic() + 5, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), {},
    )
    calls = []

    def open_request(request: Request, *, timeout: float) -> None:
        calls.append(request.full_url)
        if failure == "reset":
            raise URLError(ConnectionResetError(10054, "fixture reset"))
        if failure == "tls":
            raise URLError(ssl.SSLError("fixture TLS failure"))
        raise HTTPError(request.full_url, 404 if failure == "missing" else 500, "fixture error", cast(Message, {}), None)

    cast(Any, client.opener).open = open_request
    message = (
        "request budget" if maximum == 1 else
        "unavailable" if failure == "missing" else "wheel feed request failed"
    )
    expected_error = acquisition.AcquisitionUnavailable if failure == "missing" else acquisition.WheelError
    with pytest.raises(expected_error, match=message) as raised:
        client.request("https://feed.invalid/simple/fixture/", feed, None, "application/json", index=True)
    assert isinstance(raised.value, acquisition.AcquisitionUnavailable) is (failure == "missing")
    assert len(calls) == expected_calls
    assert client.requests == expected_calls


@pytest.mark.parametrize("boundary", ["deadline", "peer-failure"])
def test_reset_retry_rechecks_the_shared_execution_boundary(boundary: str) -> None:
    feed = acquisition.WheelFeed("sha256:" + "b" * 64, "https://feed.invalid/simple")
    client = acquisition._Client(limits(), time.monotonic() + 5, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), {})
    calls = []

    def open_request(request: Request, *, timeout: float) -> None:
        calls.append(request.full_url)
        if boundary == "deadline":
            client.budget.deadline = time.monotonic() - 1
        else:
            client.budget.fail(acquisition.WheelError("fixture peer failed"))
        raise URLError(ConnectionResetError(10054, "fixture reset"))

    cast(Any, client.opener).open = open_request
    with pytest.raises(acquisition.WheelError, match="deadline|cancelled"):
        client.request("https://feed.invalid/simple/fixture/", feed, None, "application/json", index=True)
    assert len(calls) == 1 and client.requests == 1


def test_redirected_reset_retry_does_not_forward_feed_headers() -> None:
    feed = acquisition.WheelFeed(
        "sha256:" + "b" * 64, "https://feed.invalid/simple", (("X-Fixture", "value"),),
    )
    client = acquisition._Client(limits(), time.monotonic() + 5, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), {})
    response = object()
    calls = []

    def open_request(request: Request, *, timeout: float) -> object:
        calls.append((request.full_url, dict(request.header_items())))
        if len(calls) == 1:
            raise URLError(ConnectionResetError(10054, "fixture reset"))
        if len(calls) == 2:
            raise HTTPError(request.full_url, 302, "redirect", cast(Message, {"Location": "https://other.invalid/file"}), None)
        if len(calls) == 3:
            raise URLError(ConnectionResetError(10054, "fixture reset"))
        return response

    cast(Any, client.opener).open = open_request
    result, location, chain = client.request(
        "https://feed.invalid/simple/fixture/", feed, None, "application/json", index=True,
    )
    assert result is response and location == "https://other.invalid/file"
    assert chain == [
        hashlib.sha256(b"https://feed.invalid").hexdigest(),
        hashlib.sha256(b"https://other.invalid").hexdigest(),
    ]
    assert all(headers.get("X-fixture") == "value" for _, headers in calls[:2])
    assert all("X-fixture" not in headers for _, headers in calls[2:])
    assert client.requests == 4 and client.redirects == 1


@pytest.mark.parametrize("outcome", ["eligible", "foreign-origin", "withdrawn", "yanked", "digest"])
def test_retained_feed_admission_uses_exact_first_owner_metadata_without_download(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    digest = "a" * 64
    expected = {"name": "fixture", "filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + digest}
    entry: dict[str, Any] = {
        "filename": expected["filename"], "url": "/wheels/" + expected["filename"],
        "hashes": {"sha256": digest}, "upload-time": "2020-01-01T00:00:00Z",
        "yanked": False,
    }
    if outcome == "foreign-origin":
        entry["url"] = "https://foreign.invalid/wheel.whl"
    elif outcome == "withdrawn":
        entry["withdrawn"] = True
    elif outcome == "yanked":
        entry["yanked"] = True
    elif outcome == "digest":
        entry["hashes"]["sha256"] = "f" * 64
    content = json.dumps({
        "meta": {"api-version": "1.1"}, "name": "fixture", "files": [entry],
    }).encode()
    calls = []

    class Response:
        class headers:
            @staticmethod
            def get_content_type() -> str:
                return "application/vnd.pypi.simple.v1+json"

        def close(self) -> None:
            pass

    class Client:
        def request(self, location: str, _feed: acquisition.WheelFeed, _origins: set[str] | None, _accept: str, *, index: bool=False) -> tuple[Response, str, list[str]]:
            assert index
            calls.append(location)
            return Response(), location, ["0" * 64]

        def read(self, _response: Response, _limit: int) -> tuple[bytes, str, int]:
            return content, "sha256:" + "0" * 64, len(content)

    class Budget:
        def check(self) -> None:
            pass

    monkeypatch.setattr(acquisition, "_Client", lambda *_args: Client())
    feed = acquisition.WheelFeed("sha256:" + "b" * 64, "https://feed.invalid/simple")
    arguments = (
        Path("unused"), expected, (feed,), {"https://allowed.invalid"},
        {"fixture": datetime.fromisoformat("2025-01-01T00:00:00+00:00")},
        limits(), time.monotonic() + 5, cast(ssl.SSLContext, None), cast(acquisition._Budget, Budget()),
    )
    if outcome == "eligible":
        result = acquisition._download_one(*arguments, retained_size=123)
        assert result["sha256"] == expected["sha256"] and result["size"] == 123
        assert result["source_origins"] == ["0" * 64]
    else:
        error = acquisition.AcquisitionUnavailable if outcome in ("withdrawn", "yanked") else acquisition.WheelError
        with pytest.raises(error):
            acquisition._download_one(*arguments, retained_size=123)
    assert calls == ["https://feed.invalid/simple/fixture/"]


@pytest.mark.parametrize("outcome", [
    "success", "snapshot-deadline", "wrong-digest", "over-budget", "worker-size", "worker-download", "changed",
])
def test_retained_admission_binds_worker_facts_to_live_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    filename, digest = "fixture-1.0-py3-none-any.whl", "sha256:" + "a" * 64
    expected = {"name": "fixture", "filename": filename, "sha256": digest}
    size = 11 if outcome == "over-budget" else 4
    deadline = time.monotonic() + 30
    snapshot_deadline = deadline - 10 if outcome == "snapshot-deadline" else deadline + 30
    snapshot = acquisition.WheelSnapshot(
        {filename: tmp_path / filename}, {}, {filename: cast("tuple[stat_result, stat_result, str, int]", (None, None, digest, size))}, snapshot_deadline,
    )
    checks, workers = [], []

    def verify(current: acquisition.WheelSnapshot) -> None:
        checks.append(current._deadline)
        if outcome == "changed" and len(checks) == 2:
            raise acquisition.WheelError("retained wheel changed")

    @contextmanager
    def temporary(_: str) -> Iterator[Path]:
        yield tmp_path

    def worker(_root: Path, selected: list[dict[str, Any]], _feeds: tuple[acquisition.WheelFeed, ...], _origins: set[str], _cutoffs: dict[str, datetime], _limits: acquisition.AcquisitionLimits, _deadline: float, _context: ssl.SSLContext, _procutil: ModuleType,
               *, retained_sizes: dict[str, int]) -> dict[str, Any]:
        workers.append(True)
        assert _deadline == min(deadline, snapshot_deadline)
        assert selected == [expected] and retained_sizes == {filename: size}
        return {
            "files": [{
                "filename": filename, "sha256": digest,
                "size": size + (outcome == "worker-size"), "feed": "sha256:" + "b" * 64,
                "uploaded": "2020-01-01T00:00:00+00:00", "source_origins": ["0" * 64],
                "yanked": False, "withdrawn": False,
            }],
            "metadata_bytes": 1, "wheel_bytes": int(outcome == "worker-download"),
            "request_count": 1, "redirect_count": 0, "control_bytes": 3,
        }

    monkeypatch.setattr(acquisition.WheelSnapshot, "verify", verify)
    monkeypatch.setattr(dependencies, "_temporary_inputs", temporary)
    monkeypatch.setattr(dependencies, "_private_retained_root", lambda path: path)
    monkeypatch.setattr(acquisition, "_run_worker", worker)
    if outcome == "wrong-digest":
        expected = {**expected, "sha256": "sha256:" + "c" * 64}
    options: dict[str, Any] = {
        "snapshot": snapshot,
        "feeds": (acquisition.WheelFeed("sha256:" + "b" * 64, "https://feed.invalid/simple"),),
        "download_origins": (),
        "cutoffs": {"fixture": datetime.fromisoformat("2025-01-01T00:00:00+00:00")},
        "policy_id": "sha256:" + "d" * 64, "limits": limits(),
        "deadline": deadline, "ssl_context": ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        "proxies": {}, "procutil": ModuleType("test_procutil"),
    }
    if outcome in ("success", "snapshot-deadline"):
        with acquisition.admit_retained_wheel_inputs([expected], **options) as admitted:
            evidence = admitted.evidence()
            assert admitted.snapshot.paths == snapshot.paths
            assert evidence["retained_wheel_bytes"] == size
            assert evidence["downloaded_wheel_bytes"] == 0
            assert evidence["temporary_highwater_bytes"] == size + 3
        assert checks == [min(deadline, snapshot_deadline)] * 3 and workers == [True]
    else:
        with pytest.raises(acquisition.WheelError), acquisition.admit_retained_wheel_inputs([expected], **options):
            pytest.fail("invalid retained admission was exposed")
        assert bool(workers) == (outcome in ("worker-size", "worker-download", "changed"))


@pytest.mark.parametrize("workers,count,parallel", [(1, 3, False), (8, 1, False), (8, 3, True)])
def test_download_orchestration_bounds_workers_and_preserves_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workers: int, count: int, parallel: bool,
) -> None:
    calls = []

    class Pool:
        def __init__(self, *, max_workers: int) -> None:
            assert parallel and max_workers == count
            calls.append("pool")

        def __enter__(self) -> Pool:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("settled")

        def map(self, function: Callable[[_T], _U], items: Iterable[_T]) -> Iterator[_U]:
            return map(function, items)

    def download(
        _root: Path, item: int, _feeds: object, _origins: object, _cutoffs: object, _limits: object, _deadline: float, _context: object, budget: acquisition._Budget,
    ) -> dict[str, int]:
        budget.reserve("requests", 1)
        return {"selected": item}

    monkeypatch.setattr(acquisition, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(acquisition, "_download_one", download)
    selected = list(reversed(range(count)))
    result = acquisition._download(
        tmp_path / "downloads", cast(list[dict[str, Any]], selected), (), cast(set[str], ()), {}, limits(max_workers=workers),
        time.monotonic() + 5, cast(ssl.SSLContext, None), 0,
    )
    assert result["files"] == [{"selected": value} for value in selected]
    assert result["request_count"] == count
    assert calls == (["pool", "settled"] if parallel else [])


def test_download_reports_original_failure_only_after_pool_settlement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = acquisition.WheelError("request budget exceeded")
    settled = []

    class Pool:
        def __init__(self, **_options: object) -> None:
            pass

        def __enter__(self) -> Pool:
            return self

        def __exit__(self, *_args: object) -> None:
            settled.append(True)

        def map(self, function: Callable[[_T], _U], items: Iterable[_T]) -> Iterator[_U]:
            return map(function, items)

    def download(*args: Any) -> None:
        args[-1].fail(original)
        raise acquisition.WheelError("peer cancellation")

    monkeypatch.setattr(acquisition, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(acquisition, "_download_one", download)
    with pytest.raises(acquisition.WheelError) as raised:
        acquisition._download(
            tmp_path / "downloads", cast(list[dict[str, Any]], [1, 2]), (), cast(set[str], ()), {}, limits(),
            time.monotonic() + 5, cast(ssl.SSLContext, None), 0,
        )
    assert raised.value is original and settled == [True]
