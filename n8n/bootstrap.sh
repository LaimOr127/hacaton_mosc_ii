#!/bin/sh
set -eu

credential_file=$(mktemp)
trap 'rm -f "$credential_file"' EXIT HUP INT TERM

node -e '
const fs = require("fs");
const e = process.env;
const credentials = [
  {
    id: "appPostgresCred0001",
    name: "Application PostgreSQL",
    type: "postgres",
    data: {
      host: e.APP_DB_HOST,
      port: Number(e.APP_DB_PORT),
      database: e.APP_DB_NAME,
      user: e.APP_DB_USER,
      password: e.APP_DB_PASSWORD,
      ssl: "disable",
    },
  },
  {
    id: "internalBearerCred01",
    name: "Internal Bearer",
    type: "httpHeaderAuth",
    data: { name: "Authorization", value: `Bearer ${e.INTERNAL_API_TOKEN}` },
  },
  {
    id: "internalOcrCred001",
    name: "Internal OCR",
    type: "httpHeaderAuth",
    data: { name: "X-Internal-Token", value: e.INTERNAL_API_TOKEN },
  },
];
for (const key of ["APP_DB_HOST", "APP_DB_NAME", "APP_DB_USER", "APP_DB_PASSWORD", "INTERNAL_API_TOKEN"]) {
  if (!e[key]) throw new Error(`${key} is required`);
}
fs.writeFileSync(process.argv[1], JSON.stringify(credentials), { mode: 0o600 });
' "$credential_file"

workflow_ids=$(node -e '
const fs = require("fs");
for (const name of fs.readdirSync("/workflows").filter((name) => name.endsWith(".json"))) {
  const workflow = JSON.parse(fs.readFileSync(`/workflows/${name}`, "utf8"));
  if (!workflow.id) throw new Error(`${name}: stable workflow id is required`);
  for (const node of workflow.nodes) {
    if (node.type === "n8n-nodes-base.webhook" && !node.webhookId) {
      throw new Error(`${name}: stable webhookId is required for ${node.name}`);
    }
  }
  if (workflow.active) process.stdout.write(`${workflow.id}\n`);
}
')

n8n import:credentials --input="$credential_file"
n8n import:workflow --separate --input=/workflows

for workflow_id in $workflow_ids; do
  n8n publish:workflow --id="$workflow_id"
done
echo 'n8n bootstrap complete.'
