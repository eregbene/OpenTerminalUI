from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,
            "service": "bensim-api",
            "module": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "correlation_id", "user_id", "job_id", "provider", "symbol", "asset_class", "error_code", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    if os.getenv("BENSIM_LOG_FORMAT", os.getenv("LOG_FORMAT", "")).lower() != "json":
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


def get_request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if value:
        return str(value)
    return request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())


async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    correlation_id = request.headers.get(CORRELATION_ID_HEADER) or request_id
    request.state.request_id = request_id
    request.state.correlation_id = correlation_id
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers[REQUEST_ID_HEADER] = request_id
    response.headers[CORRELATION_ID_HEADER] = correlation_id
    logging.getLogger("backend.request").info(
        "api_request",
        extra={
            "request_id": request_id,
            "correlation_id": correlation_id,
            "duration_ms": duration_ms,
        },
    )
    return response
