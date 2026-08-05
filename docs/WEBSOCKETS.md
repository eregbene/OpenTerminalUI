# WebSockets

## Current Channels

- `/api/ws/quotes`: market quote and depth subscriptions.
- `/api/ws/depth`: depth-only subscriptions.
- `/api/ws/alerts`: push-only alert channel.
- `/api/ws/us-quotes`: US trades and bars.

## Current Behavior

Clients send JSON operations such as `ping`, `subscribe`, and `unsubscribe`. Existing responses use a `type` field. Phase 3 adds envelope helpers for future channels without forcing current clients to migrate.

## Hardening Guidance

New or migrated channels should include event IDs, timestamps, correlation IDs, structured error codes, bounded client state, and documented reconnect behavior.
# Phase 4 Extension

Streaming market data should carry canonical provider provenance and freshness indicators. Existing WebSocket services remain compatible; full stream migration is tracked in `docs/STREAMING_DATA.md`.
