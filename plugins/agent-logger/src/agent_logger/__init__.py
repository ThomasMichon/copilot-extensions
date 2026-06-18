"""agent-logger -- reusable Copilot CLI session logging.

Components (built out across the plan phases):

- ``segmenter`` -- collate a single Copilot session into context-ingestible
  Markdown digest chunks (``collate-session``, ``read-session-digest``,
  ``prepare-session-log``).
- ``sync`` -- push raw session data to a configurable target (local
  dotfolder, OneDrive, SSH, or a generic ingest endpoint).
- ``orchestrator`` -- optional scheduled daemon that crunches a backlog of
  sessions into committed Markdown logs.

The plugin is intentionally personality- and layout-neutral: voices, output
path templates, and machine naming are configuration, not hard-coded.
"""

from agent_logger._build_info import __version__

__all__ = ["__version__"]
