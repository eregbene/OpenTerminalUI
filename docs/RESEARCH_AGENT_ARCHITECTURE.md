# Research Agent Architecture

The Research Agent lives in `backend/research_agent`.

It contains:

- policies
- plans
- task graph validation
- approvals
- budget checks
- scheduler facade
- executor facade
- hypotheses
- analysis
- recommendations
- reports
- owner-scoped memory
- audit

It orchestrates existing research workflows and does not implement authoritative strategy, backtest, validation, risk or trading calculations.
