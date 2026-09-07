# Faiz Updated Entry Models

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

## Arrays Extracted So Far

### Extreme FVG / OB Entry

- Sources: `1. Order Flow Trading Strategy.mp4`, `1. SMT Divergence.mp4`, `10. ABC Trading Strategy.mp4`.
- Rule: after liquidity sweep and MSS/reclaim confirmation, entry is taken from the extreme FVG or order block where the displacement originated.
- Evidence: `SPOKEN_AND_VISUAL`

### Volume Imbalance Entry

- Source: `11. VOLUME IMBALANCE.mp4`.
- Rule: volume imbalance may be used like FVG for entry, especially when aligned with another confluence such as FVG/OB; mentor says it can also be used by itself, but this lesson does not yet present it as a separate strategy ID.
- Evidence: `SPOKEN_EXPLICIT`

### BPR Entry

- Source: `12. BPR.mp4`.
- Rule: after MSS, inspect the last move before MSS for an old FVG. If the new MSS FVG overlaps with that old FVG box, entry may be taken from the BPR/old FVG box instead of the full new FVG.
- Evidence: `SPOKEN_AND_VISUAL`

### Breaker Block Entry

- Source: `13. BREAKER BLOCK.mp4`.
- Rule: after MSS, entry may be taken from the breaker block instead of waiting for the extreme FVG. If an FVG is present with the breaker, confidence improves.
- Stop: beyond the extreme FVG, not merely beyond the breaker block.
- Evidence: `SPOKEN_AND_VISUAL`

### Confirmation Entry

- Source: `7. Confirmation Entries.mp4`.
- Rule: after a higher-timeframe or strategy-timeframe MSS creates a POI, drop to a smaller timeframe and wait for another MSS with displacement through/inside the POI.
- Examples: M5 ABC can drop to M1; H1 POI can drop to M5; H1 Order Flow POI can drop to M15.
- Benefit: improves RR and win rate, but can miss trades if price never returns to the smaller-timeframe POI.
- Timing: entry must occur during London or New York killzone. For indices, mentor allows entries up to `11:59` New York time and warns to close before/around noon.
- Evidence: `SPOKEN_AND_VISUAL`

### Inducement Entry Pattern

- Source: `9. Entry Patterns.mp4`.
- Rule: when price approaches a POI but reverses early, that early reversal can generate liquidity/inducement. Wait for price to return into the actual POI before entry.
- Scope: mentor says it can be used with every strategy, including ABC and Order Flow.
- Evidence: `SPOKEN_AND_VISUAL`

### BOS Liquidity Pattern

- Source: `9. Entry Patterns.mp4`.
- Rule: after MSS/change of character creates an extreme OB/FVG POI, price may break another structure before retesting the POI. That new BOS level is liquidity, not necessarily the better entry.
- Entry: prefer the original extreme OB/FVG POI when price returns, instead of chasing the later BOS level.
- Variant: BOS liquidity can form before the full MSS; the return to POI remains valid if the structure/liquidity pattern is present.
- Evidence: `SPOKEN_AND_VISUAL`
