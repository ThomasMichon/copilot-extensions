# Build context: one SourceReader artifact containing source/ and source.json.
# Both base references must be separately selected immutable public images.
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS builder
ARG PYTHON_IMAGE
ARG UV_IMAGE
COPY --from=uv /uv /usr/local/bin/uv
RUN python3 -c 'import re,sys; assert all(re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", value) for value in sys.argv[1:]), "immutable image references required"' "$PYTHON_IMAGE" "$UV_IMAGE"
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY source/ /opt/lab/source/
COPY source.json /opt/lab/source.json
RUN chmod -R a+rX /opt/lab/source && chmod 0644 /opt/lab/source.json
WORKDIR /opt/lab/source
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_NO_CACHE=1
RUN UV_PROJECT_ENVIRONMENT=/lab/state/runtime/worktrees \
    uv sync --project plugins/agent-worktrees --no-dev --no-editable --exclude-newer "7 days"
RUN UV_PROJECT_ENVIRONMENT=/lab/state/runtime/bridge \
    uv sync --project plugins/agent-bridge --no-dev --no-editable --exclude-newer "7 days"
RUN UV_PROJECT_ENVIRONMENT=/lab/state/runtime/dispatch \
    uv sync --project plugins/agent-dispatch --no-dev --no-editable --exclude-newer "7 days"
RUN mv /lab/state/runtime /opt/lab/runtime-seed \
    && chmod -R a+rX /opt/lab/runtime-seed \
    && useradd --uid 1000 --user-group --create-home --home-dir /home/lab lab \
    && mkdir -p /lab/state /lab/workspace /lab/evidence /run \
    && chown 1000:1000 /home/lab /lab/state /lab/workspace /lab/evidence \
    && chmod 0700 /home/lab /lab/state /lab/workspace /lab/evidence \
    && ln -sfnT /usr/local/bin/python3 /usr/bin/python3 \
    && chmod 0755 /opt/lab/source/tools/reproducer-lab/guest_cli.sh \
    && ln -s /opt/lab/source/tools/reproducer-lab/guest_cli.sh /usr/local/bin/agent-worktrees \
    && ln -s /opt/lab/source/tools/reproducer-lab/guest_cli.sh /usr/local/bin/agent-bridge \
    && ln -s /opt/lab/source/tools/reproducer-lab/guest_cli.sh /usr/local/bin/agent-dispatch

# Reset inherited image environment, ports, volumes, entrypoint and healthcheck.
FROM scratch
COPY --from=builder / /
ENV HOME=/home/lab PATH=/usr/local/bin:/usr/bin:/bin PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /lab/workspace
ENTRYPOINT ["/usr/bin/sleep", "infinity"]
