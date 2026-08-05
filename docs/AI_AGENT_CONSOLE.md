# AI Agent Console

The frontend Agent Console now uses grounded `/api/ai/chat/stream` for general assistant prompts.

Existing legacy agent modes remain intact:

- debate
- strategy lab
- screener

The console displays answer text, model/provider metadata, evidence bundle id, citations, token usage, grounding warnings and latency. Research Lab and canonical paper controls include contextual AI explain buttons that open the console with the relevant entity.

Phase 10 adds a dedicated Research Agent operations page for policies, plans, budgets, approvals, lineage and reports.
