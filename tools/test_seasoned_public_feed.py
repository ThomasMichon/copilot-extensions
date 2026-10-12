from __future__ import annotations

import http.client
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "seasoned_public_feed.py"

sys.path.insert(0, str(MODULE_PATH.parent))
spf = importlib.import_module("seasoned_public_feed")

_NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def _releases_payload(releases: dict) -> bytes:
    return json.dumps({"releases": releases}).encode("utf-8")


def _file(upload_time: str, *, yanked: bool = False) -> dict:
    return {"upload_time_iso_8601": upload_time, "yanked": yanked}

def test_release_retains_only_selected_seasoned_file_hashes():
    old = {**_file("2026-01-01T00:00:00Z"), "digests": {"sha256": "a" * 64}}
    late = {**_file("2026-10-09T00:00:00Z"), "digests": {"sha256": "b" * 64}}
    payload = _releases_payload({"1.0": [old], "2.0": [late]})
    release = spf.resolve_seasoned_public_release("pkg", now=_NOW, fetch=lambda url: payload)
    assert release.version == "1.0"
    assert release.hashes == ("a" * 64,)


@pytest.mark.parametrize("digests", [None, {}, {"sha256": ""}, {"sha256": "g" * 64}, {"sha256": "a" * 63}])
def test_release_hash_missing_or_invalid_fails_closed(digests):
    payload = _releases_payload({
        "1.0": [{**_file("2026-01-01T00:00:00Z"), "digests": digests}],
    })
    with pytest.raises(spf.SeasonedVersionError, match="SHA-256"):
        spf.resolve_seasoned_public_release("pkg", now=_NOW, fetch=lambda url: payload)


def test_release_hashes_are_normalized_deduplicated_and_sorted():
    files = [{**_file("2026-01-01T00:00:00Z"), "digests": {"sha256": value}}
             for value in ("b" * 64, "A" * 64, "a" * 64)]
    release = spf.resolve_seasoned_public_release(
        "pkg", now=_NOW, fetch=lambda url: _releases_payload({"1.0": files}),
    )
    assert release.hashes == ("a" * 64, "b" * 64)


class TestResolveSeasonedPublicVersion:
    @pytest.mark.parametrize("versions", [("1.0", "1.0.0"), ("1.0.0", "1.0")])
    def test_equivalent_version_spelling_selection_is_deterministic(self, versions):
        payload = _releases_payload({v: [_file("2026-01-01T00:00:00Z")] for v in versions})
        assert spf.resolve_seasoned_public_version("pkg", now=_NOW, fetch=lambda url: payload) == "1.0.0"

    def test_url_error_wrapped_timeout_keeps_timeout_diagnostic(self, monkeypatch):
        failure = spf.urllib.error.URLError(TimeoutError("connection stalled"))

        def failing_urlopen(*args, **kwargs):
            raise failure

        monkeypatch.setattr(spf.urllib.request, "urlopen", failing_urlopen)
        with pytest.raises(spf.SeasonedVersionError, match="timed out") as caught:
            spf.resolve_seasoned_public_version("pkg", now=_NOW)
        assert caught.value.__cause__ is failure

    @pytest.mark.parametrize("package", ["", "bad name", "../pkg", "a/b", "a?b", "a#b", "a%2Fb", "-pkg", "pkg-", None])
    def test_invalid_package_rejected_before_fetch(self, package):
        def unexpected_fetch(url):
            pytest.fail("invalid distribution name must not make a request")

        with pytest.raises(spf.SeasonedVersionError, match="distribution name"):
            spf.resolve_seasoned_public_version(package, fetch=unexpected_fetch)

    def test_distribution_name_normalized_in_request(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return _releases_payload({"1.0": [_file("2026-01-01T00:00:00Z")]})

        assert spf.resolve_seasoned_public_version("Some_Pkg.Name", now=_NOW, fetch=fetch) == "1.0"
        assert urls == ["https://pypi.org/pypi/some-pkg-name/json"]

    def test_response_read_timeout_wrapped(self, monkeypatch):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                raise TimeoutError("response read stalled")

        monkeypatch.setattr(spf.urllib.request, "urlopen", lambda *args, **kwargs: Response())
        with pytest.raises(spf.SeasonedVersionError, match="timed out") as caught:
            spf.resolve_seasoned_public_version("pkg", now=_NOW)
        assert isinstance(caught.value.__cause__, TimeoutError)

    def test_invalid_unicode_wrapped(self):
        with pytest.raises(spf.SeasonedVersionError, match="not valid JSON") as caught:
            spf.resolve_seasoned_public_version("pkg", fetch=lambda url: b"\xff")
        assert isinstance(caught.value.__cause__, UnicodeDecodeError)

    @pytest.mark.parametrize("failure", [http.client.IncompleteRead(b"partial"), ConnectionResetError("reset")])
    def test_response_read_failures_wrapped(self, monkeypatch, failure):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                raise failure

        monkeypatch.setattr(spf.urllib.request, "urlopen", lambda *args, **kwargs: Response())
        with pytest.raises(spf.SeasonedVersionError, match="response read failed") as caught:
            spf.resolve_seasoned_public_version("pkg", now=_NOW)
        assert caught.value.__cause__ is failure

    @pytest.mark.parametrize("yank_fields", [{}, {"yanked": None}, {"yanked": 0}, {"yanked": ""}, {"yanked": "false"}])
    def test_unknown_yank_status_disqualifies_release(self, yank_fields):
        malformed_file = {"upload_time_iso_8601": "2026-01-01T00:00:00Z", **yank_fields}
        payload = _releases_payload({
            "2.0": [_file("2026-01-01T00:00:00Z"), malformed_file],
            "1.0": [_file("2026-01-01T00:00:00Z")],
        })
        assert spf.resolve_seasoned_public_version("pkg", now=_NOW, fetch=lambda url: payload) == "1.0"

    def test_picks_newest_seasoned_release(self):
        payload = _releases_payload(
            {
                "1.0.0": [_file("2026-01-01T00:00:00.000000Z")],
                "1.5.0": [_file("2026-09-20T00:00:00.000000Z")],  # 20 days old
                "2.0.0": [_file("2026-10-05T00:00:00.000000Z")],  # 5 days old, too new
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.5.0"

    def test_excludes_yanked_release_even_if_old_enough(self):
        payload = _releases_payload(
            {
                "1.0.0": [_file("2026-01-01T00:00:00.000000Z")],
                "2.0.0": [_file("2026-01-01T00:00:00.000000Z", yanked=True)],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.0.0"

    def test_trailing_zero_normalization_picks_post_release_over_base(self):
        # 1.0.post1 is newer than 1.0.0 under PEP 440 (trailing ".0"
        # segments are insignificant: 1.0 == 1.0.0), but a RAW,
        # un-normalized tuple comparison would rank (1, 0, 0) above
        # (1, 0) and wrongly return the base release instead.
        payload = _releases_payload(
            {
                "1.0.0": [_file("2026-01-01T00:00:00.000000Z")],
                "1.0.post1": [_file("2026-01-02T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.0.post1"

    def test_excludes_prerelease_even_if_old_enough(self):
        payload = _releases_payload(
            {
                "1.0.0": [_file("2026-01-01T00:00:00.000000Z")],
                "2.0.0rc1": [_file("2026-01-01T00:00:00.000000Z")],
                "2.0.0.dev0": [_file("2026-01-01T00:00:00.000000Z")],
                "2.0.0b2": [_file("2026-01-01T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.0.0"

    def test_accepts_seasoned_post_release_as_newest(self):
        # A post release is stable (unlike pre/dev) -- a toolchain
        # package's own corrective `.postN` republish must not be
        # silently skipped in favor of an older base version.
        payload = _releases_payload(
            {
                "1.0.0": [_file("2026-01-01T00:00:00.000000Z")],
                "1.0.0.post1": [_file("2026-01-02T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.0.0.post1"

    def test_negative_min_age_days_rejected(self):
        payload = _releases_payload({"1.0.0": [_file("2026-10-09T00:00:00.000000Z")]})
        with pytest.raises(spf.SeasonedVersionError, match="non-negative integer"):
            spf.resolve_seasoned_public_version(
                "pkg", min_age_days=-1, now=_NOW, fetch=lambda url: payload
            )

    @pytest.mark.parametrize("age", [float("nan"), float("inf"), 1.5, "7", None, True, False])
    def test_non_integer_age_rejected_before_fetch(self, age):
        def unexpected_fetch(url):
            pytest.fail("invalid age must not make a request")

        with pytest.raises(spf.SeasonedVersionError, match="non-negative integer"):
            spf.resolve_seasoned_public_version("pkg", min_age_days=age, fetch=unexpected_fetch)

    def test_wrong_json_root_shape_raises_seasoned_version_error(self):
        payload = json.dumps([1, 2, 3]).encode("utf-8")
        with pytest.raises(spf.SeasonedVersionError, match="no 'releases' object"):
            spf.resolve_seasoned_public_version("pkg", now=_NOW, fetch=lambda url: payload)

    def test_offsetless_upload_timestamp_excludes_the_version(self):
        payload = _releases_payload(
            {
                # Malformed: no timezone offset on this file's timestamp.
                "1.0.0": [{"upload_time_iso_8601": "2026-01-01T00:00:00", "yanked": False}],
                "0.9.0": [_file("2026-01-01T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "0.9.0"

    def test_age_measured_from_newest_file_not_oldest(self):
        # A version re-uploaded recently (e.g. a new wheel added to an
        # already-published release) must be measured from its NEWEST
        # file, not its oldest -- this resolver returns a bare version,
        # not a pinned file hash, so an install could select that fresh
        # file; the seasoning guarantee only holds if every file for the
        # version clears the age floor.
        payload = _releases_payload(
            {
                "1.0.0": [
                    _file("2026-01-01T00:00:00.000000Z"),
                    _file("2026-10-09T00:00:00.000000Z"),  # fresh file, 1 day old
                ],
                "0.9.0": [_file("2026-01-01T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        # 1.0.0 is excluded (its newest file is only 1 day old); 0.9.0 is
        # the newest version whose EVERY file clears the floor.
        assert got == "0.9.0"

    def test_exact_boundary_age_is_accepted(self):
        exactly_seven_days_ago = "2026-10-03T00:00:00.000000Z"
        payload = _releases_payload({"1.0.0": [_file(exactly_seven_days_ago)]})
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.0.0"

    def test_no_qualifying_release_raises(self):
        payload = _releases_payload({"1.0.0": [_file("2026-10-09T00:00:00.000000Z")]})
        with pytest.raises(spf.SeasonedVersionError, match="no release at least"):
            spf.resolve_seasoned_public_version(
                "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
            )

    def test_empty_releases_raises(self):
        payload = _releases_payload({})
        with pytest.raises(spf.SeasonedVersionError, match="no release at least"):
            spf.resolve_seasoned_public_version(
                "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
            )

    def test_version_with_no_uploaded_files_is_skipped(self):
        payload = _releases_payload(
            {
                "1.0.0": [],
                "0.9.0": [_file("2026-01-01T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "0.9.0"

    def test_unparseable_version_string_is_skipped_not_guessed(self):
        payload = _releases_payload(
            {
                "not-a-version": [_file("2026-01-01T00:00:00.000000Z")],
                "1.0.0": [_file("2026-01-01T00:00:00.000000Z")],
            }
        )
        got = spf.resolve_seasoned_public_version(
            "pkg", min_age_days=7, now=_NOW, fetch=lambda url: payload
        )
        assert got == "1.0.0"

    def test_malformed_json_raises(self):
        with pytest.raises(spf.SeasonedVersionError, match="not valid JSON"):
            spf.resolve_seasoned_public_version(
                "pkg", now=_NOW, fetch=lambda url: b"not json at all {"
            )

    def test_missing_releases_key_raises(self):
        payload = json.dumps({"info": {}}).encode("utf-8")
        with pytest.raises(spf.SeasonedVersionError, match="no 'releases' object"):
            spf.resolve_seasoned_public_version("pkg", now=_NOW, fetch=lambda url: payload)

    def test_http_error_raises_seasoned_version_error(self):
        import urllib.error

        def failing_fetch(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with pytest.raises(spf.SeasonedVersionError, match="HTTP 404"):
            spf.resolve_seasoned_public_version("pkg", now=_NOW, fetch=failing_fetch)

    def test_url_error_raises_seasoned_version_error(self):
        import urllib.error

        def failing_fetch(url):
            raise urllib.error.URLError("network unreachable")

        with pytest.raises(spf.SeasonedVersionError, match="could not reach"):
            spf.resolve_seasoned_public_version("pkg", now=_NOW, fetch=failing_fetch)

    def test_naive_now_rejected(self):
        payload = _releases_payload({"1.0.0": [_file("2026-01-01T00:00:00.000000Z")]})
        with pytest.raises(spf.SeasonedVersionError, match="naive datetime"):
            spf.resolve_seasoned_public_version(
                "pkg", now=datetime(2026, 10, 10), fetch=lambda url: payload  # noqa: DTZ001 -- deliberately naive, exercising the rejection path
            )

    def test_real_default_fetch_sets_user_agent_and_timeout(self, monkeypatch):
        # Doesn't hit the real network -- stubs urllib.request.urlopen to
        # prove `_default_fetch` builds the request/timeout correctly,
        # without this test depending on pypi.org being reachable.
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                return b'{"releases": {}}'

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return _FakeResponse()

        monkeypatch.setattr(spf.urllib.request, "urlopen", fake_urlopen)
        result = spf._default_fetch("https://pypi.org/pypi/pkg/json")
        assert result == b'{"releases": {}}'
        assert captured["url"] == "https://pypi.org/pypi/pkg/json"
        assert captured["timeout"] == spf._REQUEST_TIMEOUT_SECONDS
        assert any(k.lower() == "user-agent" for k in captured["headers"])


class TestParseReleaseVersion:
    @pytest.mark.parametrize(
        "version_str,expected",
        [
            ("1.0.0", (0, (1,), -1)),
            ("2.3", (0, (2, 3), -1)),
            ("1!2.3.4", (1, (2, 3, 4), -1)),
            ("10.20.30", (0, (10, 20, 30), -1)),
            ("1.0.0.post1", (0, (1,), 1)),
            ("1!2.3.4.post10", (1, (2, 3, 4), 10)),
            ("1.2.0", (0, (1, 2), -1)),
            ("0.0.0", (0, (0,), -1)),
        ],
    )
    def test_parses_clean_release_versions(self, version_str, expected):
        assert spf._parse_release_version(version_str) == expected

    def test_trailing_zeros_make_equivalent_releases_equal(self):
        assert spf._parse_release_version("1.0") == spf._parse_release_version("1.0.0")
        assert spf._parse_release_version("1.0") == spf._parse_release_version("1")

    def test_bare_release_sorts_before_its_own_post_release(self):
        base = spf._parse_release_version("1.0.0")
        post = spf._parse_release_version("1.0.0.post1")
        assert base < post

    @pytest.mark.parametrize(
        "version_str",
        ["1.0.0rc1", "1.0.0.dev0", "1.0.0b2", "1.0.0a1", "not-a-version", ""],
    )
    def test_rejects_non_clean_versions(self, version_str):
        assert spf._parse_release_version(version_str) is None
