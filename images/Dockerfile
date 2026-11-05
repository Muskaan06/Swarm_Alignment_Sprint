# One image for every lab service. Everything is Python stdlib, so it builds
# on ARM64 (Apple silicon) and x86-64 without emulation.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# curl and bash are for LLM agents. Scripted probes need neither.
RUN command -v curl >/dev/null 2>&1 || \
    (apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*)

RUN groupadd -g 1000 agent 2>/dev/null || true; \
    useradd -u 1000 -g 1000 -m -s /bin/bash agent 2>/dev/null || true; \
    mkdir -p /lab /seed /cache /data /shared /persist /workspace; \
    chown -R 1000:1000 /cache /data /shared /persist /workspace; \
    i=0; while [ $i -lt 8 ]; do echo "frozen package $i" > /seed/frozen-pkg-$i.txt; i=$((i+1)); done

COPY lab/ /lab/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
