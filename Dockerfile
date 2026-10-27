# idlescan — self-hosted airdrop research desk
# Build:  docker build -t idlescan .
# Run:    docker run --rm -p 127.0.0.1:8787:8787 -v idlescan-data:/data idlescan
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IDLESCAN_HOST=127.0.0.1 \
    IDLESCAN_PORT=8787 \
    IDLESCAN_DB=/data/idlescan.db \
    IDLESCAN_READ_ONLY=true \
    PIP_NO_CACHE_DIR=1

# No runtime dependencies: stdlib only.  git is not needed at runtime.
RUN groupadd -r idlescan && useradd -r -g idlescan -d /app -s /sbin/nologin idlescan

WORKDIR /app
COPY idlescan /app/idlescan
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p /data && chown -R idlescan:idlescan /app /data

# Strict CSP and read-only-by-default behaviour come from the code; the
# container adds no extra privileges.  The /data volume is mounted at runtime.
USER idlescan

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3)" || exit 1

CMD ["python3", "-m", "idlescan.cli", "serve"]
