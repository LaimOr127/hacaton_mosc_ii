# Construction inspection MVP

An n8n-first MVP for comparing project, working, and as-built PDF documentation. PostgreSQL owns application state; Redis is only the n8n queue; MinIO stores original files; PDF/OCR services are isolated HTTP workers.

## Current phase

Foundation is implemented: pinned Compose topology, isolated networks, persistent volumes, role-separated PostgreSQL databases, checksum-guarded migrations, MinIO bucket initialization, and an importable n8n health workflow. The PDF/OCR services, product API workflows, deterministic inspection pipeline, frontend, and E2E demo are in progress.

## Local setup

```sh
make secrets
make build
make up
make health
```

`make down` preserves volumes. Do not use `down -v` for normal operation.

## Boundaries

- The public endpoint is Nginx on `http://localhost`; PostgreSQL, Redis, MinIO, and thin services stay on the internal Docker network.
- This is explicitly a single-user/demo boundary until authentication is implemented. See `docs/assumptions.md`.
- Do not commit `.env`, PDFs, volumes, backups, or generated runtime files.
