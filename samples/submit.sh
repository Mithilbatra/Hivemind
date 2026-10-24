#!/usr/bin/env bash
# Usage: samples/submit.sh [index]   (needs the stack running and jq installed)
# Registers samples/repos/demo-app, sets the project's validation command and submits task <index> from tasks.json.
set -euo pipefail
API="${API:-http://localhost:8000/api}"
USER_NAME="${ADMIN_USERNAME:-admin}"; PASS="${ADMIN_PASSWORD:-change-me}"
here="$(cd "$(dirname "$0")" && pwd)"
tok=$(curl -s "$API/auth/login" -H 'content-type: application/json' -d "{\"username\":\"$USER_NAME\",\"password\":\"$PASS\"}" | jq -r .token)
auth=(-H "Authorization: Bearer $tok" -H 'content-type: application/json')
repo=$(curl -s "$API/repos" "${auth[@]}" -d '{"path":"/repos/demo-app"}' | jq -r .id)
curl -s -X PUT "$API/projects/1" "${auth[@]}" -d '{"name":"default","max_retries":2,"validation_commands":[{"kind":"test","command":"python -m unittest discover -s tests"}]}' >/dev/null
jq ".[${1:-0}] | del(.name) + {repo_ids:[$repo]}" "$here/tasks.json" | curl -s "$API/tasks" "${auth[@]}" -d @- | jq '.[] | {id, title, status}'
