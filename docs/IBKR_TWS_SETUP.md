# IBKR TWS Setup

Use TWS paper mode. Enable API access and use the paper API port, commonly `7497`.

The backend must verify:

- account identifier is allow-listed,
- account is paper-like (`DU...`),
- expected environment is `PAPER`,
- live trading remains disabled.
