# Assumptions

- This directory was specification-only at Phase 0; no colleague implementation existed to preserve.
- The initial deliverable is a single-user/demo P0 boundary. `ENABLE_AUTH=false` is explicit and is not production authentication.
- Docker Desktop was available for the final build, tests, and E2E on 2026-09-23.
- The MVP keeps PDFs in MinIO and passes only keys/metadata through n8n.
- LLM, embeddings, advanced OCR, and multi-worker scaling remain disabled until deterministic P0 passes.
