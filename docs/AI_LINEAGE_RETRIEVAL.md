# AI Lineage Retrieval

Lineage retrieval is implemented by `backend/ai_assistant/lineage.py`.

The service traces known deterministic relationships:

- candidate -> scorecard
- candidate -> deployment
- deployment -> risk evaluation
- deployment -> paper order
- paper order -> paper fill
- paper fill -> position
- position/order -> stored reconciliation

Lineage output contains `nodes`, `edges`, `missing_links`, and `warnings`. Cycles or duplicate nodes are guarded by a seen-node set and reported as warnings.

Lineage is evidence only. It does not approve candidates, approve deployments, submit orders, reconcile accounts, or mutate trading state.
