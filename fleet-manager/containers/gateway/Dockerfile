# Build from the repository root, or the witness's allowlisted Git context.
# Intended targets: linux/amd64 and linux/arm64; qualification is per native run.
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
ENV TMPDIR=/build/.tmp
RUN mkdir -p "$TMPDIR" && ! command -v cc && ! command -v gcc
COPY fleet-manager/pyproject.toml fleet-manager/setup.py fleet-manager/README.md ./fleet-manager/
COPY fleet-manager/src/fleet_manager/*.py ./fleet-manager/src/fleet_manager/
COPY libs/versioned-runtime/standalone_build.py libs/versioned-runtime/versioned_runtime.py libs/versioned-runtime/versioned_runtime_leases.py libs/versioned-runtime/versioned_runtime_construction.py libs/versioned-runtime/versioned_runtime_publication.py libs/versioned-runtime/versioned_runtime_source.py ./libs/versioned-runtime/
COPY libs/installer-engine/installer_engine_process.py libs/installer-engine/runtime_launcher.py ./libs/installer-engine/
COPY libs/agent-procutil/pyproject.toml libs/agent-procutil/setup.py libs/agent-procutil/README.md ./libs/agent-procutil/
COPY libs/agent-procutil/src/agent_procutil/ ./libs/agent-procutil/src/agent_procutil/
RUN uv build --no-cache --exclude-newer "7 days" --wheel --out-dir /wheels ./libs/agent-procutil \
    && uv build --no-cache --exclude-newer "7 days" --wheel --out-dir /wheels ./fleet-manager \
    && uv venv /opt/gateway \
    && for wheel in /wheels/fleet_manager-*.whl; do \
         uv pip install --no-cache --exclude-newer "7 days" --python /opt/gateway/bin/python "${wheel}[service-gateway]" /wheels/agent_procutil-*.whl; \
       done \
    && /opt/gateway/bin/python -c "from fleet_manager.gateway_service_connector import ServiceConnector; from fleet_manager.gateway_service_server import serve; from agent_procutil.daemon_instances import admit_daemon_instance; from agent_procutil._daemon_instance_linux import check; check()"

FROM python:3.13-slim
ARG SOURCE_COMMIT
ARG GATEWAY_UID=10001
ARG GATEWAY_GID=10001
LABEL org.opencontainers.image.title="fleet-manager Gateway" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"
COPY --from=build /opt/gateway /opt/gateway
ENV PATH="/opt/gateway/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/state
# Only the explicitly selected account is configured; its old home is never moved.
RUN set -eu; \
    for value in "$GATEWAY_UID" "$GATEWAY_GID"; do \
        case "$value" in ""|0*|*[!0-9]*) echo "Gateway identity must be positive decimal integers" >&2; exit 1;; esac; \
        test "$value" -le 2147483647; \
    done; \
    test ! -L /state; \
    group="gateway-$GATEWAY_GID"; test "$GATEWAY_GID" != 10001 || group=gateway; \
    account="gateway-$GATEWAY_UID"; test "$GATEWAY_UID" != 10001 || account=gateway; \
    if ! getent group "$GATEWAY_GID" >/dev/null; then groupadd --gid "$GATEWAY_GID" "$group"; fi; \
    if getent passwd "$GATEWAY_UID" >/dev/null; then \
        account="$(getent passwd "$GATEWAY_UID" | cut -d: -f1)"; \
        usermod --gid "$GATEWAY_GID" --home /state "$account"; \
    else \
        useradd --uid "$GATEWAY_UID" --gid "$GATEWAY_GID" --home-dir /state \
            --no-create-home --shell /usr/sbin/nologin "$account"; \
    fi; \
    mkdir -p /config; \
    install -d -m 700 -o "$GATEWAY_UID" -g "$GATEWAY_GID" /state
USER ${GATEWAY_UID}:${GATEWAY_GID}
WORKDIR /state
RUN ! command -v cc && ! command -v gcc \
    && python -I -c "import pwd,os; from agent_procutil._daemon_instance_linux import check; assert pwd.getpwuid(os.getuid()).pw_dir == '/state'; check()"
# Listener and authenticated role readiness are deployment-owned probes.
HEALTHCHECK NONE
ENTRYPOINT ["fleet-manager"]
CMD ["serve", "--role", "gateway", "--service-policy", "/config/policy.json", "--tls-config", "/config/listener.json"]
