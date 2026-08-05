# AI Architecture

Bensim Trading's canonical AI Assistant is a deterministic, read-only explanation layer around existing system outputs.

## Package

`backend/ai_assistant/`

- `models.py`: canonical intents, domains, entity references, tool specs, evidence items, bundles and explanation payloads.
- `intents.py`: deterministic keyword intent parsing.
- `planner.py`: maps intent/domain pairs to read-only tools.
- `tool_registry.py`: declares AI-visible tools and metadata.
- `authorization.py`: blocks non-read-only tools and requires prohibited-action guardrails.
- `adapters/`: read-only retrieval adapters for market structure, strategy, research and trading evidence.
- `evidence.py`: creates canonical evidence items.
- `bundles.py`: creates persisted canonical evidence bundles.
- `lineage.py`: traces deterministic entity relationships.
- `repository.py`: file-backed AI evidence/audit persistence.
- `freshness.py`: domain-aware freshness and quality classification.
- `grounding.py`: enforces evidence presence and guardrail metadata.
- `explanations.py`: deterministic explanation builders.
- `services.py`: orchestration entrypoint.
- `audit.py`: non-mutating audit record payloads.

## Boundary

The assistant can explain, summarize, compare, investigate and retrieve evidence.

It cannot create orders, approve deployments, approve candidates, approve risk, change strategies, mutate paper accounts or execute workflows.

## Current Implementation

Phase 9B does not use an LLM. All explanations are generated from deterministic payloads or context bundles.

## Phase 9C Persistent Evidence

The assistant now retrieves evidence through `backend/ai_assistant/adapters` before explaining. Evidence is stored in bundles with entity references, content hashes, lineage, freshness, quality, missing evidence and warnings.

See `AI_EVIDENCE_SOURCE_MAP.md`, `AI_EVIDENCE_ADAPTERS.md`, `AI_LINEAGE_RETRIEVAL.md`, `AI_FRESHNESS_AND_QUALITY.md` and `AI_GROUNDING_RULES.md`.
## Phase 9C.1 Security Hardening

AI endpoints now use trusted authenticated request context, owner-scoped bundle retrieval, bounded requests/responses, evidence allow-lists, centralized redaction, safe error payloads, rate limits and atomic file-backed persistence.

See `AI_API_SECURITY_REVIEW.md`, `AI_EVIDENCE_SECURITY.md`, `AI_AUTHENTICATION_AND_AUTHORIZATION.md`, `AI_DATA_REDACTION.md` and `AI_RETENTION.md`.

## Phase 9D Provider And Chat Layer

The remaining Phase 9 layer adds `backend/ai_provider` for provider abstraction, prompt construction, citation handling, token budgets, output validation, SSE formatting and owner-scoped conversations.

The LLM is a presentation layer over verified evidence only. It cannot call tools directly and cannot mutate trading, risk, research, portfolio or deployment state.

See `AI_PROVIDER.md`, `AI_PROMPTS.md`, `AI_CHAT.md`, `AI_CITATIONS.md`, `AI_STREAMING.md`, `AI_TOKEN_BUDGET.md`, `AI_AGENT_CONSOLE.md` and `AI_CONVERSATIONS.md`.

## Phase 10

Provider production hardening adds real usage capture, cost estimates, budgets, provider health, secret abstraction, circuit breaker, and staged streaming. The Research Agent adds policy-bounded research-only planning, approvals, reports and operations UI.
## Phase 11 Broker Boundary

AI remains read-only for broker state. It may explain broker health, paper account snapshots, order lifecycle, execution evidence and reconciliation differences, but it must not import broker order adapters or submit/cancel orders.
# Phase 12 AI Boundary

AI may explain and recommend allocation changes from evidence, but it cannot apply allocations, activate strategies, approve risk, or mutate broker/portfolio state.
