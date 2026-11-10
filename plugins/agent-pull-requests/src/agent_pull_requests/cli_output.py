"""Human-readable pull-request presentation, independent of command routing."""


def version(_args):
    """Print the package version without capturing a runtime invocation."""
    from . import __version__

    print(f"agent-pull-requests {__version__}")
    return 0


def render_status(status):
    review = status["reviewDecision"] or "(none)"
    mergeable = status["mergeable"] or "(unknown)"
    draft = "yes" if status["isDraft"] else "no"
    return "\n".join(
        [
            f"repo:            {status['repo']}",
            f"number:          #{status['number']}",
            f"state:           {status['state']}",
            f"mergeable:       {mergeable}",
            f"review decision: {review}",
            f"draft:           {draft}",
            f"title:           {status['title']}",
            f"url:             {status['url']}",
        ]
    )
