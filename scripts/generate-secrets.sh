#!/bin/sh
set -eu

[ -e .env ] && { echo '.env already exists; refusing to overwrite' >&2; exit 1; }
random() { openssl rand -hex 32; }
sed \
  -e "s/change_me_postgres/$(random)/" \
  -e "s/change_me_n8n/$(random)/" \
  -e "s/change_me_app/$(random)/" \
  -e "s/change_me_readonly/$(random)/" \
  -e "s/change_me_redis/$(random)/" \
  -e "s/change_me_minio_admin/minioadmin/" \
  -e "s/change_me_minio_password/$(random)/" \
  -e "s/replace_with_a_32_plus_character_random_value/$(random)/" \
  -e "s/replace_with_a_random_internal_token/$(random)/" \
  .env.example > .env
chmod 600 .env
echo 'Created .env with local secrets.'
