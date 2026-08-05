from __future__ import annotations


def deterministic_latency_ms(configured_ms: int) -> int:
    return max(0, int(configured_ms))
