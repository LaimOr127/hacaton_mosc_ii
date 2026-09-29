# Construction inspection MVP

Local n8n-first inspection of project, working, and as-built PDF documents. Upload three PDFs, run the inspection, review differences with page citations, and export a JSON report. The as-built demo PDF has no text layer, so the automated scenario exercises OCR.

## Run locally

Requirements: Docker Compose, approximately 6 GB of free disk space, and port 80 free on `127.0.0.1`.

```sh
make secrets       # once; creates ignored .env with random passwords
make verify        # build, start, unit tests, full API/OCR/persistence E2E
```

Open [the interface](http://localhost/) after `make verify`. Use `make health` to inspect containers and `make down` to stop without deleting PostgreSQL, Redis, object storage, or n8n volumes. Never use `down -v` for ordinary operation.

To run only the demo again: `make e2e`. It creates a new project, uploads the three committed synthetic PDFs, checks the expected findings and evidence, reviews one finding, and verifies persistence after restarting n8n. It does not delete previous demo projects.

## Run on a shared server

Keep this project in its own directory and use its own Compose project name and volumes. Set these values in the server's ignored `.env`:

```dotenv
APP_BASE_URL=http://localhost:8088
PUBLIC_API_BASE_URL=http://localhost:8088/api/v1
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8088
N8N_HOST=localhost
N8N_EDITOR_PORT=15678
```

Run `docker compose up -d --build && docker compose restart n8n-main` from that directory. The restart loads workflows imported during bootstrap. Connect from your computer with `ssh -L 8088:127.0.0.1:8088 -L 15678:127.0.0.1:15678 root@SERVER_IP`, then open `http://localhost:8088/` for the app or `http://localhost:15678/` for n8n. The application processes data on the server; the tunnel carries browser traffic. PostgreSQL and Redis stay on the Compose internal network.

## Architecture and scope

Nginx exposes the frontend and n8n webhook API at `http://localhost/api/v1`. n8n coordinates the workflow; PostgreSQL owns application state; Redis is the n8n queue; SeaweedFS stores originals through its S3-compatible API; isolated Python services handle storage signatures, PDF extraction, OCR, and deterministic comparison. Migrations and n8n workflows are built into their one-shot bootstrap images for reliable Docker Desktop startup. The [master specification](CODEX_N8N_CONSTRUCTION_INSPECTION_MASTER_PROMPT.md) documents the intended broader product.

This is a **single-user, loopback-only demo**. There is no end-user authentication or authorization; keep its published port on `127.0.0.1`. PDF/page limits and internal service tokens are configured in `.env`. The deterministic rule engine handles the included door, window, and wall examples; it is not a general-purpose drawing-understanding model. Advanced retries, multi-user permissions, LLM reasoning, and broader document formats remain out of scope. Local secrets in `.env`, user PDFs, volumes, and runtime files are ignored by Git.
