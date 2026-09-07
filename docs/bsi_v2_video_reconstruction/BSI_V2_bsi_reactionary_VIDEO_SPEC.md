# BSI V2 Reactionary Video Spec

Source basis: original mentor videos 25-26, reconstructed from audiovisual rulebook, strategy spec, and visual audit.

## Mentor Rule

Reactionary Block is Order Flow with extra confirmation on the same timeframe. The defining rule is a two-array sequence: array 1 is mitigated first, then price pushes away and creates array 2. Entry is from array 2, never array 1.

## Required Sequence

1. A standard MSS/MSB Order Flow leg creates array 1 at the leg extreme.
2. Price mitigates or enters array 1.
3. Price pushes away from array 1.
4. That push creates a fresh smaller FVG/OB, array 2.
5. Array 2 does not need to break structure.
6. Enter when price returns into array 2.
7. Stop goes just beyond array 2, or wider beyond the larger leg extreme.
8. Target natural opposing liquidity/structure.

## Structure

Array 1 comes from the usual Order Flow MSS/MSB. Array 2 does not require a second MSS/CHoCH/BOS; it only needs fresh imbalance/order-block creation.

## Bias And Premium/Discount

Reactionary inherits base Order Flow context, but videos 25-26 do not independently re-teach HTF bias or premium/discount rules.

## Entry Array

Array 2 is mandatory. The mentor's RR tool is anchored to array 2 in both schematic and real examples.

Whether array 2 should use the family-wide OB-vs-FVG size discriminator is not established. The current evidence only proves that array 2 exists and is the entry.

## Target

Target is natural opposing structure/liquidity. The mentor states 1:2 to 1:5 is enough and says not to chase large RR, but the real example is narrated as 10R. Treat this as target discipline, not a hard cap.

## Current Code Comparison

Current Reactionary logic correctly implements the two-array mechanic, array-2 entry, no fresh structural-break requirement for array 2, and tight/conservative stop modes.

Ambiguous: array 2 is hardcoded as FVG in current code; the videos do not confirm or contradict this.

Not implemented as a gate: 1:2-1:5 RR discipline, intentionally questionable because the mentor's own example exceeds it.
