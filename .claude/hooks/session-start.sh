#!/bin/bash
# Ставит зависимости, чтобы линтер, тесты и шлюзы работали сразу при старте сессии.
#
# Почему это нужно именно здесь. Контейнер поднимается с чистым питоном, а в
# репозитории два шлюза, которые решают, уйдёт ли письмо живому человеку.
# Если pyyaml не установлен, lead_gate.py падает и лид уходит в рассылку
# непроверенным. Отдельная засада: pytest и ruff могут лежать в ~/.local/bin
# на ДРУГОМ интерпретаторе, чем python3, и тогда они не видят пакетов проекта.
# Поэтому всё ставится в python3 и запускается через python3 -m.
set -euo pipefail

# Только удалённые сессии: на машине разработчика своё окружение не трогаем.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

PIP_FLAGS=(--quiet --disable-pip-version-check)
# Debian помечает системный питон как externally-managed; в одноразовом
# контейнере ставить в него безопасно и это ожидаемый путь.
if python3 -m pip install --help 2>/dev/null | grep -q -- --break-system-packages; then
  PIP_FLAGS+=(--break-system-packages)
fi

python3 -m pip install "${PIP_FLAGS[@]}" -r requirements.txt

# Запуск инструментов через модуль, а не через бинарник из PATH: бинарник
# может оказаться на другом интерпретаторе и не увидеть pyyaml.
{
  echo 'export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}'"$PWD"'/camirix"'
  echo 'alias lint="python3 -m ruff check camirix tests .claude/hooks"'
  echo 'alias test="python3 -m pytest tests -q"'
} >> "${CLAUDE_ENV_FILE:-/dev/null}"

# Проверяем то, без чего шлюзы не работают. Молча установить и не убедиться —
# ровно тот класс ошибки, против которого эти шлюзы и написаны.
python3 - <<'PY'
import sys
missing = []
for mod in ("yaml", "requests", "pytest", "ruff"):
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print(f"session-start: не установились: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
print("session-start: окружение готово, шлюзы запустятся")
PY
