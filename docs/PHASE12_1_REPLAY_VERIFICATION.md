# Phase 12.1 Replay Verification

Replay sessions are durable, read-only, tenant-owned records. Controls update replay session cursor and status only.

Replay controls never call broker adapters, OMS mutation paths, portfolio mutation paths, allocation mutation paths, or risk mutation paths.

Focused tests verify replay cursor movement does not alter the active portfolio state.
