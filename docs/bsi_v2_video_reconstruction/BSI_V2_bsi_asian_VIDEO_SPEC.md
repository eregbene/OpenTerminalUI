# BSI V2 Asian Video Spec

Source basis: original mentor videos 6-9, reconstructed from audiovisual rulebooks, example catalog, and frame audits.

## Mentor Rule

Asian is an Asian-session range sweep and reversal strategy. It uses the Asian box high/low as liquidity, then waits for post-sweep MSS/CHoCH and OB/FVG entry. It does not require daily bias or premium/discount.

## Required Sequence

1. Mark the Asian session box high and low.
2. Wait for one side of the box to be swept.
3. Sweep should occur during the post-Asian/lunch-gap window described by the mentor.
4. Wait for MSS/CHoCH after the sweep.
5. Identify the OB/FVG created by the reversal structure.
6. Enter from that OB/FVG.
7. Stop beyond the entry array / swept extreme.
8. Target the opposite side of the same Asian range.

## Liquidity

Liquidity is the Asian session high or low. It is horizontal box liquidity, not trendline liquidity and not a generic swing pool.

## Bias

No daily or HTF bias is required. The mentor explicitly frames this as an algorithmic strategy where daily bias is not needed.

## Premium/Discount

No Fib, 50%, premium, discount, or OTE step appears in the Asian videos.

## Entry Array

Entry comes from the OB/FVG created by the post-sweep reversal. Multiple entry depths can be shown as alternatives for the same thesis, not as separate sequential trades.

## Target

Target is the opposite side of the same Asian session box. The examples produce variable RR based on stop depth and range size.

## Timing

The mentor describes a one- or two-hour break after Asian session. Exact clock boundaries are not established by the videos and should be treated as engineering choices unless independently verified.

## Lifecycle

Only one thesis per Asian box is demonstrated. A second sweep/reentry within the same session is not established.

## Current Code Comparison

Current Asian implementation is expected to match the major shape if it uses Asian box sweep, MSS/CHoCH confirmation, OB/FVG entry, no premium/discount, and opposite-box-edge target.

Watch items for V2: avoid importing daily bias, avoid turning the lunch-gap timing into a mentor-certified exact timestamp, and distinguish alternate entries from multiple separate opportunities.
