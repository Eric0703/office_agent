#!/usr/bin/env bash
# Playwright 隔离测试服务:独立端口(见 test-config.yaml)+ 临时数据目录
# (frontend/.e2e-runtime,每次运行重建)。不触碰仓库根 config.yaml、
# data/agent.db 与已在运行的 8000 服务(Playwright webServer 启动/终止本进程)。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$HERE/../.e2e-runtime"
AGENT_HOST="$HERE/../../backend/.venv/bin/agent-host"

rm -rf "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR"
cp "$HERE/test-config.yaml" "$RUNTIME_DIR/config.yaml"
cd "$RUNTIME_DIR"
"$AGENT_HOST" mock import
exec "$AGENT_HOST" serve
