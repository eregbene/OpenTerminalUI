"""BSI V2 audiovisual baseline scaffold.

This module is intentionally inert: it exposes the V2 methodology version and
future activation probe without registering V2 in live candidate generation.
"""
from __future__ import annotations

import os

BSI_BASELINE_V1 = "BSI_BASELINE_V1"
BSI_BASELINE_V2_AUDIOVISUAL = "BSI_BASELINE_V2_AUDIOVISUAL"
BSI_V2_ENV_FLAG = "BSI_BASELINE_V2_AUDIOVISUAL_ENABLED"


def bsi_v2_enabled() -> bool:
    """Future V2 kill switch. Defaults false so Phase 1 cannot trade."""
    return os.getenv(BSI_V2_ENV_FLAG, "false").strip().lower() in {"1", "true", "yes", "on"}

