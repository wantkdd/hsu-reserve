#!/bin/bash
# 한성대 공간 예약 MCP 설치 (macOS). Finder에서 더블클릭하세요.
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "설치 중 오류가 발생했습니다. 위 메시지를 확인해 주세요."; read -r -p "엔터를 누르면 창이 닫힙니다..."' ERR

echo "=== 한성대 공간 예약 MCP 설치 ==="
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "[1/3] uv(파이썬 실행 도구) 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
else
  echo "[1/3] uv 확인됨"
fi

echo "[2/3] 프로그램 설치 중..."
uv tool install --force --reinstall --quiet .

echo "[3/3] 설정"
"$(uv tool dir --bin)/hsu-reserve-mcp" setup

echo
read -r -p "엔터를 누르면 창이 닫힙니다..."
