#!/bin/sh
set -eu

echo 'Waiting for n8n database connection...'
for attempt in $(seq 1 30); do
  if n8n list:workflow >/dev/null 2>&1; then break; fi
  sleep 2
done
if [ ! -f /data/bootstrap.done ]; then
  n8n import:workflow --separate --input=/workflows
  touch /data/bootstrap.done
else
  echo 'Workflow import already completed; refusing duplicates.'
fi
echo 'n8n bootstrap complete.'
