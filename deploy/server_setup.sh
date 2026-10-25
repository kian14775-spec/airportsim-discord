#!/usr/bin/env bash
# One-time setup on the Oracle VM (Ubuntu). Run from the uploaded folder:  sudo bash deploy/server_setup.sh
set -euo pipefail
apt-get update -y
apt-get install -y python3 python3-venv rsync unattended-upgrades
id asbot >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin asbot
mkdir -p /opt/asbot
rsync -a --exclude .venv --exclude logs --exclude state ./ /opt/asbot/
python3 -m venv /opt/asbot/.venv
/opt/asbot/.venv/bin/pip install -q -r /opt/asbot/requirements.txt
chown -R asbot:asbot /opt/asbot
chmod 600 /opt/asbot/.env
cp /opt/asbot/deploy/asbot.service /etc/systemd/system/asbot.service
systemctl daemon-reload
systemctl enable --now asbot
systemctl --no-pager status asbot | head -5
