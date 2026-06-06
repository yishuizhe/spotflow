#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-161.33.129.33}"
USER_NAME="${USER_NAME:-ubuntu}"
KEY_PATH="${KEY_PATH:-.secrets/binance_agent_server.pem}"
REMOTE_DIR="${REMOTE_DIR:-/opt/binance-testnet-agent}"

if [[ ! -f "$KEY_PATH" ]]; then
  echo "Missing SSH key: $KEY_PATH" >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Copy .env.example to .env and fill the Binance Testnet keys first." >&2
  exit 1
fi

TMP_ARCHIVE="$(mktemp -t binance-agent.XXXXXX.tar.gz)"
tar \
  --exclude='.secrets' \
  --exclude='run_state' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -czf "$TMP_ARCHIVE" \
  pyproject.toml README.md CHANGELOG.md .env binance_testnet_agent tests deploy

scp -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "$TMP_ARCHIVE" "$USER_NAME@$HOST:/tmp/binance-testnet-agent.tar.gz"

ssh -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "$USER_NAME@$HOST" <<'REMOTE'
set -euo pipefail
sudo mkdir -p /opt/binance-testnet-agent
sudo tar -xzf /tmp/binance-testnet-agent.tar.gz -C /opt/binance-testnet-agent
sudo mkdir -p /opt/binance-testnet-agent/data
sudo chown -R ubuntu:ubuntu /opt/binance-testnet-agent
sudo chmod 600 /opt/binance-testnet-agent/.env
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-agent.service /etc/systemd/system/binance-testnet-agent.service
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-dashboard.service /etc/systemd/system/binance-testnet-dashboard.service
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-backup.service /etc/systemd/system/binance-testnet-backup.service
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-backup.timer /etc/systemd/system/binance-testnet-backup.timer
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-report.service /etc/systemd/system/binance-testnet-report.service
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-report.timer /etc/systemd/system/binance-testnet-report.timer
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-weekly-report.service /etc/systemd/system/binance-testnet-weekly-report.service
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-weekly-report.timer /etc/systemd/system/binance-testnet-weekly-report.timer
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-monthly-report.service /etc/systemd/system/binance-testnet-monthly-report.service
sudo cp /opt/binance-testnet-agent/deploy/binance-testnet-monthly-report.timer /etc/systemd/system/binance-testnet-monthly-report.timer
sudo chmod +x /opt/binance-testnet-agent/deploy/backup_agent.sh
sudo systemctl daemon-reload
sudo systemctl enable binance-testnet-agent
sudo systemctl enable binance-testnet-dashboard
sudo systemctl enable --now binance-testnet-backup.timer
sudo systemctl enable --now binance-testnet-report.timer
sudo systemctl disable --now binance-testnet-weekly-report.timer || true
sudo systemctl disable --now binance-testnet-monthly-report.timer || true
sudo systemctl restart binance-testnet-agent
sudo systemctl restart binance-testnet-dashboard
sudo systemctl --no-pager --full status binance-testnet-agent
sudo systemctl --no-pager --full status binance-testnet-dashboard
sudo systemctl --no-pager --full status binance-testnet-backup.timer
sudo systemctl --no-pager --full status binance-testnet-report.timer
sudo systemctl --no-pager --full is-enabled binance-testnet-weekly-report.timer || true
sudo systemctl --no-pager --full is-enabled binance-testnet-monthly-report.timer || true
REMOTE

rm -f "$TMP_ARCHIVE"
