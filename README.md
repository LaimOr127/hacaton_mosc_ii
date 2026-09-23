# Construction inspection MVP

Local n8n-first inspection of project, working, and as-built PDF documents. Upload three PDFs, run the inspection, review differences with page citations, and export a JSON report. The as-built demo PDF has no text layer, so the automated scenario exercises OCR.

## Run locally

Requirements: Docker Compose, approximately 6 GB of free disk space, and port 80 free on `127.0.0.1`.

```sh
make secrets       # once; creates ignored .env with random passwords
make verify        # build, start, unit tests, full API/OCR/persistence E2E
```

Open [the interface](http://localhost/) after `make verify`. Use `make health` to inspect containers and `make down` to stop without deleting PostgreSQL, Redis, MinIO, or n8n volumes. Never use `down -v` for ordinary operation.

To run only the demo again: `make e2e`. It creates a new project, uploads the three committed synthetic PDFs, checks the expected findings and evidence, reviews one finding, and verifies persistence after restarting n8n. It does not delete previous demo projects.

## Architecture and scope

Nginx exposes the frontend and public n8n webhook API at `http://localhost/api/v1`. n8n coordinates the workflow; PostgreSQL owns application state; Redis is the n8n queue; MinIO stores originals; isolated Python services handle storage signatures, PDF extraction, OCR, and deterministic comparison. Migrations and n8n workflows are built into their one-shot bootstrap images for reliable Docker Desktop startup. The [master specification](CODEX_N8N_CONSTRUCTION_INSPECTION_MASTER_PROMPT.md) documents the intended broader product.

This is a **single-user, loopback-only demo**, not a production deployment. There is no end-user authentication or authorization; do not expose port 80 through a public reverse proxy. PDF/page limits and internal service tokens are configured in `.env`. The deterministic rule engine handles the included door, window, and wall examples; it is not a general-purpose drawing-understanding model. Advanced retries, multi-user permissions, LLM reasoning, and broader document formats remain out of scope. Local secrets in `.env`, user PDFs, volumes, and runtime files are ignored by Git.
