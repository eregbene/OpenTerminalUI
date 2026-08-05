# AI Evidence Source Map

Bensim Trading AI assistant evidence is read-only and deterministic.

| Entity | Tool | Source | Persistence | Notes |
| --- | --- | --- | --- | --- |
| market-structure snapshot | `get_market_structure_snapshot` | `market_structure._SNAPSHOTS` | process-local | Available only while the backend process retains the snapshot. |
| strategy decision | `get_strategy_decision` | strategy registry | process-local | Static registered strategy specification and hash. |
| research run | `get_research_run` | `data/research/research_store.json` | file-backed | Reads research registry backtest records. |
| scorecard | `get_scorecard` | `data/research/research_store.json` | file-backed | Reads promotion gate scorecards. |
| candidate | `get_candidate` | `data/research/research_store.json` | file-backed | Reads research candidate metadata. |
| deployment | `get_deployment` | `data/trading/trading_store.json` | file-backed | Reads deployment state only. |
| risk evaluation | `get_risk_evaluation` | `data/trading/trading_store.json` | file-backed | Reads deterministic risk output. |
| paper order | `get_order` | `data/trading/trading_store.json` | file-backed | Reads order state only. |
| paper fills | `get_fills` | `data/trading/trading_store.json` | file-backed | Reads fills by fill id or order id. |
| position | `get_position` | portfolio snapshot rebuild | file-backed | Rebuilds a read-only snapshot from account ledger. |
| account snapshot | `get_account_snapshot` | portfolio snapshot rebuild | file-backed | No account mutation. |
| reconciliation | `get_reconciliation` | `data/trading/trading_store.json` | file-backed | Reads stored reconciliation records; does not run reconciliation. |

No AI assistant tool has direct database-session, shell, broker, filesystem-mutation, or unrestricted HTTP access.
