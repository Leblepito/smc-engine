#!/bin/bash
# Create the Hetzner VPS via API (run from local PowerShell/Bash with HCLOUD_API_KEY in .env).
#
# Idempotent: detects an existing 'smc-engine-runner-01' and prints its IP instead.
#
# Pre-requisites:
#   - .env contains HCLOUD_API_KEY=<token>
#   - ~/.ssh/id_ed25519.pub exists
#   - python3 in PATH (used to build JSON payload and parse responses; avoids jq)
#
# Usage:
#   bash deploy/hetzner/create_vps.sh
#   # → prints VPS_ID + IP. Save IP for the SSH deploy step.

set -euo pipefail

SERVER_NAME="smc-engine-runner-01"
SERVER_TYPE="${SERVER_TYPE:-cpx11}"     # override: SERVER_TYPE=cpx22 bash ...
LOCATION="${LOCATION:-fsn1}"            # override: LOCATION=hel1 bash ...
IMAGE="ubuntu-24.04"

if [[ ! -f .env ]]; then
    echo "ERROR: .env missing"; exit 2
fi

HCLOUD_TOKEN=$(grep '^HCLOUD_API_KEY=' .env | cut -d= -f2- | tr -d '[:space:]\r')
if [[ -z "$HCLOUD_TOKEN" ]]; then
    echo "ERROR: HCLOUD_API_KEY missing in .env"; exit 2
fi

# Idempotency: server with this name already exists?
EXISTING=$(curl -sS -H "Authorization: Bearer $HCLOUD_TOKEN" \
    "https://api.hetzner.cloud/v1/servers?name=${SERVER_NAME}")
EXISTING_IP=$(python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
s = d.get('servers') or []
print(s[0]['public_net']['ipv4']['ip'] if s else '')" <<< "$EXISTING")
if [[ -n "$EXISTING_IP" ]]; then
    EXISTING_ID=$(python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
print(d['servers'][0]['id'])" <<< "$EXISTING")
    echo "EXISTS: ${SERVER_NAME} (id=${EXISTING_ID}, ip=${EXISTING_IP}) — skipping create"
    echo "VPS_IP=${EXISTING_IP}"
    echo "VPS_ID=${EXISTING_ID}"
    exit 0
fi

# Find existing SSH key by local pub key fingerprint match
PUB_KEY=$(cat ~/.ssh/id_ed25519.pub)
LOCAL_FP=$(ssh-keygen -lE md5 -f ~/.ssh/id_ed25519.pub | awk '{print $2}' | sed 's/^MD5://')

KEYS=$(curl -sS -H "Authorization: Bearer $HCLOUD_TOKEN" https://api.hetzner.cloud/v1/ssh_keys)
SSH_KEY_ID=$(python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
for k in d.get('ssh_keys', []):
    if k['fingerprint'] == '${LOCAL_FP}':
        print(k['id']); break" <<< "$KEYS")

if [[ -z "$SSH_KEY_ID" ]]; then
    echo "Uploading local SSH pub key to Hetzner..."
    KEY_PAYLOAD=$(python3 -c "
import json
print(json.dumps({'name': 'smc-engine-deploy', 'public_key': '''${PUB_KEY}'''}))")
    KEY_RESP=$(curl -sS -X POST -H "Authorization: Bearer $HCLOUD_TOKEN" -H "Content-Type: application/json" \
        https://api.hetzner.cloud/v1/ssh_keys \
        -d "$KEY_PAYLOAD")
    SSH_KEY_ID=$(python3 -c "
import json, sys
print(json.loads(sys.stdin.read())['ssh_key']['id'])" <<< "$KEY_RESP")
fi
echo "Using SSH key id=${SSH_KEY_ID}"

# Build cloud-init with live pub key
CLOUD_INIT=$(sed "s|REPLACE_WITH_YOUR_SSH_PUBLIC_KEY|${PUB_KEY}|" \
    deploy/hetzner/cloud-init.yaml)

PAYLOAD=$(python3 -c "
import json,sys
ud = sys.stdin.read()
print(json.dumps({
    'name': '${SERVER_NAME}',
    'server_type': '${SERVER_TYPE}',
    'image': '${IMAGE}',
    'location': '${LOCATION}',
    'ssh_keys': [${SSH_KEY_ID}],
    'user_data': ud,
    'labels': {'project': 'smc-engine', 'env': 'prod'},
    'start_after_create': True,
}))" <<< "$CLOUD_INIT")

echo "Creating ${SERVER_NAME} (${SERVER_TYPE} @ ${LOCATION})..."
RESP=$(curl -sS -X POST -H "Authorization: Bearer $HCLOUD_TOKEN" -H "Content-Type: application/json" \
    https://api.hetzner.cloud/v1/servers -d "$PAYLOAD")

python3 -c "
import json, sys
r = json.loads(sys.stdin.read())
if r.get('error'):
    print('ERROR:', r['error']); sys.exit(2)
s = r['server']
print(f\"VPS_ID={s['id']}\")
print(f\"VPS_IP={s['public_net']['ipv4']['ip']}\")
print(f\"STATUS={s['status']}\")
print(f\"LOCATION={s['datacenter']['location']['name']}\")" <<< "$RESP"
