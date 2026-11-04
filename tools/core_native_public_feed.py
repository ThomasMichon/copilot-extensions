"""Reproducible producer identity for an explicitly nonsecret anonymous feed."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

from governed_feed_trust import _validated_trusted_index_url
from installer_native_feed import capture_native_feed_inputs
from installer_wheel_acquisition import DELIVERY_POLICY, _url
from runtime_profile import ProfileError

_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_KEYS = {
    "source_commit", "source_tree", "main_head", "core_payload_sha256", "core_artifact_id",
}
_OUTER_KEYS = {
    "owner_policy", "feeds", "delivery_policy", "download_origins", "transport",
    "seasoning", "index_strategy", "dependency_selection", "profile_policy",
}


def _public_identity(url: str, source: dict):
    """Return a nonsecret-only callback for the existing native feed owner."""
    try:
        normalized, _origin = _url(url)
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        raise ProfileError("public feed identity needs an ordinary HTTPS URL") from None
    if normalized != url or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ProfileError("public feed identity refuses credentials, query and fragment")
    if (
        not isinstance(source, dict) or set(source) != _SOURCE_KEYS
        or any(not isinstance(source[key], str) or _COMMIT.fullmatch(source[key]) is None
               for key in ("source_commit", "source_tree", "main_head"))
        or not isinstance(source["core_payload_sha256"], str)
        or _HEX.fullmatch(source["core_payload_sha256"]) is None
        or not isinstance(source["core_artifact_id"], str)
        or _SHA.fullmatch(source["core_artifact_id"]) is None
    ):
        raise ProfileError("public feed identity requires a bounded core source")

    def identify(text: str) -> str:
        try:
            if not isinstance(text, str) or len(text) > 16 << 20:
                raise ValueError
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError
            keys = set(data)
            if keys == {"source", "index", "downloadOrigins"}:
                if (
                    data["source"] != source or not isinstance(data["index"], list)
                    or len(data["index"]) != 2 or data["index"][0] != url
                    or data["downloadOrigins"] != []
                ):
                    raise ValueError
                data["index"][1] = None
            elif keys == {"name", "url", "authenticate", "headers"}:
                if (
                    data["url"] != url or data["headers"] != []
                    or data["authenticate"] not in ("Auto", "Never")
                ):
                    raise ValueError
                data["name"] = None
            elif keys in (_OUTER_KEYS, _OUTER_KEYS | {"excluded_local_packages"}):
                if (
                    not isinstance(data["owner_policy"], str) or _SHA.fullmatch(data["owner_policy"]) is None
                    or not isinstance(data["feeds"], list) or len(data["feeds"]) != 1
                    or not isinstance(data["feeds"][0], str) or _SHA.fullmatch(data["feeds"][0]) is None
                    or data["delivery_policy"] != DELIVERY_POLICY or data["download_origins"] != []
                    or data["index_strategy"] != "FirstIndex"
                    or data["profile_policy"] != "native-source-profile"
                ):
                    raise ValueError
            else:
                raise ValueError
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            return hashlib.sha256(("public-anonymous-feed-v1\0" + canonical).encode("ascii")).hexdigest()
        except (KeyError, TypeError, ValueError, RecursionError):
            raise ProfileError("public feed identity encountered non-public or unsupported inputs") from None

    return identify


def public_feed_policy(
    selected, transaction, projections, *, uv, url, wheels, exclusions, deadline,
):
    """Capture a stable public identity without changing local feed admission."""
    trusted = _validated_trusted_index_url(transaction.environment)
    if trusted is None or trusted[0] != url:
        raise ProfileError("public feed differs from the affirmatively trusted native index")
    source = selected.identity.document()
    identify = _public_identity(url, source)
    policy = {
        "policyId": "sha256:" + identify(json.dumps({
            "source": source, "index": trusted, "downloadOrigins": (),
        }, sort_keys=True)),
        "selection": {"downloadOrigins": []},
    }
    feed = capture_native_feed_inputs(
        transaction, projections[1], projections[0], None, policy, uv, deadline,
        wheels=wheels, profile_policy="native-source-profile",
        opaque_identity=identify, excluded_local_packages=exclusions,
    )
    if len(feed.feeds) != 1 or feed.feeds[0].index_url != url or feed.feeds[0].headers or feed.proxies:
        raise ProfileError("public feed must be anonymous and match the selected index")
    return feed
