# AI Token Budget

Token budget helpers live in `backend/ai_provider/token_budget.py`.

Current limits:

- prompt character ceiling: `24000`
- prompt token estimate: 4 characters per token
- default max prompt tokens: `6000`

Requests exceeding the prompt budget fail before provider execution.

Phase 10 adds hard provider reservations, soft warnings, concurrency limits and usage reconciliation.
