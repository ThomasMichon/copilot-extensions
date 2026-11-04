"""Human-readable pull-request presentation, independent of command routing."""


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
