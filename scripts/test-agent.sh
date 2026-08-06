#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo ".env не найден. Сначала запустите ./scripts/setup-local.sh"
  exit 1
fi

read_env_value() {
  local name="$1"
  python3 - "$name" <<'PY'
from pathlib import Path
import sys

name = sys.argv[1]
value = ""
for raw_line in Path(".env").read_text(encoding="utf-8-sig").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, current = line.split("=", 1)
    if key.strip() == name:
        value = current.strip()
print(value)
PY
}

AGENT_TOKEN="$(read_env_value AGENT_API_TOKEN)"
OPENAI_KEY="$(read_env_value OPENAI_API_KEY)"

if [[ -z "$AGENT_TOKEN" ]]; then
  echo "AGENT_API_TOKEN пустой в .env"
  exit 1
fi

if [[ -z "$OPENAI_KEY" ]]; then
  echo "OPENAI_API_KEY пустой в .env"
  exit 1
fi

if ! curl -fsS http://127.0.0.1:8000/health >/dev/null; then
  echo "Agency Stack не отвечает на http://127.0.0.1:8000"
  echo "Запустите сервер в другом окне Terminal."
  exit 1
fi

MESSAGE="${*:-Представься и расскажи, что ты сейчас умеешь в системе Agency Stack}"
PAYLOAD="$(python3 - "$MESSAGE" <<'PY'
import json
import sys
print(json.dumps({"message": sys.argv[1]}, ensure_ascii=False))
PY
)"

RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

HTTP_STATUS="$(curl -sS \
  -o "$RESPONSE_FILE" \
  -w '%{http_code}' \
  -X POST http://127.0.0.1:8000/api/v1/agent-runs \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data "$PAYLOAD")"

python3 - "$RESPONSE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
raw = path.read_text(encoding="utf-8")
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print(raw)
else:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
PY

if [[ "$HTTP_STATUS" -lt 200 || "$HTTP_STATUS" -ge 300 ]]; then
  echo "HTTP status: $HTTP_STATUS" >&2
  exit 1
fi
