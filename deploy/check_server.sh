#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-161.33.129.33}"
USER_NAME="${USER_NAME:-ubuntu}"
KEY_PATH="${KEY_PATH:-.secrets/binance_agent_server.pem}"

ssh -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "$USER_NAME@$HOST" \
  'systemctl --no-pager --full status binance-testnet-agent; echo; systemctl --no-pager --full status binance-testnet-dashboard; echo; systemctl --no-pager --full status binance-testnet-backup.timer; echo; systemctl --no-pager --full status binance-testnet-report.timer; echo; systemctl --no-pager --full status binance-testnet-weekly-report.timer; echo; systemctl --no-pager --full status binance-testnet-monthly-report.timer; echo; journalctl -u binance-testnet-agent -n 80 --no-pager'
