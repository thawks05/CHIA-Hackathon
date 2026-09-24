#!/usr/bin/env bash
# Run from the Linux head. This script creates no VMs and installs nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
backend="${1:-model}"
output="${2:-artifacts/head-$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ "$backend" != model && "$backend" != chia ]]; then
  printf 'Usage: bash scripts/run_head.sh [model|chia] [output-directory]\n' >&2
  exit 2
fi
if [[ -n "${PIVOT_CONDA_ENV:-}" ]]; then
  if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
  fi
  conda activate "$PIVOT_CONDA_ENV"
fi
if [[ "$backend" == chia ]]; then
  if [[ "${PIVOT_NETWORK_MODE:-userspace}" == kernel ]]; then
    : "${RAY_ADDRESS:?Set RAY_ADDRESS to the head Tailscale IP and port 6379}"
    unset grpc_proxy no_grpc_proxy RAY_grpc_enable_http_proxy
  else
    export RAY_ADDRESS="${RAY_ADDRESS:-127.200.0.1:6379}"
    export RAY_grpc_enable_http_proxy=1
    export grpc_proxy="${grpc_proxy:-http://127.0.0.1:13129}"
    export no_grpc_proxy="${no_grpc_proxy:-127.200.0.1,127.0.0.1,localhost}"
  fi
fi
python -m workflows.quickstart \
  --backend "$backend" --output "$output" \
  --budget "${PIVOT_BUDGET:-20}" \
  --timeout-seconds "${PIVOT_TIMEOUT_SECONDS:-1800}" \
  --random-cases "${PIVOT_RANDOM_CASES:-1000}" \
  --rtl-backend "${PIVOT_RTL_BACKEND:-auto}"
