"""copilot-extensions Worktree Manager — standalone, out-of-plugin harness control plane.

This package is deliberately **not** a Copilot plugin and is **not** delivered
through the marketplace/plugin pipe. It is its own payload, fetched and run
directly by the one-line bootstrap, because the thing that must guarantee the
plugins' prerequisites cannot itself be one of those inert plugins.

See the vision (`visions/installer/`) and umbrella issue #352.
"""

__version__ = "0.1.0-dev33"
