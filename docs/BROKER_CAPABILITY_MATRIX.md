# Broker Capability Matrix

| Capability | Internal Simulator | IBKR Paper | Future IBKR Live |
| --- | --- | --- | --- |
| Account summary | Internal paper account | Supported | Disabled |
| Portfolio | Internal ledger | Supported read-only sync | Disabled |
| Market data | Demo/internal | Delayed/realtime mode labeled | Disabled |
| Historical data | Existing providers | Supported through normalized broker bars | Disabled |
| Contract lookup | Internal instruments | Stocks, ETFs, FX, data-only indices | Disabled |
| Market orders | Simulator | Paper only | Disabled |
| Limit orders | Simulator | Paper only | Disabled |
| Stop/stop-limit | Simulator | Paper only | Disabled |
| DAY/GTC | Simulator | Paper only | Disabled |
| Brackets/trailing/algos | Existing model only | Unsupported, explicit failure | Disabled |
| Currency conversion | No automatic conversion | Unsupported, explicit failure | Disabled |

Unsupported capabilities must fail before any broker call.
