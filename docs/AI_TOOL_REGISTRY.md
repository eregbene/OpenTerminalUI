# AI Tool Registry

The AI tool registry is read-only and lives in `backend/ai_assistant/tool_registry.py`.

## Registered Tools

- `market_structure`
- `strategy_decision`
- `research_run`
- `scorecard`
- `candidate`
- `deployment`
- `risk_evaluation`
- `paper_order`
- `paper_fill`
- `position`
- `account_snapshot`
- `reconciliation`

## Required Metadata

Every tool exposes:

- schema
- authorization
- timeout
- max results
- read-only flag

## Authorization

Before a tool is read, `AssistantAuthorizationService.authorize_tool` verifies:

- tool is read-only
- tool authorization is read-only
- requested intent is allowed
- prohibited execution capabilities are declared denied

No AI tool may directly access database sessions, filesystem mutation, shell commands, broker services or unrestricted HTTP.

## Phase 9C Tool Names

The canonical tools are:

- `get_market_structure_snapshot`
- `get_strategy_decision`
- `get_research_run`
- `get_scorecard`
- `get_candidate`
- `get_deployment`
- `get_risk_evaluation`
- `get_order`
- `get_fills`
- `get_position`
- `get_account_snapshot`
- `get_reconciliation`
## Security Boundary

Every tool is read-only, authorized before execution, and accessed only through the assistant service. Tools do not expose shell, broker, unrestricted HTTP, filesystem mutation or database-session access.

## Phase 9D Provider Boundary

The LLM provider layer receives assembled evidence bundles after tool execution. Providers do not select or execute tools directly.
