# BSI V2 ABCD Video Spec

Source basis: original mentor videos 27-28, reconstructed from audiovisual rulebook, strategy spec, and visual audit.

## Mentor Rule

ABCD is ABC as a reversal, composed with a New York-style D-leg. After a completed ABC, the D leg breaks, reclaims, and retests the B-leg endpoint.

## Required Sequence

1. A complete ABC pattern forms.
2. A leg is the original impulse.
3. B leg is the retrace.
4. C leg pushes beyond A's extreme and completes the ABC.
5. D leg moves opposite the C leg.
6. D leg takes out the B-leg low/high endpoint.
7. Price reclaims that same B-leg endpoint.
8. Price retests that level.
9. Enter on the retest.
10. Stop beyond the D-leg fakeout extreme.
11. Target default is fixed 1:2 RR.

## D-Leg Level

The D leg breaks the B-leg endpoint, not the A-leg level. In P0/P1/P2 notation this is P2, the B-leg end / C-leg start.

## Entry

Entry is a level retest, not an OB/FVG array entry. Optional tiny FVG/OB or internal structure break at D is extra confluence only.

## Session

The mentor calls this a New York session strategy. Exact clock hours are not stated in videos 27-28.

## Target

Default target is 1:2 RR. Video 28 allows flexibility: structure target or about 1:3 can be used, but the repeated default is 1:2.

## Current Code Comparison

Current `evaluate_bsi_abcd` correctly uses the completed ABC requirement, D direction opposite ABC direction, P2 as the D break level, and fixed 1:2 target.

Partial gap: structural/1:3 alternate target from video 28 is not represented.

Ambiguous: code uses close-based break/reclaim; videos imply this but do not restate the explicit "wicks do not count" rule for ABCD.
