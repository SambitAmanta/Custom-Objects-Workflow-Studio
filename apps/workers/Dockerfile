# Multi-stage production Dockerfile for Forge Distributed Workers
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final runtime image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DJANGO_SETTINGS_MODULE=config.settings
ENV PYTHONPATH="/app/apps/api:/app/apps"

# Copy application source code
COPY apps/api /app/apps/api
COPY apps /app/apps
COPY scripts /app/scripts

# Create non-root system user
RUN useradd -u 1000 -m forgeuser && chown -R forgeuser:forgeuser /app
USER forgeuser

# Run unified worker runner (Relay, Webhook Dispatcher, CSV Exporter)
CMD ["python", "apps/workers/run_worker.py", "--service", "all", "--interval", "1.0"]
