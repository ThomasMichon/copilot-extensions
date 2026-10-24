ARG PYTHON_BASE
FROM ${PYTHON_BASE}
ARG PYTHON_BASE
RUN python -I -c "import os,re; assert re.fullmatch(r'[^@]+@sha256:[0-9a-f]{64}', os.environ['PYTHON_BASE'])"
COPY wheels/ /tmp/chat-wheels/
COPY requirements.lock /tmp/chat-requirements.lock
RUN python -I -m pip install --no-index --find-links=/tmp/chat-wheels --only-binary=:all: --require-hashes --no-cache-dir -r /tmp/chat-requirements.lock \
    && python -I -c "import agent_chat_service.hosting,single_instance_lease,endpoint_rendezvous" \
    && rm -rf /tmp/chat-wheels /tmp/chat-requirements.lock
USER 65532:65532
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
WORKDIR /tmp
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=2 \
    CMD ["python", "-I", "-B", "-m", "agent_chat_service.hosting", "--config", "/run/config/chat.json", "--run-dir", "/tmp/chat-run", "--health"]
ENTRYPOINT ["python", "-I", "-B", "-m", "agent_chat_service.hosting"]
CMD ["--config", "/run/config/chat.json", "--run-dir", "/tmp/chat-run", "--require-existing-storage"]
