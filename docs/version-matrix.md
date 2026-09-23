# Version matrix

| Component | Pin | Evidence |
| --- | --- | --- |
| n8n main, worker, bootstrap | `n8nio/n8n:2.40.5` | Upgraded after reviewing upstream security advisories; import, publish, and full E2E passed locally on 2026-09-23. |
| PostgreSQL | `postgres:17.4-alpine` | Migrations and E2E persistence verified locally on 2026-09-23. |
| Redis | `redis:7.4.2-alpine` | n8n queue verified by full E2E on 2026-09-23. |
| MinIO | `quay.io/minio/minio:RELEASE.2025-02-18T16-25-55Z` | Pulled from official Quay registry on 2026-09-22. |
| Nginx | `nginx:1.27.4-alpine` | Public API, frontend, and signed MinIO URLs verified locally on 2026-09-23. |

All n8n processes use the same pinned image. Workflows use built-in Webhook, PostgreSQL, HTTP Request, and Respond nodes; no Code node is required.
