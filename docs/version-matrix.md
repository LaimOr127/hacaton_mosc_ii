# Version matrix

| Component | Pin | Evidence |
| --- | --- | --- |
| n8n main, worker, bootstrap | `n8nio/n8n:2.6.4` | Pulled and CLI verified locally on 2026-09-22; `import:workflow --separate`, `list:workflow`, and `publish:workflow` are present. |
| PostgreSQL | `postgres:17.4-alpine` | Pinned Compose declaration; runtime verification pending. |
| Redis | `redis:7.4.2-alpine` | Pinned Compose declaration; runtime verification pending. |
| MinIO | `quay.io/minio/minio:RELEASE.2025-02-18T16-25-55Z` | Pulled from official Quay registry on 2026-09-22. |
| Nginx | `nginx:1.27.4-alpine` | Pinned Compose declaration; runtime verification pending. |

All n8n processes use the same pinned image. n8n task runners are intentionally not enabled in this first foundation slice: the current health workflow uses only built-in Webhook, Set, and Respond nodes. They will be configured before any Code node is introduced.
