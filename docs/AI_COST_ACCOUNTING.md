# AI Cost Accounting

Usage records are stored in `data/ai_assistant/provider_usage.json`.

Tracked fields include:

- input, output, cached input, reasoning and total tokens
- provider, model, request count and retry count
- latency, time to first token and stream duration
- cancellation status
- estimated and confirmed cost
- currency and pricing version
- user, conversation, research job, day and month

Costs use `Decimal` arithmetic through `backend/ai_provider/pricing.py`.
