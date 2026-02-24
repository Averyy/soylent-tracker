FROM python:3.14.2-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -s /bin/false -u 1001 appuser

RUN pip install --no-cache-dir uv==0.10.3

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-hashes -o requirements.txt && \
    uv pip install --system --no-cache -r requirements.txt && \
    rm requirements.txt

COPY app.py ./
COPY lib/ ./lib/
COPY templates/ ./templates/
COPY static/ ./static/

RUN mkdir -p /app/data && chown -R appuser:root /app/data

ENV PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8745

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8745/health || exit 1

# Trust XFF from any source — safe because container is only reachable via
# Docker network (Caddy proxy at 172.18.0.x) and localhost, never directly exposed.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8745", "--proxy-headers", "--forwarded-allow-ips", "*"]
