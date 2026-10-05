"""Provider-tagged payload-ref parsing for reviewer-loop PR targets.

Reviewer-loop tasks carry an opaque ``payload_ref`` owned by the emitter.
For the built-in provider-backed review flows, that ref names one pull
request on one forge and is the only stable handle the reviewer lifecycle
can use to look up persisted provider observations later (for stale-exit
checks, polling fallback, and similar read-side behavior).

This module keeps that parsing provider-aware but minimal:

- GitHub refs keep their established ``github-pr:owner/repo#123`` shape.
- Azure DevOps adds ``azure-devops-pr:organization/project/repository#123``.
- Gitea is parsed structurally for the future stub/factory path, but no live
  adapter exists yet.

The parse result is pure data: a provider tag, a provider-specific repo key,
and the PR number. Callers decide what to do with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewTargetRef:
    """One provider-backed reviewer target named by ``payload_ref``."""

    provider: str
    repo: str
    number: int

    @property
    def observation_repo(self) -> str:
        """Provider-disambiguated key for persisted observation storage.

        GitHub keeps its historical bare ``owner/repo`` key for
        backward-compatibility with the already-shipped store. Additional
        providers carry their provider tag in-band so two forges with the
        same repo-shaped path cannot resolve each other's observations.
        """
        if self.provider == "github":
            return self.repo
        return f"{self.provider}:{self.repo}"


_REF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "github",
        re.compile(r"^github-pr:(?P<repo>[^#/]+/[^#/]+)#(?P<number>\d+)(?:@.+)?$"),
    ),
    (
        "azure-devops",
        re.compile(
            r"^azure-devops-pr:(?P<repo>[^#/]+/[^#/]+/[^#/]+)#(?P<number>\d+)(?:@.+)?$"
        ),
    ),
    (
        "gitea",
        re.compile(r"^gitea-pr:(?P<repo>[^#/]+/[^#/]+/[^#/]+)#(?P<number>\d+)(?:@.+)?$"),
    ),
)


def parse_review_target_ref(payload_ref: str) -> ReviewTargetRef | None:
    """Parse one built-in provider-backed reviewer ``payload_ref``.

    Unknown or non-provider-backed refs intentionally return ``None`` so the
    reviewer lifecycle continues to treat external/custom ref formats as
    opaque unless they adopt one of the built-in provider shapes.
    """
    if not payload_ref:
        return None
    for provider, pattern in _REF_PATTERNS:
        match = pattern.fullmatch(payload_ref)
        if match is None:
            continue
        return ReviewTargetRef(
            provider=provider,
            repo=match.group("repo"),
            number=int(match.group("number")),
        )
    return None


def target_from_observation_key(repo: str, number: int) -> ReviewTargetRef:
    """Reconstruct a provider-backed target from a persisted store key."""
    if ":" not in repo:
        return ReviewTargetRef(provider="github", repo=repo, number=number)
    provider, _, raw_repo = repo.partition(":")
    if provider not in {"azure-devops", "gitea"} or not raw_repo:
        raise ValueError(f"unsupported observation repo key {repo!r}")
    return ReviewTargetRef(provider=provider, repo=raw_repo, number=number)
