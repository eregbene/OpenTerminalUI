# BSI V3 Implementation Report

Methodology: `BSI_BASELINE_V3_UPDATED_FAIZ`

Phase 0 resolved: resolved: old statement meant lesson number 40 was previously not mapped; current manifest has no missing numbered lessons and includes 40. Turtle Soups & Ranges Mastery.mp4.

Source extraction: `125/125` videos, `125/125` transcripts, `125/125` visual contact sheets.

Selected symbols: `EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, XAUUSD`.

Evidence mode: rules were reconstructed from the updated Faiz transcripts plus visual contact sheets; not audio/transcript only. The source index marks extracted rules as `SPOKEN_AND_VISUAL` where both were used.

Implementation boundary: research only; V3 not deployed; V2 demo not disabled.

Detector status: converted strategies now route through `DETECTOR_REGISTRY` and emit strategy-specific rejection reasons. Unconverted strategies remain blocked by explicit no-detector reasons, not by shared generic results.

Raw-data status: `2077` August 2026 candles loaded from `postgres`. Strategies with missing required timeframes/instruments are marked with strategy-level `DATA_GAP`.
