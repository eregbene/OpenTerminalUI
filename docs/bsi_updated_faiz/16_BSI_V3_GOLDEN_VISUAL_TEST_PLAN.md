# BSI V3 Golden Visual Test Plan

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Status: partial. Contact sheets are generated; example-by-example candle replay still pending.

## Required Artifacts

- One contact sheet per course video.
- One transcript per video.
- For each strategy, at least one marked-up chart fixture that shows:
  - HTF context.
  - Liquidity event.
  - POI/array.
  - MSS/MSB or IFVG trigger.
  - Entry, SL, TP, BE/partial events.
  - Exact source video and timestamp.

## Golden Test Groups

- SMC primitives: market structure, MSS/MSB, liquidity, FVG, PD, OB.
- Entry arrays: FVG, IFVG, BPR, breaker, volume imbalance, OB 2.0.
- No daily bias / mechanical: Asian V2, 9:30, Juggernaut, Spectre, Monday Range, Yin Yang, IFVG.
- Bias/HTF: Order Flow, ABC, ABCD, 4H OB, Holy Grail, Weaver, AR50, Standard Deviations, MMXM.
- Confluence: SMT, Quarterly Theory, Silver Bullet with Bias.

## Visual Pass Criteria

- Detected zones must overlap the mentor-drawn zones.
- Entry candle must occur after the taught trigger, not before.
- Stop must sit outside the taught invalidation structure.
- Target must match fixed-R or liquidity/draw rule for that strategy.
- Session/weekday rules must match New York time.
- If the visual shows a losing example, replay must reject or lose for the same source-led reason.
