# Real-software check. Build from the repo root:
#   docker build -t sbd-realcheck:latest -f realcheck/Dockerfile .
FROM sbd-lab:latest
USER root
RUN python3 -m pip install --no-cache-dir "devpi-server==6.21.0"
COPY realcheck/upstream.py realcheck/agent_probe.py /check/
