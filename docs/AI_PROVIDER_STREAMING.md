# AI Provider Streaming

Streaming uses a staged model:

1. verified evidence bundle
2. bounded prompt
3. provider stream
4. provisional deltas
5. final validation
6. persisted canonical answer

SSE event types:

- `started`
- `metadata`
- `provisional_delta`
- `citation`
- `usage`
- `warning`
- `validated`
- `fallback`
- `cancelled`
- `error`
- `completed`

Only the final validated answer is canonical.
