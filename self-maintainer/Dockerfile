FROM odysseia-guidance-bot_app:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/hermes-venv \
    && /opt/hermes-venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/hermes-venv/bin/pip install --no-cache-dir hermes-agent==0.19.0

ENV PATH="/opt/hermes-venv/bin:${PATH}" \
    HERMES_HOME="/hermes" \
    PYTHONUNBUFFERED="1" \
    PIP_DISABLE_PIP_VERSION_CHECK="1"

WORKDIR /workspace

CMD ["sleep", "infinity"]
