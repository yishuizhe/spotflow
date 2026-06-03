#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/binance-testnet-agent}"
BACKUP_DIR="${BACKUP_DIR:-/opt/binance-testnet-agent/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_DIR="$(mktemp -d)"

mkdir -p "$BACKUP_DIR"
mkdir -p "$TMP_DIR/binance-testnet-agent"

cp -a "$APP_DIR"/README.md "$TMP_DIR/binance-testnet-agent/" 2>/dev/null || true
cp -a "$APP_DIR"/pyproject.toml "$TMP_DIR/binance-testnet-agent/" 2>/dev/null || true
cp -a "$APP_DIR"/.env "$TMP_DIR/binance-testnet-agent/env.redacted" 2>/dev/null || true
sed -i -E 's/^(BINANCE_API_KEY|BINANCE_API_SECRET)=.*/\\1=REDACTED/' "$TMP_DIR/binance-testnet-agent/env.redacted" 2>/dev/null || true
cp -a "$APP_DIR"/data "$TMP_DIR/binance-testnet-agent/" 2>/dev/null || true
journalctl -u binance-testnet-agent -n 1000 --no-pager > "$TMP_DIR/binance-testnet-agent/agent-journal.log" 2>/dev/null || true
journalctl -u binance-testnet-dashboard -n 300 --no-pager > "$TMP_DIR/binance-testnet-agent/dashboard-journal.log" 2>/dev/null || true

tar -czf "$BACKUP_DIR/binance-testnet-agent-$STAMP.tar.gz" -C "$TMP_DIR" binance-testnet-agent
find "$BACKUP_DIR" -name 'binance-testnet-agent-*.tar.gz' -mtime +14 -delete
rm -rf "$TMP_DIR"
