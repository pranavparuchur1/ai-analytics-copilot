#!/usr/bin/env bash
# Start one SQL model worker plus the inference-gateway in front of it (run inside WSL).
# Needs a checkout of github.com/pranavparuchur1/inference-gateway at $IGW_DIR.
#
# usage: bash scripts/serve_llm.sh [7b|3b]     (default 7b)
#   then from the copilot: LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=coder (or coder-3b)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
IGW_DIR="${IGW_DIR:-$HOME/inference-gateway}"
size="${1:-7b}"
case "$size" in
  7b) model=Qwen/Qwen2.5-Coder-7B-Instruct-AWQ; port=8011; kv=$((768 * 1048576)) ;;
  3b) model=Qwen/Qwen2.5-Coder-3B-Instruct-AWQ; port=8012; kv=$((1024 * 1048576)) ;;
  *) echo "usage: $0 [7b|3b]"; exit 1 ;;
esac

cd "$IGW_DIR"
mkdir -p logs
pkill -f "[v]llm serve" || true
pkill -f "[i]gw serve" || true
sleep 3

echo "starting $model on :$port"
nohup bash scripts/serve_vllm.sh "$model" "$port" --kv-cache-memory "$kv" \
  --gpu-memory-utilization 0.2 --max-num-seqs 8 --max-model-len 6144 \
  > "logs/vllm-copilot-$size.log" 2>&1 &
pid=$!
for _ in $(seq 1 240); do
  curl -sf "localhost:$port/health" > /dev/null && break
  if ! kill -0 "$pid" 2> /dev/null; then
    echo "worker FAILED:"; tail -20 "logs/vllm-copilot-$size.log"; exit 1
  fi
  sleep 2
done
curl -sf "localhost:$port/health" > /dev/null || { echo "worker timed out"; exit 1; }
echo "worker ready: $(grep -o 'GPU KV cache size: [0-9,]* tokens' "logs/vllm-copilot-$size.log" | tail -1)"

IGW_CONFIG="$here/infrastructure/gateway/copilot.toml" \
  nohup uv run igw serve --host 0.0.0.0 --port 8000 > logs/gateway-copilot.log 2>&1 &
for _ in $(seq 1 60); do curl -sf localhost:8000/health > /dev/null && break; sleep 1; done
curl -sf localhost:8000/health && echo
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
