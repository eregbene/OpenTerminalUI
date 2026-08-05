# Deployment Migration

## Current Deployment

Existing deployments can continue using the same repository folder, Compose project name, service names, and persistent volumes. Legacy environment variables and volumes are retained.

## Fresh Bensim Deployment

Fresh deployments may use:

```powershell
$env:COMPOSE_PROJECT_NAME="bensim"
docker compose up -d --build
```

The image is now `bensim-trading:latest`.

## Repository Folder Rename

Renaming the local folder changes Docker's default project name unless `COMPOSE_PROJECT_NAME` is set. Set `COMPOSE_PROJECT_NAME=openterminalui` before a folder rename if you want to keep existing container names and volumes attached.

## Volume Preservation

Persistent volumes are intentionally still named:

- `openterminalui_data`
- `openterminalui_postgres_data`

Do not rename or delete these volumes automatically. To migrate volume names later, stop Compose, copy the volume contents with a temporary container, verify the new deployment, then keep the old volumes until rollback is no longer needed.

## Rollback

Rollback is:

1. Stop the new Compose project.
2. Restore the previous `COMPOSE_PROJECT_NAME`.
3. Start the old stack against the original volumes.

