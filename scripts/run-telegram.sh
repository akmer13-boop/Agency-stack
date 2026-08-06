#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "Виртуальное окружение не найдено. Сначала запустите:"
  echo "  bash scripts/setup-local.sh"
  exit 1
fi

exec caffeinate -dimsu ./.venv/bin/python -m app.telegram.bot
