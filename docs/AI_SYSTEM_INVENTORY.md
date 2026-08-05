# AI System Inventory

Phase 9B audit of current AI-related modules.

| Area | Path | Classification | Notes |
|---|---|---|---|
| AI Research Assistant | `backend/services/ai_research_assistant.py`, `POST /api/ai/research-brief` | active, deterministic, guarded | Produces evidence-linked research briefs from caller-supplied structured context. No LLM calls and no execution authority. |
| Natural language query route | `backend/api/routes/ai.py`, `backend/services/ai_service.py` | active, prototype, provider-specific, partially state-free, ungrounded | Classifies user text through LM Studio/OpenAI/Ollama and may run screeners or quote lookups. Evidence bundles and answer citations are absent. |
| Agent SSE API | `backend/api/routes/agent.py` | active, prototype, direct LLM output, read-only intent | Creates in-process pending runs and streams model/tool events. No durable conversation or evidence store. |
| Agent orchestration | `backend/agent/orchestrator.py` | active, tool-using, partially grounded | Uses registered tools and feeds results back to the model. Tool authorization is not a separate canonical layer. |
| Tool registry | `backend/agent/tools/registry.py` | active, read-only metadata present | Tool specs include `read_only` and `write_class`, but no full authorization/audit policy. |
| Market tools | `backend/agent/tools/market_tools.py` | active, deterministic wrappers | Uses market/screener services. Result truncation exists informally through prompt/tool limits. |
| Debate mode | `backend/agent/debate/*` | prototype, direct LLM output | Produces analyst/bull/bear/PM style outputs. Requires grounding isolation before decision support. |
| Strategy loop mode | `backend/agent/strategy_loop/*` | prototype, research-facing | Bounded strategy-analysis loop. Must remain draft/research-only. |
| Screener agent | `backend/agent/screener/*` | prototype, read-only | Natural-language screener flow; needs evidence and claim controls. |
| LLM provider abstraction | `backend/services/llm/*` | active, provider-specific abstraction | Supports OpenRouter/OpenAI/LM Studio/Gemini style endpoints. Routing exists but not tied to evidence or safety policy. |
| LLM insights | `backend/services/llm_insights.py` | active/prototype | Used for generated insight text; requires evidence linkage review before Phase 9 reuse. |
| Research autopilot | `backend/research_autopilot/*` | active/prototype, deterministic plus generated labels | Produces research verdicts/signals/backtests. Needs claim and fallback audit. |
| Frontend Agent Console | `frontend/src/agent/*` | active UI | Shows streamed agent messages and artifacts. Does not expose evidence drawer, scoped context audit, provider health, or unsupported-claim warnings. |
| Frontend AI query client | `frontend/src/api/ai.ts` | active thin client | Calls `/api/ai/query`; no evidence handling. |

## Suitability

Safe for adaptation:

- guarded research brief contract in `backend/services/ai_research_assistant.py`
- provider wrapper shape in `backend/services/llm`
- agent event streaming primitives
- read-only tool registry concept
- frontend Agent Console shell

Requires isolation before Phase 9 assistant use:

- direct model answers without evidence IDs
- in-process conversation/run storage
- tool execution without canonical authorization records
- generated bullish/bearish/debate framing
- natural-language screener claims without source citations
