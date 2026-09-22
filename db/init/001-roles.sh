#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname postgres \
  --set=n8n_db="$N8N_DB_NAME" \
  --set=n8n_user="$N8N_DB_USER" \
  --set=n8n_password="$N8N_DB_PASSWORD" \
  --set=app_db="$APP_DB_NAME" \
  --set=app_user="$APP_DB_USER" \
  --set=app_password="$APP_DB_PASSWORD" \
  --set=readonly_user="$APP_DB_READONLY_USER" \
  --set=readonly_password="$APP_DB_READONLY_PASSWORD" <<'SQL'
CREATE USER :"n8n_user" WITH PASSWORD :'n8n_password';
CREATE DATABASE :"n8n_db" OWNER :"n8n_user";
CREATE USER :"app_user" WITH PASSWORD :'app_password';
CREATE USER :"readonly_user" WITH PASSWORD :'readonly_password';
CREATE DATABASE :"app_db" OWNER :"app_user";
SQL
