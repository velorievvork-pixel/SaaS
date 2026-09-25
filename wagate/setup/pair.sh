#!/bin/bash
# Привязка рабочего номера и настройки для агента.
#   sudo /opt/wagate/setup/pair.sh 77011234567
set -eu
cd "${WAGATE_DIR:-/opt/wagate}"
set -a; . ./.env; set +a
API="http://127.0.0.1:$PORT/waInstance$WAGATE_ID"
PHONE=$(echo "${1:-}" | tr -dc 0-9)
if [ ${#PHONE} -lt 10 ]; then
  echo "Укажите рабочий номер: sudo /opt/wagate/setup/pair.sh 77011234567"; exit 1
fi

state() { curl -fsS "$API/getStateInstance/$WAGATE_TOKEN" 2>/dev/null | tr -dc 'a-zA-Z:{}"' | sed 's/.*stateInstance":"\([a-zA-Z]*\).*/\1/'; }
echo "Жду запуска шлюза..."
for _ in $(seq 1 60); do S=$(state || true); [ "$S" = notAuthorized ] || [ "$S" = authorized ] && break; sleep 2; done

if [ "${S:-}" = authorized ]; then
  echo "Номер уже привязан."
else
  CODE=$(curl -fsS -X POST "$API/getAuthorizationCode/$WAGATE_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"phoneNumber\": $PHONE}" | sed 's/.*"code":"\([^"]*\)".*/\1/')
  echo
  echo "  КОД ПРИВЯЗКИ:  $CODE"
  echo
  echo "На телефоне: WhatsApp → Настройки → Связанные устройства → Привязка устройства →"
  echo "«Связать по номеру телефона» → введите код (действует пару минут)."
  echo "Жду привязки..."
  for _ in $(seq 1 90); do [ "$(state || true)" = authorized ] && { echo "Готово: номер привязан."; break; }; sleep 2; done
fi

echo
echo "Добавьте в настройки облачной среды Claude (меню среды → Edit → переменные окружения)."
echo "В чат их не отправляйте."
echo "  GREEN_API_URL=https://$(cat HOSTNAME 2>/dev/null || echo '<адрес>')"
echo "  GREEN_API_ID=$WAGATE_ID"
echo "  GREEN_API_TOKEN=$WAGATE_TOKEN"
echo "И в Network access разрешите адрес: $(cat HOSTNAME 2>/dev/null || echo '<адрес>')"
