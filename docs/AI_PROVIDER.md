# AI Provider

Phase 9D adds `backend/ai_provider` as the grounded presentation layer for the AI Assistant.

## Providers

- `openai`: OpenAI chat-completions adapter using `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` and `OPENAI_RETRIES`.
- `local`: deterministic refusal fallback used for tests, missing keys and provider failures.

The provider receives a prompt built only from the user request, deterministic explanation output and a canonical evidence bundle. It does not call tools, access the database, mutate files or execute shell commands.

Phase 10 adds usage ledgers, pricing, budgets, secret abstraction, health APIs and circuit breaker resilience. See `AI_PROVIDER_PRODUCTION.md`.

## Safety

Provider text is validated after generation. Unsupported numeric or timestamp claims and prohibited action language are replaced with:

`I don't have sufficient verified evidence to answer.`
