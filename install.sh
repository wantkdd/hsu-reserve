#!/bin/sh
# hsu-reserve installer (macOS / Linux):
#   curl -LsSf https://raw.githubusercontent.com/wantkdd/hsu-reserve/master/install.sh | sh
set -eu

REPO="https://github.com/wantkdd/hsu-reserve/archive/refs/heads/master.zip"
export PATH="$HOME/.local/bin:$PATH"

echo "=== 한성대 공간 예약 (hsu-reserve) 설치 ==="

if ! command -v uv >/dev/null 2>&1; then
  echo "[1/3] uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  export PATH="$HOME/.local/bin:$PATH"
else
  echo "[1/3] uv 확인됨"
fi

echo "[2/3] 프로그램 설치 중..."
uv tool install --refresh --force --quiet "$REPO"

echo "[3/3] 설정"
# stdin is the curl pipe; read answers from the terminal instead.
"$(uv tool dir --bin)/hsu-reserve-mcp" setup </dev/tty
