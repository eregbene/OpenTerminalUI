from __future__ import annotations

from typing import Any

from backend.core.contracts.events import EventEnvelope


def websocket_event(
    message_type: str,
    *,
    source: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    envelope = EventEnvelope.create(
        f"ws.{message_type}",
        source=source,
        payload=payload or {},
        correlation_id=correlation_id,
    )
    data = envelope.model_dump(mode="json")
    data["type"] = message_type
    return data


def websocket_error(
    code: str,
    message: str,
    *,
    source: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    return websocket_event(
        "error",
        source=source,
        correlation_id=correlation_id,
        payload={"code": code, "message": message},
    )
