"""Provider creation retains the actual published source and target fences."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import re
import time
import uuid

from . import pr_publish, tracking
from .pr_transport_fence import Head
from .providers.base import PRProvider, PRScope, ProviderError


@contextmanager
def creation_guard(provider: PRProvider, scope: PRScope, *, token: str | None) -> Iterator[None]:
    admitted = replace(scope)
    endpoint = provider.authority_endpoint(admitted.api_base)
    owner, separator, branch = admitted.head.partition(":")
    branch = branch if separator else owner
    target = Head(pr_publish._identity("https://" + endpoint + "/" + admitted.repo, ""), admitted.head)
    identity = pr_publish.canonical_head_identity(admitted.head_identity or target.identity)
    parts = identity.split("/")
    if (
        len(parts) != 3 or not all(parts) or parts[0].casefold() != target.identity.partition("/")[0]
        or not branch or ":" in branch or branch.startswith("refs/")
        or any(ord(char) < 33 for char in branch)
        or (separator and (not admitted.head_identity or parts[1].casefold() != owner.casefold()))
        or (not separator and identity.casefold() != target.identity)
    ):
        raise ProviderError("Provider creation needs the exact published head repository and branch.")
    if admitted.expected_head_sha and not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", admitted.expected_head_sha):
        raise ProviderError("Provider creation needs the exact published head object.")
    heads = sorted({target, Head(identity, branch)}, key=lambda head: head.directory)
    with ExitStack() as lifetime:
        deadline = time.monotonic() + pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S
        try:
            for head in heads:
                lifetime.enter_context(head.guard(timeout=max(0, deadline - time.monotonic())))
        except (OSError, ValueError, TimeoutError) as exc:
            raise ProviderError(f"Provider creation is fenced: {exc}") from exc
        if admitted.expected_head_sha:
            observe = getattr(provider, "observe_branch", None)
            if not callable(observe):
                raise ProviderError("Provider cannot recheck the published head before creation.")
            actual = observe(
                "/".join(parts[1:]), branch, api_base=admitted.api_base, token=token,
            )
            if (
                not isinstance(actual, dict)
                or not isinstance(actual.get("repository"), str)
                or actual["repository"].casefold() != "/".join(parts[1:]).casefold()
                or actual.get("branch") != branch or actual.get("head_sha") != admitted.expected_head_sha
            ):
                raise ProviderError("Published head changed before provider creation admission.")
        if scope != admitted or provider.authority_endpoint(admitted.api_base) != endpoint:
            raise ProviderError("Provider creation binding changed before admission.")
        for head in heads:
            tracking._atomic_write(head.directory / "generation", uuid.uuid4().hex)
        yield
