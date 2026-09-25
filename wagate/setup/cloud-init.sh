#!/bin/bash
# Установка wagate на чистый Ubuntu 22.04/24.04 (Oracle Cloud Free Tier и любой VPS).
# Вставляется при создании сервера в поле «Cloud-init script» / «User data» — выполнится сам при первом запуске.
# Секретов внутри нет: токен шлюза генерируется на сервере и показывается только владельцу (setup/pair.sh).
set -euxo pipefail
exec > /var/log/wagate-setup.log 2>&1
export DEBIAN_FRONTEND=noninteractive

curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs git caddy iptables-persistent

# В образах Ubuntu у Oracle входящие порты закрыты iptables: открываем 80 и 443 для HTTPS.
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
netfilter-persistent save

git clone --depth 1 -b claude/sdr-service-automation-opruv0 https://github.com/velorievvork-pixel/SaaS.git /opt/saas
ln -sfn /opt/saas/wagate /opt/wagate
cd /opt/wagate
npm ci --omit=dev

TOKEN=$(node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))")
cat > .env <<ENV
WAGATE_ID=1101000001
WAGATE_TOKEN=$TOKEN
HOST=127.0.0.1
PORT=3000
DATA_DIR=data
ENV
chmod 600 .env
id wagate >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin wagate
chown -R wagate /opt/saas/wagate

cp wagate.service /etc/systemd/system/wagate.service
systemctl daemon-reload
systemctl enable --now wagate

# Бесплатный адрес с HTTPS без покупки домена: 1.2.3.4 → 1-2-3-4.sslip.io, сертификат выпустит Caddy.
IP=$(curl -fsS https://api.ipify.org)
NAME="$(echo "$IP" | tr . -).sslip.io"
printf '%s {\n  reverse_proxy 127.0.0.1:3000\n}\n' "$NAME" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
echo "$NAME" > /opt/wagate/HOSTNAME
echo "wagate: готово, https://$NAME"
