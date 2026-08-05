# AI Retention

AI evidence persistence is file-backed under `data/ai_assistant`.

Defaults: evidence bundles 30 days, AI audit records 90 days, cleanup batch size 500, maximum AI storage 50 MB.

`AIAssistantRepository.cleanup()` removes expired bundles and aged audit records. It never deletes canonical research, trading, strategy or market data records.

File-backed storage remains a local deterministic evidence store, not a high-concurrency multi-node production store.
