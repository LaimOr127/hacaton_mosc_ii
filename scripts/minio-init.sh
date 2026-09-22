#!/bin/sh
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
for bucket in "$MINIO_BUCKET_DOCUMENTS" "$MINIO_BUCKET_DERIVED" "$MINIO_BUCKET_EXPORTS"; do
  mc mb --ignore-existing "local/$bucket"
  mc anonymous set none "local/$bucket"
done
mc ilm rule add --expire-days 7 "local/$MINIO_BUCKET_DERIVED" || true
