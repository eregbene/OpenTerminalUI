# BSI V2 ABC Video Spec

Source basis: original mentor videos 15-17, reconstructed from audiovisual rulebooks, mentor notes, and frame audits. This is a video-first reconstruction. Current code is compared only after the mentor rules.

## Mentor Rule

ABC is a three-leg structure strategy. Unlike New York, ABC requires reclaim of the A level, a structure break created inside the C leg, and an OB/FVG entry from that break.

## Required Sequence

1. Identify the A leg: the main impulsive trend leg.
2. Identify the B leg: a significant pullback against A.
3. The B leg must not exceed the A-leg start level.
4. Identify the C leg: price moves back in the A-leg direction and takes the B-leg level.
5. Before taking the B-leg high/low, price must create structure inside the B-to-C area.
6. Price must reclaim the A level.
7. Price must break the structure created in the C leg.
8. Identify the OB/FVG created by that structure-breaking move.
9. Enter from that OB/FVG.
10. Stop goes beyond the entry array.
11. Target is the B-leg high/low.

## A, B, C Definitions

A leg is the dominant impulse.

B leg is the pullback. It must be significant enough to be a real counter-leg, but the videos do not establish an ATR or candle-count threshold.

C leg resumes the A-leg direction and creates the actionable reclaim/structure-break condition.

## Invalidations

If the B leg exceeds the A-leg start level, the ABC setup is invalid.

If the relevant structure break happens outside the B/C construction zone, it does not count for this setup.

## Entry Array

ABC uses OB/FVG entry. This is explicit on the video-15 summary slide and visible in videos 16-17 as candle-anchored rectangles at the MSS/structure-break location.

## Premium/Discount

No premium/discount, Fib, or equilibrium step is shown or spoken in the ABC videos.

## Target

Target is the high/low of the B leg. The examples produce variable RR values, including 5.62R, 4.74R, and 4.21R. These are outcomes from B-leg targeting, not fixed-R rules.

## Scope

The mentor describes ABC as working on every pair and any timeframe.

## Lifecycle

ABC is fractal/recursive. A completed ABC swing may become the A leg of a larger ABC, supporting sequential reentries. The mentor also cautions that this cannot continue indefinitely.

## Non-Rules

- No fixed 1:2 target.
- No bare swept-level retest entry.
- No premium/discount requirement.
- No New York-session-only rule.

## Current Code Comparison

Current `evaluate_bsi_abc` is broadly aligned in requiring ABC leg geometry, reclaim/structure break, OB/FVG entry, and B-leg target.

No definite code bug was established in the available audit. Any HTF-bias inheritance for ABC is not established as strategy-specific in the videos and should remain flagged separately if present.
