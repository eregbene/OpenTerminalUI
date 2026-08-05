# Research Agent Safety

The Research Agent cannot:

- import broker clients
- submit orders
- create paper fills
- approve risk
- promote candidates
- activate deployments
- mutate positions
- change balances
- invoke shell commands
- access unrestricted HTTP

Prompt-injection defense treats evidence and imported text as untrusted data, not instructions.
## Phase 11 Broker Isolation

The Research Agent remains research-only. It may read authorized paper-performance evidence but must not import broker mutation services, OMS mutation services, submission services or cancellation services.
# Phase 12 Research Agent Boundary

The Research Agent remains research-only and cannot mutate Phase 12 portfolios, allocations, strategies, risk limits, reports, replay state, or broker state.
