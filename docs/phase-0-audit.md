# Phase 0 audit

## Evidence

- The project initially contained only `CODEX_N8N_CONSTRUCTION_INSPECTION_MASTER_PROMPT.md` plus tool runtime directories.
- No Git repository, source code, Compose files, migrations, workflows, tests, or application configuration existed.
- Host check: macOS arm64; Docker CLI 28.4.0 and Compose 2.34.0 are installed; the Docker daemon was unavailable during audit.

## Result

There is no existing application to preserve or refactor. The implementation is created in this directory as one project, beginning with a minimal P0 foundation. Runtime state directories are ignored and are not product artifacts.

## Plan

1. Foundation: pinned Compose services, private networks, volumes, roles, migrations, MinIO buckets, safe env generation.
2. Thin services: real PDF text extraction and CPU OCR with typed contracts.
3. n8n: importable API/pipeline workflows and bootstrap verification on the actual pinned image.
4. Vertical P0: direct upload, persistence, deterministic extraction/matching/findings/evidence/review.
5. Frontend, demo fixtures, E2E, restart/failure checks, documentation and security review.
