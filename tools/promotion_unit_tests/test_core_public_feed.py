"""Pure controls for the explicit nonsecret producer identity."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import core_native_public_feed as public
from runtime_profile import ProfileError


URL = "https://feed.example/simple/"
SOURCE = {
    "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40,
    "core_payload_sha256": "d" * 64, "core_artifact_id": "sha256:" + "e" * 64,
}


def _outer():
    return {
        "owner_policy": "sha256:" + "1" * 64, "feeds": ["sha256:" + "2" * 64],
        "delivery_policy": public.DELIVERY_POLICY, "download_origins": [],
        "transport": {"certificates": []}, "seasoning": {"global": "7d"},
        "index_strategy": "FirstIndex", "dependency_selection": {"resolution": "Highest"},
        "profile_policy": "native-source-profile",
    }


def test_public_identity_is_stable_without_keying_on_the_local_index_name():
    identify = public._public_identity(URL, SOURCE)
    one = {"source": SOURCE, "index": [URL, "first"], "downloadOrigins": []}
    two = {"source": SOURCE, "index": [URL, "second"], "downloadOrigins": []}
    assert identify(json.dumps(one)) == identify(json.dumps(two))
    one = {"name": "first", "url": URL, "authenticate": "Never", "headers": []}
    two = dict(one, name="second")
    assert identify(json.dumps(one)) == identify(json.dumps(two))
    changed = _outer()
    before = identify(json.dumps(changed))
    changed["seasoning"]["global"] = "14d"
    assert identify(json.dumps(changed)) != before


def test_public_identity_tracks_core_content_not_its_source_container():
    source = deepcopy(SOURCE)
    original = {"source": SOURCE, "index": [URL, None], "downloadOrigins": []}
    source.update(source_commit="1" * 40, source_tree="2" * 40, main_head="3" * 40)
    moved = {"source": source, "index": [URL, None], "downloadOrigins": []}
    assert public._public_identity(URL, SOURCE)(json.dumps(original)) == (
        public._public_identity(URL, source)(json.dumps(moved))
    )
    source["core_artifact_id"] = "sha256:" + "4" * 64
    assert public._public_identity(URL, SOURCE)(json.dumps(original)) != (
        public._public_identity(URL, source)(json.dumps(moved))
    )


@pytest.mark.parametrize("url", [
    "http://feed.example/simple/", "https://u:p@feed.example/simple/",
    "https://feed.example/simple/?token=sample", "https://feed.example/simple/#fragment",
], ids=["http", "userinfo", "query", "fragment"])
def test_public_identity_refuses_nonpublic_url_shapes(url):
    with pytest.raises(ProfileError):
        public._public_identity(url, SOURCE)


@pytest.mark.parametrize("change", ["header", "other-feed", "extra-field", "origins", "source-path"])
def test_public_identity_never_hashes_secret_or_unselected_inputs(change):
    source = deepcopy(SOURCE)
    if change == "source-path":
        source["source_tree"] = "private-path"
        with pytest.raises(ProfileError):
            public._public_identity(URL, source)
        return
    identify = public._public_identity(URL, source)
    data = {"name": "index", "url": URL, "authenticate": "Auto", "headers": []}
    if change == "header":
        data["headers"] = [["X-Private", "dummy"]]
    elif change == "other-feed":
        data["url"] = "https://other.example/simple/"
    elif change == "extra-field":
        data["other"] = "dummy"
    else:
        data = _outer()
        data["download_origins"] = ["https://other.example"]
    with pytest.raises(ProfileError):
        identify(json.dumps(data))


def test_public_capture_requires_the_independently_selected_feed(monkeypatch):
    selected = SimpleNamespace(identity=SimpleNamespace(document=lambda: SOURCE))
    transaction = SimpleNamespace(environment={})
    monkeypatch.setattr(public, "_validated_trusted_index_url", lambda _environment: (URL, "fixture"))
    observed = {}

    def capture(*_args, **kwargs):
        identify = kwargs["opaque_identity"]
        observed["identity"] = identify(json.dumps({
            "name": "fixture", "url": URL, "authenticate": "Never", "headers": [],
        }))
        return SimpleNamespace(
            feeds=(SimpleNamespace(index_url=URL, headers=()),), proxies={},
            policy_id="sha256:" + "3" * 64,
        )

    monkeypatch.setattr(public, "capture_native_feed_inputs", capture)
    result = public.public_feed_policy(
        selected, transaction, ["original", "retained"], uv="uv", url=URL,
        wheels=[], exclusions=(), deadline=10,
    )
    assert result.policy_id == "sha256:" + "3" * 64
    assert len(observed["identity"]) == 64
    with pytest.raises(ProfileError, match="differs"):
        public.public_feed_policy(
            selected, transaction, ["original", "retained"], uv="uv",
            url="https://other.example/simple/", wheels=[], exclusions=(), deadline=10,
        )


@pytest.mark.parametrize("change", ["headers", "other-feed", "multiple", "proxy", "invalid-url"])
def test_public_preflight_refuses_nonanonymous_or_unselected_feeds(monkeypatch, change):
    selected = SimpleNamespace(identity=SimpleNamespace(document=lambda: SOURCE))
    transaction = SimpleNamespace(environment={})
    feed = SimpleNamespace(index_url=URL, headers=())
    inputs = SimpleNamespace(feeds=(feed,), proxies={})
    monkeypatch.setattr(public, "_validated_trusted_index_url", lambda _environment: (URL, "fixture"))
    public.require_public_feed(selected, transaction, URL, inputs)
    if change == "headers":
        feed.headers = (("X-Fixture", "value"),)
    elif change == "other-feed":
        feed.index_url = "https://other.example/simple/"
    elif change == "multiple":
        inputs.feeds = (feed, feed)
    elif change == "proxy":
        inputs.proxies = {"https": "fixture"}
    else:
        url = URL + "?query=value"
        monkeypatch.setattr(public, "_validated_trusted_index_url", lambda _environment: (url, "fixture"))
        with pytest.raises(ProfileError):
            public.require_public_feed(selected, transaction, url, inputs)
        return
    with pytest.raises(ProfileError):
        public.require_public_feed(selected, transaction, URL, inputs)
