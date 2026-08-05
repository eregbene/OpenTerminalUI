from __future__ import annotations

import json
from typing import AsyncIterator


def sse(event: str, data: dict) -> str:
    payload = {"type": event, **data}
    return f"data: {json.dumps(payload, default=str)}\n\n"


async def stream_text(text: str) -> AsyncIterator[str]:
    for token in text.split(" "):
        yield token + " "
