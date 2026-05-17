#!/bin/bash
# Create a snapshot of the running smc-engine-runner-01 VPS.
# Run locally; reads token from .env.
set -euo pipefail

SERVER_NAME="smc-engine-runner-01"
DESCRIPTION="${1:-smc-engine clean snapshot $(date -u +%Y-%m-%d)}"

HCLOUD_TOKEN=$(grep '^HCLOUD_API_KEY=' .env | cut -d= -f2- | tr -d '[:space:]\r')
if [[ -z "$HCLOUD_TOKEN" ]]; then echo "ERROR: HCLOUD_API_KEY missing"; exit 2; fi

RESP=$(curl -sS -H "Authorization: Bearer $HCLOUD_TOKEN" \
    "https://api.hetzner.cloud/v1/servers?name=${SERVER_NAME}")
SERVER_ID=$(python3 -c "
import json,sys
d = json.loads(sys.stdin.read())
s = d.get('servers') or []
if not s: print('NOTFOUND'); sys.exit(2)
print(s[0]['id'])" <<< "$RESP")

if [[ "$SERVER_ID" == "NOTFOUND" ]]; then
    echo "ERROR: server ${SERVER_NAME} not found"; exit 2
fi

PAYLOAD=$(python3 -c "
import json
print(json.dumps({'type': 'snapshot', 'description': '''${DESCRIPTION}'''}))")

echo "Creating snapshot for server id=${SERVER_ID}: ${DESCRIPTION}"
SNAP_RESP=$(curl -sS -X POST -H "Authorization: Bearer $HCLOUD_TOKEN" -H "Content-Type: application/json" \
    "https://api.hetzner.cloud/v1/servers/${SERVER_ID}/actions/create_image" \
    -d "$PAYLOAD")
python3 -c "
import json,sys
r = json.loads(sys.stdin.read())
if r.get('error'):
    print('ERROR:', r['error']); sys.exit(2)
img = r.get('image') or {}
act = r.get('action') or {}
print(f\"SNAPSHOT_ID={img.get('id')}\")
print(f\"ACTION_STATUS={act.get('status')}\")
print(f\"ACTION_ID={act.get('id')}\")" <<< "$SNAP_RESP"
