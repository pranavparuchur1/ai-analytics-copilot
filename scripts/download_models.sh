#!/usr/bin/env bash
# Download the SQL-generation models into the Hugging Face cache (run inside WSL,
# from the inference-gateway checkout so its environment has huggingface_hub).
set -euo pipefail
cd ~/inference-gateway
for m in Qwen/Qwen2.5-Coder-7B-Instruct-AWQ Qwen/Qwen2.5-Coder-3B-Instruct-AWQ; do
  echo "== $m"
  uv run python -c "from huggingface_hub import snapshot_download as d; print(d('$m'))"
done
