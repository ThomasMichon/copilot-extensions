"""Session segmenter -- collate a single Copilot session into digestible chunks.

The events -> Markdown conversion is the reusable engine in
:mod:`agent_logger.segmenter.digest`; each console script layers its own
charter (persistent digest, takeover brief, ...) on top of it.

Console scripts (exposed via pyproject ``[project.scripts]``):

- ``collate-session`` -- :mod:`agent_logger.segmenter.collate`
- ``read-session-digest`` -- :mod:`agent_logger.segmenter.read_digest`
- ``prepare-session-log`` -- :mod:`agent_logger.segmenter.prepare_log`
- ``ramp-up-session`` -- :mod:`agent_logger.segmenter.ramp_up`
"""
