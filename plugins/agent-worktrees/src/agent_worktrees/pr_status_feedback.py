"""Feedback-first policy for the public PR reminder state vocabulary."""

STATUS_FEEDBACK: dict[str, tuple[str, str]] = {
    "changes-requested": (
        "changes requested",
        "address the outstanding reviewer feedback, then obtain its resolution",
    ),
    "conflict": (
        "conflict requires submitter action",
        "resolve the source conflict through the supported PR update flow",
    ),
    "commented": (
        "review comments received",
        "assess the findings under the repository's review policy before completion",
    ),
}
