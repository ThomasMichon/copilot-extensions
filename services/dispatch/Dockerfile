# syntax=docker/dockerfile:1
#
# Container image for the agent-dispatch coordinator daemon -- Phase 2 of
# efforts/active/agent-dispatch-standalone-container/README.md.
#
# Build from the REPOSITORY ROOT (not this directory): the image needs
# plugins/agent-dispatch plus the handful of shared libs/* it depends on,
# in their real relative positions. agent-dispatch-service's own
# pyproject.toml deliberately does NOT carry a [tool.uv.sources] pointer at
# plugins/agent-dispatch (that escaping-path shape broke release promotion
# -- tools/materialize_main.py's uv-editable materializer only ever vendors
# an escaping libs/<lib> reference, by design), so this build installs
# plugins/agent-dispatch explicitly alongside agent-dispatch-service in one
# flat `uv pip install` invocation instead, the same pattern
# tools/run-standalone-tests.py's prepare() uses for local/CI testing.
#
#   docker build -f services/dispatch/Dockerfile -t agent-dispatch-service .
#   docker run --rm -p 8787:8787 -e AGENT_DISPATCH_PORT=8787 \
#       -v agent-dispatch-data:/home/dispatch/.agent-dispatch \
#       agent-dispatch-service
#
# No systemd unit, Scheduled Task, or self-update loop runs inside the
# container -- Docker (or the fleet's chosen container manager) owns
# restart/health, and an image rebuild+redeploy is how this service updates,
# not an in-place self-update (disabled below; see
# agent_dispatch.coordinator_loops's own AGENT_DISPATCH_SELF_UPDATE switch).

FROM python:3.12-slim AS builder

# Package-index override, for environments whose network policy requires
# routing through an approved internal package feed instead of public PyPI
# (e.g. a Microsoft-managed developer machine's Central Feed Services
# enforcement). Defaults to public PyPI -- the correct, portable default for
# this public repository; override only via --build-arg for a local/managed
# environment that requires it. uv does not read pip.conf/pip.ini, so both
# pip (installing uv itself) and uv's own resolution are pointed at the same
# override explicitly.
ARG PYTHON_INDEX_URL=https://pypi.org/simple/
ENV PIP_INDEX_URL=${PYTHON_INDEX_URL} \
    UV_DEFAULT_INDEX=${PYTHON_INDEX_URL}

RUN pip install --no-cache-dir uv
RUN ! command -v cc && ! command -v gcc

WORKDIR /src
# Only the sources agent-dispatch's own pyproject.toml [tool.uv.sources]
# table needs, in their real relative positions.
COPY libs/zdd libs/zdd
COPY libs/agent-procutil libs/agent-procutil
COPY libs/dropin-registry libs/dropin-registry
COPY libs/plugin-activation libs/plugin-activation
COPY libs/plugin-resolve libs/plugin-resolve
COPY libs/single-instance-lease libs/single-instance-lease
COPY plugins/agent-dispatch plugins/agent-dispatch
COPY services/dispatch services/dispatch

RUN uv build --no-cache --exclude-newer "7 days" --wheel --out-dir /wheels /src/libs/agent-procutil \
    && uv venv /opt/venv \
    && uv pip install --no-editable --python /opt/venv/bin/python \
         /wheels/agent_procutil-*.whl /src/plugins/agent-dispatch /src/services/dispatch

FROM python:3.12-slim

RUN useradd --system --create-home --home-dir /home/dispatch --shell /usr/sbin/nologin dispatch
COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    AGENT_DISPATCH_INSTALL_DIR=/home/dispatch/.agent-dispatch \
    AGENT_DISPATCH_SELF_UPDATE=0

# AGENT_DISPATCH_HOST defaults to the coordinator's own loopback-only default
# (127.0.0.1) -- NOT 0.0.0.0 -- for the same reason the coordinator itself
# enforces: it refuses to bind 0.0.0.0 without AGENT_DISPATCH_TOKEN set ("the
# task-control API must not be exposed on the LAN unauthenticated"). A
# loopback-only bind is reachable only from inside this exact container's own
# network namespace, which is enough for a single-container smoke test but
# NOT for a sibling container on the same Docker network. To reach the
# coordinator from another container (the real fleet deployment shape --
# e.g. a container reached only through the Gateway, per this effort's own
# "only the Gateway exposes non-management ports" model), set BOTH:
#   -e AGENT_DISPATCH_HOST=0.0.0.0 -e AGENT_DISPATCH_TOKEN=<bearer-token>
# Never set AGENT_DISPATCH_HOST=0.0.0.0 without a token merely to work around
# the guard -- that guard is the real safety boundary, not a bug to route
# around.
#
# AGENT_DISPATCH_PORT is intentionally left unset: the coordinator binds an
# OS-assigned dynamic port by default (the same Stage C behavior every
# plugin-delivered/standalone host already uses), advertised via its own
# rendezvous file under AGENT_DISPATCH_INSTALL_DIR. Pin a stable, publishable
# port with `-e AGENT_DISPATCH_PORT=<port>` when a deployment needs one
# (e.g. a fixed container-network hostname:port another service reaches),
# rather than this image silently reintroducing the fixed-shared-port
# collision the host installer moved away from (see
# plugins/agent-dispatch/scripts/install.sh's own Stage C migration note).

RUN mkdir -p /home/dispatch/.agent-dispatch && chown -R dispatch:dispatch /home/dispatch
USER dispatch
WORKDIR /home/dispatch
VOLUME ["/home/dispatch/.agent-dispatch"]

ENTRYPOINT ["python", "-m", "agent_dispatch", "serve"]
