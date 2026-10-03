# Containers

| Piece | Image | Defined in | Why |
|---|---|---|---|
| Warehouse | `postgres:17-alpine` | `docker-compose.yml` (`warehouse`) | First start runs `infrastructure/postgres/*.sql/.sh`: schema, then the read-only `copilot_ro` role. |
| Copilot API + UI | built from the root `Dockerfile` | `docker-compose.yml` (`copilot`, profile `app`) | Non-root user, `uv sync --frozen`, health check on `/api/health`. |
| LLM | not containerised | `scripts/serve_llm.sh` | vLLM needs the GPU, which on this machine is only reachable from WSL2. The container calls the inference-gateway via `host.docker.internal:8000`. |

```bash
docker compose up -d --wait warehouse          # database only (local development)
docker compose --profile app up -d --build     # database + web app
```

The warehouse init scripts only run when the data volume is empty. After changing them:
`docker compose down -v` (deletes the data) and seed again with `make seed`.
