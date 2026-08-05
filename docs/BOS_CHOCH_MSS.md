# BOS, CHoCH And MSS

Implemented break confirmation modes:

- `wick`
- `close`
- `body_close`
- `close_plus_distance`
- `close_plus_displacement`

BOS is classified when the break direction continues the current trend state.

CHoCH is classified when price breaks counter to the current trend without displacement.

MSS is classified when price breaks counter to the current trend with displacement evidence.

If trend context is insufficient, the engine emits `unclassified_break`.
