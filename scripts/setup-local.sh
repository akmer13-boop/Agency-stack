#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Agency Stack: настройка macOS ==="

if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="python3.12"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Python 3 не найден. Установите Python 3.12 через Homebrew:"
  echo "  brew install python@3.12"
  exit 1
fi

PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
$PYTHON_BIN -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "Нужен Python 3.12 или новее. Найдена версия: $PYTHON_VERSION"
  echo "Установите: brew install python@3.12"
  exit 1
}

echo "Python: $PYTHON_BIN ($PYTHON_VERSION)"

if [[ ! -d .venv ]]; then
  echo "Создаю виртуальное окружение .venv..."
  "$PYTHON_BIN" -m venv .venv
fi

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

echo "Обновляю pip..."
"$VENV_PYTHON" -m pip install --upgrade pip

echo "Устанавливаю проект и dev-зависимости..."
"$VENV_PYTHON" -m pip install -e '.[dev]'

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Создан .env из шаблона. Заполните OPENAI_API_KEY и AGENT_API_TOKEN."
else
  echo ".env уже существует — не изменяю его."
fi

echo
echo "Проверка внешнего IP:"
if command -v curl >/dev/null 2>&1; then
  COUNTRY="$(curl -fsS https://ipinfo.io/country 2>/dev/null | tr -d '\r\n' || true)"
  PUBLIC_IP="$(curl -fsS https://api.ipify.org 2>/dev/null || true)"
  echo "  IP: ${PUBLIC_IP:-не определён}"
  echo "  Страна: ${COUNTRY:-не определена}"
else
  echo "  curl не найден — проверка пропущена."
fi

echo
echo "=== НАСТРОЙКА ЗАВЕРШЕНА ==="
echo "Рабочая ветка: $(git branch --show-current)"
echo
echo "Следующие команды:"
echo "  nano .env"
echo "  ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
echo
echo "После запуска откройте: http://127.0.0.1:8000/docs"
