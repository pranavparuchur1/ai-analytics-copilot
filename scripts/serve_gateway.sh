#!/usr/bin/env bash
# (Re)start only the inference-gateway with the copilot config, once a worker is up.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "${IGW_DIR:-$HOME/inference-gateway}"
for _ in $(seq 1 180); do
  curl -sf localhost:8011/health > /dev/null || curl -sf localhost:8012/health > /dev/null && break
  sleep 2
done
pkill -f "[i]gw serve" || true
sleep 1
IGW_CONFIG="$here/infrastructure/gateway/copilot.toml" \
  nohup uv run igw serve --host 0.0.0.0 --port 8000 > logs/gateway-copilot.log 2>&1 &
for _ in $(seq 1 60); do curl -sf localhost:8000/health > /dev/null && break; sleep 1; done
curl -sf localhost:8000/health && echo
