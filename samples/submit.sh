#!/usr/bin/env bash
# Usage: samples/submit.sh [index]   (needs the stack running and jq installed)
# Registers samples/repos/demo-app, sets the project's validation command and submits task <index> from tasks.json.
set -euo pipefail
API="${API:-http://localhost:8000/api}"
here="$(cd "$(dirname "$0")" && pwd)"
env_value() { grep "^$1=" "$here/../.env" 2>/dev/null | cut -d= -f2-; }
USER_NAME="${ADMIN_USERNAME:-$(env_value ADMIN_USERNAME)}"; PASS="${ADMIN_PASSWORD:-$(env_value ADMIN_PASSWORD)}"
tok=$(jq -n --arg u "$USER_NAME" --arg p "$PASS" '{username: $u, password: $p}' \
  | curl -s "$API/auth/login" -H 'content-type: application/json' -d @- | jq -r .token)
auth=(-H "Authorization: Bearer $tok" -H 'content-type: application/json')
repo=$(curl -s "$API/repos" "${auth[@]}" -d '{"path":"/repos/demo-app"}' | jq -r .id)
# Set the demo's test command on the first project, keeping its name and goal.
curl -s "$API/projects" "${auth[@]}" \
  | jq -c '.[0] | {id, body: (del(.id) + {validation_commands: [{kind: "test", command: "python -m unittest discover -s tests"}]})}' \
  | { read -r p; curl -s -X PUT "$API/projects/$(jq .id <<<"$p")" "${auth[@]}" -d "$(jq .body <<<"$p")" >/dev/null; }
jq ".[${1:-0}] | del(.name) + {repo_ids:[$repo]}" "$here/tasks.json" | curl -s "$API/tasks" "${auth[@]}" -d @- | jq '.[] | {id, title, status}'
