# syntax=docker/dockerfile:1

FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY backend/requirements*.txt /tmp/
RUN pip install -r /tmp/requirements-dev.txt
# Forex Factory calendar/news scraper providers (backend/economic_intelligence/providers)
# use headless Chromium via Playwright for background ingestion jobs only -- never on the
# trading-cycle hot path. Real build-time cost: ~300-400MB extra image size.
RUN python -m playwright install --with-deps chromium

COPY backend/ ./backend/
COPY pytest.ini ./pytest.ini
COPY models/ ./models/
COPY nlp/ ./nlp/
COPY data/ ./data/
COPY plugins/ ./plugins/
COPY scripts/ ./scripts/
# Root-level Historical Intelligence corpus driver/watchdog scripts (2026-08-17 fix -- a prior
# rebuild silently dropped all 14 background corpus workers because this Dockerfile never copied
# them at all; they had only ever existed in the running container via ad-hoc `docker cp` during
# development, invisible until the next real rebuild). *.py glob, not an enumerated list, so a
# future new root-level driver script doesn't silently repeat this exact gap.
COPY *.py ./
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist
RUN sed -i 's/\r$//' backend/entrypoint.sh && chmod +x backend/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()" || exit 1

CMD ["./backend/entrypoint.sh"]
