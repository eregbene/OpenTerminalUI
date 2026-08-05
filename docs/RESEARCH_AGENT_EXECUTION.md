# Research Agent Execution

Execution is bounded and research-only.

Allowed:

- evidence collection
- dataset validation
- baseline backtest orchestration
- validation orchestration
- robustness review
- report generation

Forbidden:

- order creation
- broker calls
- risk approval
- candidate promotion
- deployment activation
- account or portfolio mutation

Each execution checks policy and kill switches immediately before launch.
