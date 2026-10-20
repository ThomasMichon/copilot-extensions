# Build from the repository root, or the witness's allowlisted Git context.
# Intended targets: linux/amd64 and linux/arm64; qualification is per native run.
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY fleet-manager/pyproject.toml fleet-manager/setup.py fleet-manager/README.md ./fleet-manager/
COPY fleet-manager/src/fleet_manager/*.py ./fleet-manager/src/fleet_manager/
COPY libs/versioned-runtime/standalone_build.py libs/versioned-runtime/versioned_runtime.py libs/versioned-runtime/versioned_runtime_leases.py libs/versioned-runtime/versioned_runtime_construction.py libs/versioned-runtime/versioned_runtime_publication.py libs/versioned-runtime/versioned_runtime_source.py ./libs/versioned-runtime/
RUN uv build --no-cache --exclude-newer "7 days" --wheel --out-dir /wheels ./fleet-manager \
    && uv venv /opt/gateway \
    && for wheel in /wheels/fleet_manager-*.whl; do \
         uv pip install --no-cache --exclude-newer "7 days" --python /opt/gateway/bin/python "${wheel}[service-gateway]"; \
       done \
    && /opt/gateway/bin/python -c "from fleet_manager.gateway_service_connector import ServiceConnector; from fleet_manager.gateway_service_server import serve"

FROM python:3.13-slim
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.title="fleet-manager Gateway" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"
COPY --from=build /opt/gateway /opt/gateway
ENV PATH="/opt/gateway/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/state
RUN mkdir /config /state && chown 10001:10001 /state
USER 10001:10001
WORKDIR /state
# Listener and authenticated role readiness are deployment-owned probes.
HEALTHCHECK NONE
ENTRYPOINT ["fleet-manager"]
CMD ["serve", "--role", "gateway", "--service-policy", "/config/policy.json", "--tls-config", "/config/listener.json"]
