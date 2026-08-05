# AI Research Assistant

Phase 9 introduces a guarded research-brief contract for Bensim Trading.

## Scope

The assistant is decision support only. It may summarize, compare, explain, retrieve evidence, and suggest next research actions.

It must not:

- submit orders
- approve risk
- modify paper accounts
- promote strategies
- change strategy logic
- bypass deterministic services

## Endpoint

`POST /api/ai/research-brief`

Request:

```json
{
  "symbol": "RELIANCE",
  "horizon": "swing",
  "question": "Explain the setup",
  "context": {
    "quote": { "last_price": 2500, "change_pct": 1.2 },
    "market_structure": { "trend": "bullish", "status": "validated" },
    "risk": { "risk_score": 0.25 },
    "strategy": { "status": "passed", "sharpe": 1.4 }
  }
}
```

Response:

- `summary`: short deterministic summary
- `decision_support`: posture, confidence, next actions, and out-of-scope actions
- `sections`: human-readable evidence sections
- `evidence`: evidence IDs, sources, summaries, and source payloads
- `guardrails`: execution authority and human-review requirements

## Evidence Model

The current implementation only uses caller-supplied structured context. Supported evidence keys:

- `quote`
- `market_structure`
- `risk`
- `strategy`

The service does not call an LLM and does not invent missing evidence.

## Frontend Client

`frontend/src/api/ai.ts` exposes `createAIResearchBrief`.

No user-facing workflow was changed in Phase 9.
