# datasette-publish-dokploy

Datasette plugin for publishing data to self-hosted Dokploy.

## Installation

Install this plugin in the same environment as Datasette:

```bash
datasette install datasette-publish-dokploy
```

## Usage

Generate deployable files (recommended):

```bash
datasette publish dokploy my-database.db --generate-dir app
```

This writes `Dockerfile`, `index.py`, `requirements.txt`, and database/static files into `app/`.

Generate a GitHub Actions workflow for Dokploy:

```bash
datasette publish dokploy my-database.db --generate-github-actions > .github/workflows/deploy-datasette.yml
```

The generated workflow builds and pushes a container image to GHCR, then triggers Dokploy deployment.

## Direct deploy from local machine

You can also build/push and trigger deploy directly:

```bash
datasette publish dokploy my-database.db \
  --image ghcr.io/OWNER/REPO:latest \
  --dokploy-url https://dokploy.example.com \
  --application-id YOUR_APPLICATION_ID \
  --api-key YOUR_DOKPLOY_API_KEY
```

Or with a Dokploy webhook URL:

```bash
datasette publish dokploy my-database.db \
  --image ghcr.io/OWNER/REPO:latest \
  --deploy-url https://dokploy.example.com/api/.../deploy
```

Optional options:
- `--token` adds `Authorization: Bearer <token>` to webhook requests
- `--setting name value` passes Datasette settings
- `--crossdb` enables cross-database queries

## GitHub Actions secrets

For generated workflow, set:
- `DOKPLOY_AUTH_TOKEN`
- `DOKPLOY_APPLICATION_ID`
- `DOKPLOY_URL`

If you use a custom workflow + webhook, set your webhook secret as needed.
