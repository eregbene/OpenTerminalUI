# IBKR Docker Connectivity

When TWS or Gateway runs on the host, Docker backend should use:

```text
IBKR_HOST=host.docker.internal
IBKR_PORT=7497
```

The Docker compose file already includes `host.docker.internal:host-gateway`.
