# sandbox/base/python:3.12
# Minimal Python sandbox image for OpenSandbox-on-Azure demo.
# Runs as non-root user "sandbox" (uid 10001).
# CMD = sleep infinity — ACI exec attaches transient bash shells.

FROM python:3.12-slim

# System packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid sandbox --shell /bin/bash --create-home sandbox

# Pre-install Python packages as root so they're available system-wide
RUN pip install --no-cache-dir \
    pip \
    httpx \
    numpy \
    pandas \
    requests

# Drop to non-root for runtime
USER sandbox
WORKDIR /home/sandbox

# Keep container alive for ACI exec attach
CMD ["sleep", "infinity"]
