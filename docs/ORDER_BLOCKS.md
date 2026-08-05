# Order Blocks

Implemented deterministic variant:

`last_opposing_candle_before_displacement_and_break`

Configuration controls:

- displacement requirement
- structure-break requirement
- zone source: full range or body
- maximum search bars
- invalidation rule
- mitigation rule

The object tracks candidate source, confirmation, active/partial/invalidated status, mitigation time and invalidation time.

Order blocks are analytical zones, not proof of institutional orders.
