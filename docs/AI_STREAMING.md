# AI Streaming

`POST /api/ai/chat/stream` returns server-sent events.

Events are emitted as JSON `data:` frames with a `type` field:

- `model`
- `status`
- `token`
- `final`

Phase 9D validates the final grounded answer before streaming it to the client. True provider token streaming is intentionally deferred.

Phase 10 adds staged provider streaming with provisional deltas and final validation. See `AI_PROVIDER_STREAMING.md`.
