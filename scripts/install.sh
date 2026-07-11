#!/usr/bin/env bash
set -euo pipefail

BASE=/var/www/ebook-translator
LOG=/var/log/ebook-translator

mkdir -p "$BASE" "$BASE/jobs" "$BASE/fonts" "$LOG"
tar --exclude='./venv' --exclude='./.env' --exclude='./providers.yaml' -C /root/ebook-translator -cf - . | tar -C "$BASE" -xf -
if [ ! -f "$BASE/.env" ]; then
  cp "$BASE/.env.example" "$BASE/.env"
fi
if [ ! -f "$BASE/providers.yaml" ]; then
  cp "$BASE/providers.yaml.example" "$BASE/providers.yaml"
fi
python3.12 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -r "$BASE/requirements.txt"
cp /root/fonts/Vazirmatn-*.ttf "$BASE/fonts/" 2>/dev/null || true
cp "$BASE/systemd/"*.service /etc/systemd/system/
systemctl daemon-reload
echo "Installed. Fill $BASE/.env and $BASE/providers.yaml, then enable services."
