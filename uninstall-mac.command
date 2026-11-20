#!/bin/bash
# 한성대 공간 예약 MCP 제거 (macOS). Finder에서 더블클릭하세요.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"

BIN="$(uv tool dir --bin 2>/dev/null)/hsu-reserve-mcp"
if [ -x "$BIN" ]; then
  "$BIN" uninstall
  uv tool uninstall hsu-reserve
  echo "제거 완료."
else
  echo "설치되어 있지 않습니다."
fi
echo
read -r -p "엔터를 누르면 창이 닫힙니다..."
