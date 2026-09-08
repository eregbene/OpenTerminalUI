# BSI V3 Bucharest Entry Window Policy

Policy source: `BENSIM_USER_POLICY`

This policy gates only new position entries. Planning, POI monitoring, confirmations, and adaptive position management may continue outside the windows.

## Active Windows

- Timezone: `Europe/Bucharest`
- Window 1: `09:50-13:00`
- Window 2: `15:30-18:00`
- Strategy exceptions: disabled by default

## Strategy Impact

No V3 strategy is deleted or rewritten by this policy. If a final confirmation closes outside the active windows, the entry is blocked as `ENTRY_WINDOW_BLOCKED`; if the strategy has a session window attached, the block also carries `TIME_WINDOW_CONFLICT`.

Potentially affected session-sensitive strategies:

- `bsi_v3_asian_v2`
- `bsi_v3_0930`
- `bsi_v3_ict_silver_bullet`
- `bsi_v3_silver_bullet_with_bias`
- `bsi_v3_ar50`
- `bsi_v3_yin_yang`
- `bsi_v3_smt_session_hl`
- `bsi_v3_turtle_soups_ranges`

Exception support exists through:

- `BSI_V3_ALLOW_STRATEGY_TIME_EXCEPTIONS`
- `BSI_V3_ENTRY_WINDOW_EXCEPTION_STRATEGIES`

Both remain inactive unless explicitly configured.
