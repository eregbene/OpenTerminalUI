# syntax=docker/dockerfile:1

FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY backend/requirements*.txt /tmp/
RUN pip install -r /tmp/requirements-dev.txt
# ctrader-open-api exact-pins pyOpenSSL==24.1.0 (pulling in an old cryptography transitively via
# Twisted), which leaves `service-identity` unable to import and degrades Twisted's TLS hostname
# verification for the cTrader connection (TLS-wrapped despite the SDK's "TcpProtocol" name --
# see backend/requirements-core.txt's own comment). A single combined pip-install pass refuses to
# resolve a newer pyOpenSSL alongside that exact pin, so this upgrade runs as its OWN, separate
# pass instead -- empirically verified (2026-08-21) that ctrader_open_api + twisted.internet.ssl +
# service_identity + the full test suite all still work correctly with the newer versions.
RUN pip install --upgrade "cryptography>=47" "pyOpenSSL>=25"
# Forex Factory calendar/news scraper providers (backend/economic_intelligence/providers)
# use headless Chromium via Playwright for background ingestion jobs only -- never on the
# trading-cycle hot path. Real build-time cost: ~300-400MB extra image size.
RUN python -m playwright install --with-deps chromium

COPY backend/ ./backend/
COPY pytest.ini ./pytest.ini
COPY models/ ./models/
COPY nlp/ ./nlp/
COPY data/ ./data/
COPY docs/ ./docs/
COPY plugins/ ./plugins/
COPY scripts/ ./scripts/
# Root-level Historical Intelligence corpus driver/watchdog scripts (2026-08-17 fix -- a prior
# rebuild silently dropped all 14 background corpus workers because this Dockerfile never copied
# them at all; they had only ever existed in the running container via ad-hoc `docker cp` during
# development, invisible until the next real rebuild). *.py glob, not an enumerated list, so a
# future new root-level driver script doesn't silently repeat this exact gap.
COPY *.py ./
RUN sed -i 's/\r$//' backend/entrypoint.sh && chmod +x backend/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()" || exit 1

CMD ["./backend/entrypoint.sh"]
